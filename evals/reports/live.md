# Live copilot evaluation

Models (checked with models.list): cheap `gpt-4o-mini` (routing, SQL, rerank), strong `gpt-4o` (answers, judge); embeddings `text-embedding-3-small`. Available: gpt-4o, gpt-4o-mini, text-embedding-3-small. Temperature 0, seed 7. Session spend so far: $0.1646 of $3.00.

| Model | Gold SQL accuracy | Refusals | Methodology answers cited | Red-team failures | Judge: faithful | Judge: answers question | Time | Cost |
|---|---|---|---|---|---|---|---|---|
| fake (deterministic) | 100.0% (40) | 100% (10) | 84% (32) | 0 / 54 | 100% | 100% | 48.5 s | $0.0000 |
| OpenAI (live) | 97.5% (40) | 90% (10) | 59% (32) | 0 / 54 | 67% | 97% | 310.7 s | $0.1412 |

Judge = strong-tier model grading each answered gold question against its result table.

## Retrieval ablation (live embeddings + LLM reranker)

| Pipeline | Recall@1 | Recall@3 | MRR@10 | nDCG@5 | ms / query |
|---|---|---|---|---|---|
| BM25 | 0.656 | 0.844 | 0.758 | 0.726 | 0 |
| Dense (text-embedding-3-small) | 0.531 | 0.750 | 0.651 | 0.631 | 5 |
| Hybrid (BM25 + dense, RRF) | 0.656 | 0.812 | 0.767 | 0.742 | 5 |
| Hybrid + LLM rerank (gpt-4o-mini) | 0.906 | 0.969 | 0.938 | 0.901 | 1752 |
| Hybrid + cross-encoder rerank (bge-reranker-base, CPU) | 0.781 | 0.938 | 0.858 | 0.821 | 908 |

## Live misses

- Which 5 pilot routes had the lowest missed-trip rate on weekdays in the evening in December 2025? → I can't answer that from this data. (The question asks for data beyond the specified timeframe of 2025-07 to 2025-12.)
