# RQ4: Delta DVP48ES300R vendor compilation and simulation

This package contains the completed RQ4 audit for the paper *Recursive Self-Improvement for PLC Structured Text Program Generation*. Each of the three models independently runs Full on the same 100 tasks, starting with an empty knowledge store and allowing at most 10 candidates per task. Feedback comes from ISPSoft 3.24 compilation and COMMGR 2.11.0.14 DVP-ES3 simulation.

A task succeeds when the same candidate passes compilation and every test scenario. The suite contains 516 scenarios, 2,845 logical scans and 16,644 output assertions, with a 100 ms logical scan step. This is a separate generation experiment using vendor feedback; it does not replay the programs produced in RQ1. The evidence concerns vendor simulation, with physical-controller evaluation left to future work.

| Model | Tasks with a compiling candidate | First-candidate successes | Successes / 100 | Unknown | Mean attempts among successes | Model calls | Reported tokens | Calls missing usage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen | 98 | 2 | 34 | 0 | 4.82 | 1,003 | 11,616,766 | 0 |
| DeepSeek | 88 | 14 | 54 | 0 | 3.30 | 774 | 9,820,513 | 0 |
| Haiku | 89 | 5 | 35 | 4 | 4.74 | 942 | 22,956,221 | 6 |

Unknown outcomes remain in the denominator. Calls and tokens include unsuccessful generation attempts and post-task knowledge extraction and review. Haiku's token count contains the reported portion; the six calls with missing usage are not estimated into this total.

## Files

- [`vendor_audit.json`](vendor_audit.json): the original audit collected on 2026-09-20. It contains the frozen protocol, 300 task records, candidate-stage outcomes, per-call usage, 123 passing receipt audits, diagnostic examples, and a 6,182-entry source-file hash manifest.
- [`summary.json`](summary.json): compact results recomputed from the task and call records.
- [`verify.py`](verify.py): an offline verifier for task coverage, candidate limits, statuses, usage totals, and receipt case coverage against the public task package.
- [`MANIFEST.json`](MANIFEST.json): hashes and sizes of the published files in this package.

The audit records the source-code and runtime-image binding checks performed during collection. The original candidate programs, complete vendor receipts and project directories are referenced by their provenance hashes; their contents are not all embedded in this audit package. The six process-evidence archives elsewhere in `RESULTS/` cover RQ1–RQ3, not this RQ4 run.

## Verify locally

From the repository root, using Python 3.11 or newer:

```bash
python -B RESULTS/paper_support/vendor/verify.py
```

The verifier needs neither Git LFS downloads nor a model or vendor-software connection. It recomputes all aggregate results, checks the 100-task dataset manifest, and compares each passing audit's case identifiers with the corresponding public tests. Source-manifest entries describe collection-time provenance; checking the original source files themselves requires those files.
