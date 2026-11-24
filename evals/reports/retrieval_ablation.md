# Methodology retrieval ablation

32 gold questions over 27 passages · mode: offline · default reranker: cross_encoder

| Pipeline | Recall@1 | Recall@3 | MRR@10 | nDCG@5 | ms / query |
|---|---|---|---|---|---|
| BM25 | 0.656 | 0.844 | 0.758 | 0.726 | 0.2 |
| Dense (local hashing embedding) | 0.531 | 0.750 | 0.651 | 0.631 | 5.1 |
| Hybrid (BM25 + dense, RRF) | 0.656 | 0.812 | 0.767 | 0.742 | 4.6 |
| Hybrid + cross-encoder rerank (bge-reranker-base, CPU) | 0.781 | 0.938 | 0.858 | 0.821 | 1100.7 |
