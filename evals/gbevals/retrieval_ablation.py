"""Retrieval ablation for the methodology RAG: each pipeline stage on the same gold set.

Rows: BM25 · dense · hybrid (reciprocal rank fusion) · hybrid + cheap-LLM rerank · hybrid + bge-reranker-base.
Metrics: recall@1, recall@3, MRR@10, nDCG@5 (a hit = any gold passage id). Dense uses OpenAI text-embedding-3-small
in live mode and a local hashing embedding otherwise; the LLM reranker runs only in live mode.

Run: uv run python -m gbevals.retrieval_ablation   (writes evals/reports/retrieval_ablation.md)
"""

from __future__ import annotations

import math
import time

from gbcopilot import llm, rag

from .rag_gold import GOLD
from .run import REPORTS


def metrics(ranked: list[list[str]], gold: list[list[str]]) -> dict:
    r1 = r3 = mrr = ndcg = 0.0
    for ids, rel in zip(ranked, gold):
        hits = [i for i, x in enumerate(ids[:10]) if x in rel]
        r1 += bool(ids[:1] and ids[0] in rel)
        r3 += any(x in rel for x in ids[:3])
        mrr += 1 / (hits[0] + 1) if hits else 0.0
        dcg = sum(1 / math.log2(i + 2) for i, x in enumerate(ids[:5]) if x in rel)
        idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), 5)))
        ndcg += dcg / idcg
    n = len(gold)
    return {"recall@1": r1 / n, "recall@3": r3 / n, "mrr@10": mrr / n, "ndcg@5": ndcg / n}


def run(include_llm: bool | None = None, include_cross: bool | None = None) -> dict:
    ret = rag.retriever()
    qs = [q for q, _ in GOLD]
    gold = [g for _, g in GOLD]
    rows = []

    def row(name: str, fn) -> None:
        t0 = time.perf_counter()
        ranked = [[h.id for h in fn(q)] for q in qs]
        ms = (time.perf_counter() - t0) * 1000 / len(qs)
        rows.append({"pipeline": name, **metrics(ranked, gold), "ms_per_query": ms})

    row("BM25", lambda q: ret.bm25_search(q, 10))
    row("Dense (" + ("text-embedding-3-small" if llm.live() else "local hashing embedding") + ")",
        lambda q: ret.dense_search(q, 10))
    row("Hybrid (BM25 + dense, RRF)", lambda q: ret.hybrid_search(q, 10))
    if include_llm if include_llm is not None else llm.live():
        row(f"Hybrid + LLM rerank ({llm.model_for('rerank')})", lambda q: rag.llm_rerank(q, ret.hybrid_search(q, 10)))
    if include_cross if include_cross is not None else rag.cross_encoder_available():
        rag.cross_encoder_rerank("warm up", ret.hybrid_search("warm up", 2))
        row("Hybrid + cross-encoder rerank (bge-reranker-base, CPU)",
            lambda q: rag.cross_encoder_rerank(q, ret.hybrid_search(q, 10)))
    return {"n_questions": len(qs), "n_passages": len(ret.docs), "mode": "live" if llm.live() else "offline",
            "rows": rows, "default": rag.default_reranker()}


def write(r: dict) -> str:
    L = ["# Methodology retrieval ablation", "",
         f"{r['n_questions']} gold questions over {r['n_passages']} passages · mode: {r['mode']} · "
         f"default reranker: {r['default']}", "",
         "| Pipeline | Recall@1 | Recall@3 | MRR@10 | nDCG@5 | ms / query |", "|---|---|---|---|---|---|"]
    L += [f"| {x['pipeline']} | {x['recall@1']:.3f} | {x['recall@3']:.3f} | {x['mrr@10']:.3f} | {x['ndcg@5']:.3f} | "
          f"{x['ms_per_query']:.1f} |" for x in r["rows"]]
    text = "\n".join(L) + "\n"
    REPORTS.mkdir(parents=True, exist_ok=True)
    name = "retrieval_ablation.md" if r["mode"] == "offline" else "retrieval_ablation_live.md"
    (REPORTS / name).write_text(text, encoding="utf-8")
    return text


def main() -> int:
    print(write(run()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
