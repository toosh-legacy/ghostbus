"""LangSmith datasets and experiments for the copilot (runs only when LANGSMITH_API_KEY is set).

Uploads the gold SQL set and the methodology set as LangSmith datasets (idempotent) and runs an experiment of the
copilot graph against each, scored by the same evaluators as the local suite (exact-table match, refusal, citation).
Traces from every graph run also land in the "ghostbus" project while the key is set.

Run: uv run python -m gbevals.langsmith_experiments [--provider live|fake]
"""

from __future__ import annotations

import argparse
import json
import os


def main() -> int:
    if not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY not set; skipping LangSmith experiments (local evals still cover everything).")
        return 0
    from gbcopilot.agent import answer
    from langsmith import Client, evaluate

    from .gold import OUT, generate, score_item
    from .rag_gold import GOLD

    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="live")
    a = ap.parse_args()
    client = Client()
    gold = json.loads(OUT.read_text()) if OUT.exists() else generate()

    def dataset(name: str, examples: list[dict]) -> str:
        if not client.has_dataset(dataset_name=name):
            ds = client.create_dataset(name)
            client.create_examples(dataset_id=ds.id, inputs=[e["inputs"] for e in examples],
                                   outputs=[e["outputs"] for e in examples])
        return name

    sql_ds = dataset("ghostbus-gold-sql", [{"inputs": {"question": i["question"]}, "outputs": {"item": i}}
                                           for i in gold["answerable"]] +
                     [{"inputs": {"question": q}, "outputs": {"refuse": True}} for q in gold["unanswerable"]])
    rag_ds = dataset("ghostbus-methodology", [{"inputs": {"question": q}, "outputs": {"ids": g}} for q, g in GOLD])

    def target(inputs: dict) -> dict:
        return answer(inputs["question"], provider=a.provider)

    def correct(run, example) -> dict:
        exp = example.outputs or {}
        out = run.outputs or {}
        if exp.get("refuse"):
            return {"key": "correct", "score": int(bool(out.get("refused")))}
        if "ids" in exp:
            return {"key": "correct", "score": int(any(c["id"] in exp["ids"] for c in out.get("citations", [])))}
        return {"key": "correct", "score": int(score_item(exp["item"], out))}

    for ds in (sql_ds, rag_ds):
        evaluate(target, data=ds, evaluators=[correct], experiment_prefix=f"copilot-{a.provider}",
                 metadata={"provider": a.provider})
    print("LangSmith experiments submitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
