# Phase 8 holdout comparison

The frozen 500-sample holdout was evaluated once per locked arm. Sample IDs and ordering match exactly.

| Metric | Baseline | Optimized |
|---|---:|---:|
| Accuracy | 46.40% | 100.00% |
| Macro precision | 69.10% | 100.00% |
| Macro recall | 41.14% | 100.00% |
| Macro F1 | 31.18% | 100.00% |
| Mismatch recall | 17/220 (7.73%) | 220/220 (100.00%) |
| False positives | 2/280 (0.71%) | 0/280 (0.00%) |
| Execution failures | 8 | 0 |
| Attempted LLM calls | 500 | 35 |
| Recorded tokens | 368,191 | 21,722 |

Attempted LLM calls fell 93.00%; recorded tokens fell 94.10%. Baseline failures were eight typed `llm_timeout` results. The optimized development-to-holdout generalization gap is zero for accuracy, mismatch recall, and false-positive rate.

Latency values in the arm artifacts are controlled serial quality-evaluation latency, not the production performance benchmark.
