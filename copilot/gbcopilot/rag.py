"""Methodology RAG: retrieval over the published definitions, data sources, known gaps and model notes, so the
copilot can answer "how is this measured" questions with citations.

Pipeline: BM25 (rank-bm25) + dense vectors (OpenAI text-embedding-3-small in an InMemoryVectorStore; a hashing
embedding offline) fused with reciprocal rank fusion, then reranked by a cross-encoder (BAAI/bge-reranker-base on
CPU, via sentence-transformers) or by the cheap OpenAI model. `evals/gbevals/retrieval_ablation.py` measures each
stage.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from gbcore.definitions import DATA_SOURCES, GHOST, GHOST_DEF_VERSION, KNOWN_GAPS, METRIC_DEF_VERSION, METRICS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from . import llm

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TQDM_DISABLE", "1")

GHOST_TITLES = {"historical_missed_trip": "Historical missed trip (ghost bus in past data)",
                "high": "Ghost-bus state High (likely ghost bus)", "elevated": "Ghost-bus state Elevated",
                "unknown": "Ghost-bus state Unknown", "low": "Ghost-bus state Low (bus is running)",
                "reassignment_v1_1": "Ghost-bus rule: wrong trip assignment", "prior": "Ghost-bus prior probability"}
TOKEN = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "is", "are", "to", "in", "on", "for", "and", "or", "how", "what", "does", "do",
        "it", "be", "by", "with", "that", "this", "as", "at", "from", "which", "when", "you", "i", "count", "counts"}


def tokens(s: str) -> list[str]:
    return [t for t in TOKEN.findall(s.lower()) if t not in STOP]


@lru_cache
def corpus() -> list[Document]:
    docs = []
    for k, v in METRICS.items():
        docs.append(Document(page_content=v, metadata={"id": f"def:{k}", "version": METRIC_DEF_VERSION}))
    for k, v in GHOST.items():
        docs.append(Document(page_content=f"{GHOST_TITLES.get(k, k)}. {v}",
                             metadata={"id": f"ghost:{k}", "version": GHOST_DEF_VERSION}))
    for i, s in enumerate(DATA_SOURCES):
        docs.append(Document(page_content=f"Data source: {s['name']} ({s['url']}). License: {s['license']}. "
                                          f"{s['attribution']}", metadata={"id": f"source:{i}"}))
    for i, g in enumerate(KNOWN_GAPS):
        docs.append(Document(page_content=f"Known data gap: {g}", metadata={"id": f"gap:{i}"}))
    extra = {
        "thresholds": "Thresholds in use: on time means between 60 seconds early and 360 seconds (6 minutes) late; "
                      "headway tolerance is plus or minus 50% of the scheduled headway; bunching is a headway below "
                      "25% of scheduled; route-days below 70% completeness are unknown.",
        "ghost_versions": "Ghost definition version history: v1.0 is the original rule set; v1.1 adds the "
                          "wrong-trip-assignment rule (High becomes Elevated). Stored ghost evaluations keep the "
                          "version that produced them.",
        "arrival_ranges": "Arrival predictions: the arrival model predicts the 10th, 50th and 90th percentile arrival "
                          "time; conformalized quantile regression calibrates the range so about 80% of buses arrive "
                          "inside it. When no model prediction is available the agency prediction or the schedule "
                          "plus historical delay is shown, labelled with its source.",
        "missed_prior": "The missed-trip probability is a calibrated classifier trained on historical missed trips by "
                        "route, time band and day; it is shown beside the live rule state as a prior, never as the "
                        "rule itself.",
        "event_time": "Live processing uses feed timestamps (event time), never the wall clock, so replaying recorded "
                      "feeds gives the same arrivals and ghost states as the live run.",
        "unknown_policy": "Missing or unhealthy feed data is reported as unknown, never as missed: during a feed "
                          "outage trips are not flagged as ghost buses.",
        "privacy": "GhostBus stores no rider location or identity; coordinates sent for nearby-stop searches are "
                   "never logged.",
    }
    for k, v in extra.items():
        docs.append(Document(page_content=v, metadata={"id": f"note:{k}"}))
    return docs


class HashingEmbeddings(Embeddings):
    """Deterministic local embedding (word + character n-gram hashing). Used when no OpenAI key is set."""

    def __init__(self, dim: int = 1024):
        from sklearn.feature_extraction.text import HashingVectorizer

        self.word = HashingVectorizer(n_features=dim, analyzer="word", ngram_range=(1, 2), alternate_sign=False)
        self.char = HashingVectorizer(n_features=dim, analyzer="char_wb", ngram_range=(3, 5), alternate_sign=False)

    def _emb(self, texts: list[str]) -> list[list[float]]:
        m = np.hstack([self.word.transform(texts).toarray(), 0.5 * self.char.transform(texts).toarray()])
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        return m.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._emb(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._emb([text])[0]


def embeddings() -> Embeddings:
    if llm.live():
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=llm.config()["embeddings"]["model"], api_key=llm._api_key())
    return HashingEmbeddings()


@dataclass
class Hit:
    doc: Document
    score: float

    @property
    def id(self) -> str:
        return self.doc.metadata["id"]


class Retriever:
    def __init__(self, docs: list[Document] | None = None, emb: Embeddings | None = None):
        from langchain_core.vectorstores import InMemoryVectorStore

        self.docs = docs or corpus()
        self.bm25 = BM25Okapi([tokens(d.page_content + " " + d.metadata["id"].replace("_", " ")) for d in self.docs])
        self.emb = emb or embeddings()
        self.store = InMemoryVectorStore(self.emb)
        self.store.add_documents(self.docs, ids=[d.metadata["id"] for d in self.docs])
        if llm.live():  # embedding spend counts toward the session cost guard
            toks = sum(len(d.page_content) for d in self.docs) // 4
            llm.Ledger().charge(toks * llm.config()["embeddings"]["input_per_mtok"] / 1e6,
                                {"model": llm.config()["embeddings"]["model"], "role": "embed_corpus",
                                 "input_tokens": toks, "output_tokens": 0})

    def bm25_search(self, q: str, k: int = 10) -> list[Hit]:
        s = self.bm25.get_scores(tokens(q))
        order = np.argsort(-s)[:k]
        return [Hit(self.docs[i], float(s[i])) for i in order if s[i] > 0]

    def dense_search(self, q: str, k: int = 10) -> list[Hit]:
        return [Hit(d, float(sc)) for d, sc in self.store.similarity_search_with_score(q, k=k)]

    def hybrid_search(self, q: str, k: int = 10, rrf_k: int = 60) -> list[Hit]:
        scores: dict[str, float] = {}
        by_id = {}
        for hits in (self.bm25_search(q, 20), self.dense_search(q, 20)):
            for rank, h in enumerate(hits):
                scores[h.id] = scores.get(h.id, 0.0) + 1.0 / (rrf_k + rank + 1)
                by_id[h.id] = h.doc
        top = sorted(scores, key=lambda i: -scores[i])[:k]
        return [Hit(by_id[i], scores[i]) for i in top]


# --------------------------------------------------------------------------- rerankers
class PassageScore(BaseModel):
    id: str
    score: float = Field(ge=0, le=10)


class RerankScores(BaseModel):
    scores: list[PassageScore]


def _passages(hits: list[Hit]) -> str:
    return "\n".join(f"[{h.id}] {h.doc.page_content}" for h in hits)


def llm_rerank(q: str, hits: list[Hit]) -> list[Hit]:
    """Listwise rerank with the cheap model (structured output: one 0-10 score per passage id)."""
    if not hits:
        return hits
    out = llm.structured("rerank", "rerank", RerankScores, inputs={"question": q, "ids": [h.id for h in hits],
                                                                    "texts": [h.doc.page_content for h in hits]},
                         question=q, passages=_passages(hits))
    sc = {s.id: s.score for s in out.scores}
    return sorted((Hit(h.doc, sc.get(h.id, 0.0)) for h in hits), key=lambda h: -h.score)


@lru_cache
def _cross_encoder():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(os.environ.get("GB_RERANK_MODEL", "BAAI/bge-reranker-base"), device="cpu")


def cross_encoder_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def cross_encoder_rerank(q: str, hits: list[Hit]) -> list[Hit]:
    if not hits:
        return hits
    scores = _cross_encoder().predict([(q, h.doc.page_content) for h in hits])
    return sorted((Hit(h.doc, float(s)) for h, s in zip(hits, scores)), key=lambda h: -h.score)


def default_reranker() -> str:
    """auto: the cheap-LLM reranker when a key is set (best in the ablation), else the local cross-encoder."""
    choice = os.environ.get("GB_RERANKER", "auto")
    if choice == "auto":
        return "llm" if llm.live() else ("cross_encoder" if cross_encoder_available() else "none")
    return choice


@lru_cache
def _retriever(live: bool) -> Retriever:
    return Retriever()


def retriever() -> Retriever:
    """One index per mode: OpenAI embeddings when live, the local hashing embedding otherwise."""
    return _retriever(llm.live())


def retrieve(q: str, k: int = 4, reranker: str | None = None) -> list[Hit]:
    hits = retriever().hybrid_search(q, k=10)
    r = reranker or default_reranker()
    if r == "cross_encoder":
        hits = cross_encoder_rerank(q, hits)
    elif r == "llm":
        hits = llm_rerank(q, hits)
    return hits[:k]


@llm.fake_handler("rerank")
def _fake_rerank(inputs: dict) -> str:
    """Offline rerank: query-term overlap with each passage, scaled to 0-10."""
    q = set(tokens(inputs["question"]))
    out = []
    for i, t in zip(inputs["ids"], inputs["texts"]):
        dt = set(tokens(t + " " + i.replace("_", " ")))
        out.append({"id": i, "score": round(10 * len(q & dt) / max(1, len(q)), 3)})
    return RerankScores(scores=[PassageScore(**o) for o in out]).model_dump_json()
