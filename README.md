# Recursive Self-Improvement for PLC Structured Text Program Generation

Research code, a 100-task evaluation dataset, and recorded experiment evidence for generating IEC 61131-3 Structured Text (ST) programs with large language models.

The method connects two components: **current-task error feedback** and **cross-task experience accumulation**. The generator remains fixed; reusable knowledge is accumulated outside the model as tasks are completed.

The current study evaluates Qwen, DeepSeek, and Haiku, compares eight baselines, and includes four ablation settings. This repository contains the method implementations, public task specifications and tests, result summaries, and six process-evidence archives.

[Method](#method) · [Dataset](#evaluation-dataset) · [Baselines](#baselines) · [Results](#recorded-results) · [Getting started](#getting-started) · [Evidence](#experiment-evidence) · [Reproduction](#running-and-reproducing-studies)

## Method

The current implementation is in [`our_method/method_history_feedback/`](our_method/method_history_feedback/). Its two-level execution path is enabled with `two_level_feedback: true`.

### Component 1: current-task error feedback

Each candidate is compiled and tested. Compiler diagnostics include the relevant source context. Runtime feedback includes failing input sequences, scan positions, expected and actual outputs, and assertion counts.

The next generation call receives the current task, the recent tool feedback, a source candidate, and any retrieved historical rules. It returns complete ST code together with a short proposed explanation of the change. The tools then evaluate the new candidate.

The controller restores a better previous candidate after a regression and requests a fresh implementation after duplicate code or repeated failure signatures. It stops on success, an exhausted budget, or an unresolved tool error.

### Component 2: cross-task experience accumulation

After a task ends, the method identifies observed **failure-to-pass transitions within the same validation stage**. It uses the code changes and tool records to propose conditional rules containing:

- a concrete implementation recommendation;
- applicability conditions and exceptions;
- suggested checks;
- keywords and source-evidence identifiers.

A separate model call reviews each proposal against its evidence. Accepted records are versioned and published at the task boundary. Compilation transitions support compilation rules; runtime rules require runtime transitions.

Later tasks retrieve relevant active rules from their starting knowledge snapshot. The new tasks produce further execution records, extending the experience store. During a task, retrieval may change with the current error, but newly learned rules become available only to subsequent tasks.

| Implementation | Responsibility |
|---|---|
| [`two_level.py`](our_method/method_history_feedback/two_level.py) | Error feedback, repair policy, transition extraction, rule generation, and review |
| [`workflow.py`](our_method/method_history_feedback/workflow.py) | Candidate execution, validation, budgets, and task completion |
| [`knowledge.py`](our_method/method_history_feedback/knowledge.py) | Scope-aware rule retrieval and ranking |
| [`store.py`](our_method/method_history_feedback/store.py) | Ordered publication, evidence storage, and knowledge snapshots |
| [`study.py`](our_method/method_history_feedback/study.py) | Task streams, configuration freezing, recovery, and reporting |

Earlier implementations also remain under `our_method/`. The current two-level method is the subpackage identified above.

## Evaluation dataset

[`test_dataset_100_tasks/`](test_dataset_100_tasks/) contains the current evaluation task pool.

| Item | Value |
|---|---:|
| Tasks | 100 |
| Test scenarios | 516 |
| Scheduled scans | 2,845 |
| Output assertions | 16,644 |
| Scan period | 100 ms |
| Source reference-program length | 100–160 lines; median 110 |

The tasks were selected by descending reference-program length from the previous 200-task selection, with task ID as the tie breaker. Line counts include declarations, comments, and blank lines. Reference implementations were used for selection and integrity checks and are not included in the model inputs or this task package.

- [`tasks/`](test_dataset_100_tasks/tasks/): requirements and interfaces.
- [`test_cases/`](test_dataset_100_tasks/test_cases/): test inputs and expected outputs.
- [`evaluator/`](test_dataset_100_tasks/evaluator/): executable-test plans.
- [`selection.json`](test_dataset_100_tasks/selection.json): ranking and provenance.
- [`manifest.json`](test_dataset_100_tasks/manifest.json): file integrity information.
- [`preparation_report.json`](test_dataset_100_tasks/preparation_report.json): dataset preparation checks.

The recorded generation experiments provide requirements, interfaces, and tests to the model. A task succeeds when the **same generated candidate compiles and passes all specified runtime assertions**. Each test scenario starts with a fresh function-block instance. Unknown tool outcomes are reported separately and do not count as successes.

The task pool has prior development and experiment exposure. The reported setting is an ordered online task stream: every method or ablation starts with an empty knowledge store and learns only from its own completed tasks. It does not preload the earlier 1,000-task asset collection.

## Baselines

The eight baselines cover independent generation, example reuse, retrieval, and memory-based methods.

| Baseline | Main comparison mechanism |
|---|---|
| [Vanilla](baseline_Vanilla/) | Independent generation without historical memory |
| [FewShot](baseline_FewShot/) | A fixed set of successful examples after collection |
| [FinalCodeRAG](baseline_FinalCodeRAG/) | Retrieval of historical successful programs |
| [RawTrajectoryRAG](baseline_RawTrajectoryRAG/) | Retrieval of original attempts, feedback, and task outcomes |
| [Memento](baseline_Memento/) | State/action/outcome case memory with nonparametric retrieval |
| [EverMemOS](baseline_EverMemOS/) | Structured MemCell/MemScene memory and retrieval |
| [MemSkill](baseline_MemSkill/) | Memory operations with a small learned controller |
| [MSCE](baseline_MSCE/) | Trajectory, strategy, and knowledge accumulation with skill admission |

These are PLC adaptations. [`MIGRATION_SOURCES.json`](MIGRATION_SOURCES.json) records implementation provenance. The generator is fixed in the proposed method; MemSkill's separate controller is a baseline-specific update.

The repository includes both earlier asset-based interfaces and source snapshots of the later online experiments. Use the frozen configuration and source associated with a result when inspecting its exact baseline behavior.

## Ablation design

| Setting | Current-task error feedback | Historical rules | Post-task rule accumulation |
|---|---|---|---|
| Full | Yes | Yes | Yes |
| NoAssets | Yes | No | No |
| NoFeedback | No | Yes | Yes |
| Neither | No | No | No |

All settings execute the evaluator. In NoFeedback and Neither, subsequent candidates are generated independently without previous candidate code or task-error feedback. NoFeedback can use the privately retained execution records for learning **after** the task ends.

## Recorded results

The table below summarizes the completed **method and ablation experiments**, with 100 tasks per row. Calls include generation and post-task learning; tokens include input and output usage, including unsuccessful attempts.

| Model | Setting | Passed / 100 | Success rate | Model calls | Total tokens |
|---|---|---:|---:|---:|---:|
| Qwen | Full | 52 | 52% | 952 | 11,187,650 |
| Qwen | NoAssets | 49 | 49% | 783 | 7,802,683 |
| Qwen | NoFeedback | 13 | 13% | 1,056 | 9,894,606 |
| Qwen | Neither | 12 | 12% | 935 | 6,902,756 |
| DeepSeek | Full | 84 | 84% | 1,009 | 13,114,827 |
| DeepSeek | NoAssets | 73 | 73% | 533 | 5,785,989 |
| DeepSeek | NoFeedback | 71 | 71% | 675 | 7,694,986 |
| DeepSeek | Neither | 63 | 63% | 627 | 5,250,518 |
| Haiku | Full | 46 | 46% | 892 | 21,312,020 |
| Haiku | NoAssets | 44 | 44% | 772 | 14,886,467 |
| Haiku | NoFeedback | 24 | 24% | 1,002 | 20,107,115 |
| Haiku | Neither | 37 | 37% | 781 | 9,912,418 |

Source: [final report](artifacts/three_model_final_20260918/REPORT.zh.md) and its [machine-readable snapshot](artifacts/three_model_final_20260918/snapshot.json).

The 12 rows total 10,017 model calls and 133,852,035 tokens. Haiku includes 135,408 estimated tokens for three calls with incomplete usage records; reported usage and estimates are retained separately. Five Haiku task outcomes are unknown and are included in the task denominators.

### Configuration and interpretation

- Regular ablations use up to 10 candidates and 64 model calls per task, temperature 0.7, and a 16,384-token generation limit. Rule extraction and review each allow 4,096 output tokens.
- Current-task feedback retains the two most recent executed attempts. Retrieval selects at most four rules within a 10,000-character context budget.
- The merged DeepSeek Full record includes up to **20 candidates** per task. Its 84% result uses that recorded budget.
- Recorded baseline runs use temperature 0 and an 8,192-token generation limit. Haiku baseline records extend earlier four-candidate runs to ten while retaining their original task knowledge snapshots.
- Baselines and ablations ran on different hosts. Compare wall-clock time together with hardware, queueing, provider, and sampling settings.

Qwen used a local quantized 27B deployment, DeepSeek an official API, and Haiku a routed API. Exact request/response model identifiers and configurations are stored with each run.

The eight-baseline results are separate from the four-arm table above. Their summaries are under [`RESULTS/nas/`](RESULTS/nas/); method and ablation summaries are under [`RESULTS/huashuo/`](RESULTS/huashuo/).

## Repository layout

```text
our_method/method_history_feedback/  Current two-component method
baseline_*/                         Eight baseline packages
baseline_common/                    Providers, budgets, tools, and shared utilities
test_dataset_100_tasks/              Requirements, test cases, and evaluation plans
RESULTS/                            Six evidence archives and readable summaries
artifacts/                          Result snapshots, preparation records, and audits
tests/                              Protocol and implementation tests
tools/                              Data, evidence-export, and inspection utilities
infrastructure/                     Deployment and engineering-environment scripts
semantic_contract_harness/           Supporting contract and validation material
docs/                               Implementation history and adaptation notes
```

The top-level `experiments/` orchestration directory is excluded from this public source tree. Selected frozen experiment and evaluator source files are available inside the evidence archives. Some older documentation and retained module names refer to earlier task counts; the current evaluation dataset is the 100-task package above.

## Getting started

The core Python workflow uses Python 3.11 or newer and the standard library. Linux or macOS is required for the file-locking implementation. Git LFS is needed only when downloading the process-evidence archives.

### Clone the code

To inspect the code and summaries without immediately downloading the approximately 1.03 GB of compressed evidence:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/warmjademe/RSI_PLC_Generation.git
cd RSI_PLC_Generation
python3 -m venv .venv
source .venv/bin/activate
python -B -m our_method.method_history_feedback --help
```

Optional encoder and controller dependencies are listed in [`requirements-learning.txt`](requirements-learning.txt). Install them only when using those baseline components.

### Check the local protocol

```bash
python -B -m unittest our_method.method_history_feedback.tests.test_online -v
python -B -m our_method.method_history_feedback demo --output runs/protocol-demo
```

Use a new output directory for the demo. It runs scripted model responses and synthetic validator fixtures to illustrate task repair, publication, and later retrieval. It checks orchestration without a model API or PLC compiler. The demo exercises the compatibility workflow; it is not a reproduction of the current two-level experiment results.

## Experiment evidence

Each of the six run groups contains:

| File or directory | Contents |
|---|---|
| `summary/` | Results, configurations, completion audits, and cost summaries |
| `process_evidence.tar.gz` | Selected per-task process evidence |
| `MANIFEST.jsonl` | Paths, sizes, and SHA-256 hashes of exported evidence files |
| `EXPORT.json` | Export scope, archive hash, counts, and verification status |

The groups are `nas/{qwen,deepseek,haiku}` for the eight baselines and `huashuo/{qwen,deepseek,haiku}` for the method and ablations. [`RESULTS/INDEX.json`](RESULTS/INDEX.json) indexes all six packages.

After installing Git LFS, download all archives:

```bash
git lfs install
git lfs pull
```

Or download one group:

```bash
git lfs pull --include="RESULTS/huashuo/qwen/process_evidence.tar.gz"
```

Inspect an archive without extracting it:

```bash
tar -tzf RESULTS/huashuo/qwen/process_evidence.tar.gz
```

The exported evidence covers inputs, frozen source, model requests and responses, candidate programs, validation receipts, runtime traces, learning records, and cost ledgers. Compiler binaries, intermediate builds, caches, repeated memory states, vector arrays, and controller weights are excluded. These are process-review packages rather than complete machine images; the export manifests describe the contents precisely.

## Running and reproducing studies

A live study requires a configured model endpoint and a real compiler/runtime validator. The bundled [example configuration](our_method/method_history_feedback/example_config.json) is a template with unavailable validators.

For the current method, the relevant settings are:

```json
{
  "validation_stages": ["compile", "runtime"],
  "method": {
    "two_level_feedback": true,
    "use_task_feedback": true,
    "use_cross_task_knowledge": true,
    "learn_cross_task_knowledge": true,
    "reflect_after_attempt": false
  }
}
```

This is a configuration fragment. Provide the endpoint, model identifier, sampling settings, budgets, and compiler/runtime commands in your full configuration. API credentials are read through the environment-variable name in `provider.api_key_env`. The provider implementations support OpenAI-compatible and native Anthropic interfaces.

For a faithful rerun:

1. Choose the model and experiment setting, then inspect that run's frozen configuration, source, task order, and result manifests.
2. Restore or provide the required evaluator and orchestration code, install the compiler/runtime, and replace machine-specific paths.
3. Assemble the generation inputs from both `tasks/` and the associated public tests, and preserve the recorded scan semantics.
4. Enable the intended component switches and create an independent empty knowledge store for each setting.
5. Record all requests, responses, tool receipts, candidate counts, and usage, including failed attempts and post-task learning.

The generic command-line initializer retains a legacy default of 117 tasks. The underlying `study.initialize(..., expected_count=100)` API supports an explicit count, but the recorded 100-task input assembly and evaluator integration belong to the experiment orchestration. Pointing the generic CLI at the 100-task directory alone does not reconstruct that pipeline.

The current evaluation uses MatIEC compilation and 100 ms scan tests. The planned controller study targets a Delta DVP48ES300R, using ISPSoft/COMMGR for engineering and simulation followed by separate physical-controller testing. Hardware execution results are not included in the success-rate table above.

## Citation, reuse, and contributions

The manuscript is titled *Recursive Self-Improvement for PLC Structured Text Program Generation*. Until publication metadata is available, cite this repository URL together with the commit used:

```text
RSI_PLC_Generation. https://github.com/warmjademe/RSI_PLC_Generation
Version: <commit SHA>; accessed: <date>.
```

No top-level license is currently included. Reuse permissions and licenses for incorporated baseline code and source data should be checked with their respective owners; implementation provenance is recorded in [`MIGRATION_SOURCES.json`](MIGRATION_SOURCES.json).

Use [GitHub Issues](https://github.com/warmjademe/RSI_PLC_Generation/issues) for reproducibility questions or bug reports. Include the commit, Python and tool versions, method configuration, task ID, and a minimal reproduction with credentials removed.
