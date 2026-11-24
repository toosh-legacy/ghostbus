"""Live evaluation with real OpenAI models (`make eval-live`). Needs OPENAI_API_KEY; the cost guard stops the run
at the session limit ($3 by default).

Compares the fake chat model with the live models on the same suites:
gold SQL questions, unanswerable refusals, red team (prompts + SQL + MCP attacks), methodology RAG (cited answers),
an LLM-as-judge faithfulness check (strong tier) on every answered gold question, and the retrieval ablation with
real embeddings and the LLM reranker. Writes evals/reports/live.md.
"""

from __future__ import annotations

import json
import sys
import time

from gbcopilot import llm
from gbcopilot.agent import answer
from pydantic import BaseModel

from .gold import OUT, generate, score_item
from .rag_gold import GOLD as RAG_GOLD
from .run import REPORTS


class Verdict(BaseModel):
    faithful: bool
    answers_question: bool
    rationale: str


def judge(q: str, a: dict) -> Verdict:
    t = a.get("table") or {"columns": [], "rows": []}
    rows = t["rows"][:50]
    return llm.structured("judge", "judge", Verdict, inputs={"question": q, "rows": rows, "answer": a["answer"]},
                          question=q, columns=", ".join(t["columns"]), rows=json.dumps(rows, default=str),
                          answer=a["answer"])


def evaluate(provider: str) -> dict:
    from .redteam import run as redteam

    gold = json.loads(OUT.read_text()) if OUT.exists() else generate()
    t0 = time.time()
    spent0 = llm.Ledger().spent()
    answered, ok, misses = [], 0, []
    for it in gold["answerable"]:
        a = answer(it["question"], provider=provider)
        good = score_item(it, a)
        ok += good
        if not good:
            misses.append({"q": it["question"], "answer": a["answer"][:160], "sql": a.get("sql") or a.get("rejected_sql")})
        if not a["refused"]:
            answered.append((it["question"], a))
    refusals = [answer(q, provider=provider)["refused"] for q in gold["unanswerable"]]
    rag_ok = 0
    for q, g in RAG_GOLD:
        a = answer(q, provider=provider)
        rag_ok += (not a["refused"]) and any(c["id"] in g for c in a.get("citations", []))
    rt = redteam(provider)
    tok = llm.MODE.set("live" if provider == "live" else "fake")
    try:
        verdicts = [judge(q, a) for q, a in answered]
    finally:
        llm.MODE.reset(tok)
    return {"provider": provider, "gold_accuracy": ok / len(gold["answerable"]), "n_gold": len(gold["answerable"]),
            "refusal_rate": sum(refusals) / len(refusals), "n_unanswerable": len(refusals),
            "rag_cited": rag_ok / len(RAG_GOLD), "n_rag": len(RAG_GOLD),
            "redteam_failures": rt["failures"], "redteam_n": rt["n_prompts"] + rt["n_sql_payloads"] + rt["n_mcp_payloads"],
            "db_unchanged": rt["db_unchanged"],
            "judge_faithful": sum(v.faithful for v in verdicts) / max(1, len(verdicts)),
            "judge_answers": sum(v.answers_question for v in verdicts) / max(1, len(verdicts)),
            "seconds": round(time.time() - t0, 1), "usd": round(llm.Ledger().spent() - spent0, 4), "misses": misses}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not llm.MODE.get() and not (llm._api_key()):
        print("OPENAI_API_KEY is not set; eval-live needs it.")
        return 2
    tok = llm.MODE.set("live")
    try:
        models = llm.verify_models()
    finally:
        llm.MODE.reset(tok)
    rows = [evaluate("fake"), evaluate("live")]
    from . import retrieval_ablation

    tok = llm.MODE.set("live")
    try:
        abl = retrieval_ablation.run(include_llm=True)
    finally:
        llm.MODE.reset(tok)
    c = llm.config()
    L = ["# Live copilot evaluation", "",
         f"Models (checked with models.list): cheap `{c['tiers']['cheap']['model']}` (routing, SQL, rerank), strong "
         f"`{c['tiers']['strong']['model']}` (answers, judge); embeddings `{c['embeddings']['model']}`. "
         f"Available: {', '.join(models)}. Temperature 0, seed 7. Session spend so far: ${llm.Ledger().spent():.4f} "
         f"of ${llm.Ledger().limit:.2f}.", "",
         "| Model | Gold SQL accuracy | Refusals | Methodology answers cited | Red-team failures | Judge: faithful | "
         "Judge: answers question | Time | Cost |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        name = "fake (deterministic)" if r["provider"] == "fake" else "OpenAI (live)"
        L.append(f"| {name} | {100 * r['gold_accuracy']:.1f}% ({r['n_gold']}) | {100 * r['refusal_rate']:.0f}% "
                 f"({r['n_unanswerable']}) | {100 * r['rag_cited']:.0f}% ({r['n_rag']}) | {r['redteam_failures']} / "
                 f"{r['redteam_n']} | {100 * r['judge_faithful']:.0f}% | {100 * r['judge_answers']:.0f}% | "
                 f"{r['seconds']} s | ${r['usd']:.4f} |")
    L += ["", "Judge = strong-tier model grading each answered gold question against its result table.", "",
          "## Retrieval ablation (live embeddings + LLM reranker)", "",
          "| Pipeline | Recall@1 | Recall@3 | MRR@10 | nDCG@5 | ms / query |", "|---|---|---|---|---|---|"]
    L += [f"| {x['pipeline']} | {x['recall@1']:.3f} | {x['recall@3']:.3f} | {x['mrr@10']:.3f} | {x['ndcg@5']:.3f} | "
          f"{x['ms_per_query']:.0f} |" for x in abl["rows"]]
    live = rows[1]
    if live["misses"]:
        L += ["", "## Live misses", ""] + [f"- {m['q']} → {m['answer']}" for m in live["misses"]]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "live.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    retrieval_ablation.write(abl)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
