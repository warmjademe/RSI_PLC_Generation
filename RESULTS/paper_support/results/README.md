# RQ1–RQ3 results at the paper's ten-candidate budget

[`metrics.json`](metrics.json) contains 36 model–configuration summaries: eight baselines and four component settings for each of Qwen, DeepSeek and Haiku. [`tasks.json`](tasks.json) contains their 3,600 task records. Full appears once in these records and is shared by the overall comparison and ablation analysis.

The main `success`, `first_success`, `cost`, and `prefix` fields use at most ten candidates. DeepSeek Full's later budget extension is retained separately in `final_success`, `success_final`, `first_success_final`, and `cost_final`. Main costs include the original ten-candidate run and its post-task learning. Reported input and output tokens determine `tokens`; estimates and calls with missing usage are stored separately.

[`contrasts.json`](contrasts.json) contains task-paired differences and bootstrap intervals, using the 43 ordered functional-category pairs as clusters. The scripts use 20,000 bootstrap samples and Bonferroni correction for the respective comparison families.

## Recompute from the archived records

From the repository root, after downloading the six Git LFS evidence archives:

```bash
python -B RESULTS/paper_support/results/collect.py
python -B RESULTS/paper_support/results/analyze.py
python -B RESULTS/paper_support/results/contrasts.py
```

`collect.py` selects task results and call ledgers from the archives and checks each selected file against its original SHA-256 manifest. The extracted `evidence/` directory is a disposable cache excluded from Git. `analyze.py` rebuilds task records and aggregates; `contrasts.py` computes the paired comparisons. These scripts run locally without model calls or PLC execution.
