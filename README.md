# Recursive Self-Improvement for PLC Structured Text Program Generation

Research code, a 100-task evaluation dataset, and recorded experiment evidence for generating IEC 61131-3 Structured Text (ST) programs with large language models.

The method connects two components: **current-task error feedback** and **cross-task experience accumulation**. The generator remains fixed; reusable knowledge is accumulated outside the model as tasks are completed.

The study evaluates Qwen3.8-27B (`Q4_K_M`), DeepSeek V4.1 Flash, and Claude Haiku 4.5 (20251001). It compares eight baselines, studies four component settings, measures model-call and token costs, and evaluates generation with Delta DVP48ES300R vendor compilation and simulation. This repository contains the implementations, public task specifications and tests, six process-evidence archives for RQ1–RQ3, and the completed RQ4 audit data.

[Method](#method) · [Dataset](#evaluation-dataset) · [Baselines](#baselines) · [Results](#recorded-results) · [RQ4](#rq4-vendor-compilation-and-simulation) · [Getting started](#getting-started) · [Evidence](#experiment-evidence) · [Reproduction](#running-and-reproducing-studies)

The paper addresses four questions: how the method compares with existing approaches (RQ1), how the two components affect generation (RQ2), what generation and knowledge accumulation cost (RQ3), and whether the method generates programs that pass a specific PLC platform's compilation and simulation checks (RQ4).

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

Task construction starts from 50 basic control behaviors in ten functional categories. Ordered pairs of behaviors form two subsystems connected by enable, readiness, reset, and output-isolation requirements. Ten combinations per ordered category pair produce 1,000 candidate tasks. The evaluation selects the 100 longest reference programs, with task ID as the tie breaker. The intermediate selection of 200 tasks yields the same top 100 as ranking all 1,000 directly.

The selected tasks cover all ten categories, 38 basic behaviors and 43 ordered category pairs; 66 tasks include multi-device coordination. Line counts include declarations, comments and blank lines. Reference implementations support selection and verification and are not provided to the generator.

- [`tasks/`](test_dataset_100_tasks/tasks/): requirements and interfaces.
- [`test_cases/`](test_dataset_100_tasks/test_cases/): test inputs and expected outputs.
- [`evaluator/`](test_dataset_100_tasks/evaluator/): executable-test plans.
- [`selection.json`](test_dataset_100_tasks/selection.json): ranking and provenance.
- [`manifest.json`](test_dataset_100_tasks/manifest.json): file integrity information.
- [`preparation_report.json`](test_dataset_100_tasks/preparation_report.json): dataset preparation checks.

In RQ1–RQ3, a task succeeds when the **same generated candidate passes MatIEC compilation and all specified scan-test assertions**. Each test scenario starts with a fresh function-block instance. Unknown tool outcomes remain in the success-rate denominator. Baselines receive requirements and interfaces; the proposed method and its ablations additionally receive the full public scan tests. RQ1 compares these recorded configurations; RQ2 holds inputs, sampling and validation constant while changing the components.

Dataset validation combines manual review of reference programs and test oracles, formal and semantic checks supported by PLCverif/CBMC/nuXmv and OpenPLC, and target-platform compilation and simulation. The reported generation outcomes use the concrete validation procedures identified for each research question below.

The task pool has prior development and experiment exposure. The reported setting is an ordered online task stream: every method or ablation starts with an empty knowledge store and learns only from its own completed tasks. It does not preload the earlier 1,000-task asset collection.

## Baselines

The eight baselines cover independent generation, example reuse, retrieval, and memory-based methods.

| Baseline | Main comparison mechanism | Current-task error feedback |
|---|---|---|
| [Vanilla](baseline_Vanilla/) | Independent generation without historical memory | No |
| [FewShot](baseline_FewShot/) | The first two successful historical tasks become fixed examples | No |
| [FinalCodeRAG](baseline_FinalCodeRAG/) | Retrieval of up to two historical successful programs | No |
| [RawTrajectoryRAG](baseline_RawTrajectoryRAG/) | Retrieval of historical attempts, feedback, and task outcomes | No |
| [Memento](baseline_Memento/) | State/action/outcome case memory with nonparametric retrieval | Yes |
| [EverMemOS](baseline_EverMemOS/) | Structured MemCell/MemScene memory and retrieval | Yes |
| [MemSkill](baseline_MemSkill/) | Memory operations with a small learned controller | Yes |
| [MSCE](baseline_MSCE/) | Trajectory, strategy, and knowledge accumulation with skill admission | Yes |

These are PLC adaptations. [`MIGRATION_SOURCES.json`](MIGRATION_SOURCES.json) records implementation provenance. The generator is fixed in the proposed method; MemSkill's separate controller is a baseline-specific update.

Each baseline starts without historical assets and can publish memory only after a task ends. For the first four baselines, an additional attempt does not receive the current task's failed code or error feedback. Historical feedback from completed tasks remains available to methods that retain it. Use the frozen configuration and source associated with a result when inspecting its exact behavior; earlier asset-based interfaces remain in the repository for provenance.

## Ablation design

| Setting | Current-task error feedback | Historical rules | Post-task rule accumulation |
|---|---|---|---|
| Full | Yes | Yes | Yes |
| NoAssets | Yes | No | No |
| NoFeedback | No | Yes | Yes |
| Neither | No | No | No |

All settings execute the evaluator. In NoFeedback and Neither, subsequent candidates are generated independently without previous candidate code or task-error feedback. NoFeedback can use the privately retained execution records for learning **after** the task ends.

## Recorded results

The paper's main comparison uses **at most 10 candidates per task**, stopping after a candidate passes compilation and all tests. Each model–configuration pair has 100 tasks. Success rate is the fraction that passes within the candidate budget; `S1` is the first-candidate rate. Calls include generation and auxiliary model requests; tokens are the reported input and output usage across successful and unsuccessful tasks. Missing usage is listed separately.

### RQ1: comparison with eight baselines

| Method | Qwen success rate | DeepSeek success rate | Haiku success rate |
|---|---:|---:|---:|
| Vanilla | 21% | 25% | 20% |
| FewShot | 24% | 51% | 28% |
| FinalCodeRAG | 43% | 48% | 42% |
| RawTrajectoryRAG | 5% | 0% | 6% |
| Memento | 3% | 15% | 12% |
| EverMemOS | 20% | 38% | 13% |
| MemSkill | 4% | 31% | 7% |
| MSCE | 10% | 16% | 8% |
| Full | 52% | 71% | 46% |

Full exceeds the highest baseline success rate by 9, 20 and 4 percentage points, respectively. The paired, multiplicity-adjusted intervals for these three comparisons include zero; [paired comparisons](RESULTS/paper_support/results/contrasts.json) report the uncertainty. First-candidate results, unknown outcomes and costs for all 36 model–configuration pairs are in the [machine-readable metrics](RESULTS/paper_support/results/metrics.json).

### RQ2–RQ3: component settings and costs

| Model | Setting | Passed / 100 | Success rate | Model calls | Total tokens |
|---|---|---:|---:|---:|---:|
| Qwen | Full | 52 | 52% | 952 | 11,187,650 |
| Qwen | NoAssets | 49 | 49% | 783 | 7,802,683 |
| Qwen | NoFeedback | 13 | 13% | 1,056 | 9,894,606 |
| Qwen | Neither | 12 | 12% | 935 | 6,902,756 |
| DeepSeek | Full | 71 | 71% | 733 | 9,268,376 |
| DeepSeek | NoAssets | 73 | 73% | 533 | 5,785,989 |
| DeepSeek | NoFeedback | 71 | 71% | 675 | 7,694,986 |
| DeepSeek | Neither | 63 | 63% | 627 | 5,250,518 |
| Haiku | Full | 46 | 46% | 892 | 21,212,129 |
| Haiku | NoAssets | 44 | 44% | 772 | 14,886,467 |
| Haiku | NoFeedback | 24 | 24% | 1,002 | 20,107,115 |
| Haiku | Neither | 37 | 37% | 781 | 9,876,901 |

Source: [paper metrics](RESULTS/paper_support/results/metrics.json), [3,600 task records](RESULTS/paper_support/results/tasks.json), and [recomputation scripts](RESULTS/paper_support/results/README.md). These paper-aligned aggregates take precedence over earlier progress snapshots for the ten-candidate comparison.

The 12 rows total 9,741 model calls and 129,870,176 reported tokens. Haiku has three calls with missing usage (two in Full and one in Neither); estimates are excluded from these totals. Five Haiku task outcomes are unknown and remain in the denominators.

With current feedback enabled, adding historical rules changes success rates by +3, −2 and +2 percentage points for Qwen, DeepSeek and Haiku. Rule extraction and review account for 18.7%–22.8% of Full's reported tokens. The contribution of cross-task experience therefore varies by model and entails additional model calls.

### Configuration and interpretation

- Regular ablations use up to 10 candidates and 64 model calls per task, temperature 0.7, and a 16,384-token generation limit. Rule extraction and review each allow 4,096 output tokens.
- Current-task feedback retains the two most recent executed attempts. Retrieval selects at most four rules within a 10,000-character context budget.
- The archived DeepSeek Full extension reaches 84% with up to **20 candidates** per task. It is a separate budget-extension result; the main table above restores the original ten-candidate result and its learning cost.
- Recorded baseline runs use temperature 0 and an 8,192-token generation limit. Haiku baseline records extend earlier four-candidate runs to ten while retaining their original task knowledge snapshots.
- Baselines and ablations ran on different hosts. Compare wall-clock time together with hardware, queueing, provider, and sampling settings.

The models cover two open-weight families and a widely used commercial model family. Quantized Qwen supports evaluation under limited local compute; DeepSeek Flash and Haiku provide complementary code-generation and cost profiles. Exact request/response identifiers and configurations are retained with each run.

The eight-baseline results are separate from the four-arm table above. Their summaries are under [`RESULTS/nas/`](RESULTS/nas/); method and ablation summaries are under [`RESULTS/huashuo/`](RESULTS/huashuo/).

### RQ4: vendor compilation and simulation

Full was independently run with each model on the same 100 tasks using **ISPSoft 3.24** and **COMMGR 2.11.0.14** for the **Delta DVP48ES300R**. Each run starts from an empty knowledge store and allows at most ten candidates per task. Compilation and simulation feedback drive both current-task repair and subsequent knowledge accumulation. The experiment preserves the original 516 test scenarios and expected outputs.

| Model | Tasks with a compiling candidate | S1 | Success rate | Unknown | Mean attempts among successes | Model calls | Reported tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen | 98 | 2% | 34% | 0 | 4.82 | 1,003 | 11,616,766 |
| DeepSeek | 88 | 14% | 54% | 0 | 3.30 | 774 | 9,820,513 |
| Haiku | 89 | 5% | 35% | 4 | 4.74 | 942 | 22,956,221 |

Success requires the same candidate to pass vendor compilation and every simulation scenario. Haiku has six calls without complete usage; its token total includes only reported usage. All unknown outcomes remain in the denominator.

These results show that the method can generate programs meeting the compilation and test requirements of a specific PLC platform. Compared with RQ1's 52%, 71% and 46%, the vendor success rates are lower by 18, 17 and 11 percentage points. The two experiments independently generate programs with different validation and feedback toolchains; target-specific language and engineering constraints change both repair and learning. RQ4 evaluates vendor simulation; physical-controller testing is future work.

The [RQ4 package](RESULTS/paper_support/vendor/README.md) provides 300 task records, candidate outcomes and per-call usage, 123 passing receipt audits, diagnostic examples, and source-file hashes. Verify the published records without downloading the large archives:

```bash
python -B RESULTS/paper_support/vendor/verify.py
```

## Repository layout

```text
our_method/method_history_feedback/  Current two-component method
baseline_*/                         Eight baseline packages
baseline_common/                    Providers, budgets, tools, and shared utilities
test_dataset_100_tasks/              Requirements, test cases, and evaluation plans
RESULTS/                            Six RQ1–RQ3 evidence archives and summaries
RESULTS/paper_support/results/       Paper-aligned RQ1–RQ3 metrics and analysis
RESULTS/paper_support/vendor/        Completed RQ4 audit, costs, and verifier
artifacts/                          Result snapshots, preparation records, and audits
tests/                              Protocol and implementation tests
tools/                              Data, evidence-export, and inspection utilities
infrastructure/                     Deployment and engineering-environment scripts
semantic_contract_harness/           Historical method documentation index
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

The groups are `nas/{qwen,deepseek,haiku}` for the eight baselines and `huashuo/{qwen,deepseek,haiku}` for the method and ablations. [`RESULTS/INDEX.json`](RESULTS/INDEX.json) indexes these six RQ1–RQ3 packages. The separate [RQ4 audit package](RESULTS/paper_support/vendor/README.md) is stored as ordinary Git files and does not require Git LFS. Its source manifest records collection-time hashes; complete vendor project directories and raw receipts are not all embedded in the audit.

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
3. Assemble the inputs required by the selected configuration: requirements and interfaces for baselines; requirements, interfaces and full public tests for the method and its ablations. Preserve the recorded scan semantics.
4. Enable the intended component switches and create an independent empty knowledge store for each setting.
5. Record all requests, responses, tool receipts, candidate counts, and usage, including failed attempts and post-task learning.

The generic command-line initializer retains a legacy default of 117 tasks. The underlying `study.initialize(..., expected_count=100)` API supports an explicit count, but the recorded 100-task input assembly and evaluator integration belong to the experiment orchestration. Pointing the generic CLI at the 100-task directory alone does not reconstruct that pipeline.

RQ1–RQ3 use MatIEC 0.1 (with the `REPEAT UNTIL` semantic patch), GCC 15.2.0, 100 ms scan tests and an absolute REAL comparison tolerance of `1e-3`. The completed RQ4 study uses ISPSoft 3.24 and COMMGR 2.11.0.14. Formal and semantic validation infrastructure also includes PLCverif (2024-10-21 build), CBMC 6.10.0, nuXmv 2.0.0 and OpenPLC v3.

To recompute the paper's RQ1–RQ3 aggregates after obtaining the six evidence archives:

```bash
python -B RESULTS/paper_support/results/collect.py
python -B RESULTS/paper_support/results/analyze.py
```

Future evaluation will extend the study to independently held-out tasks, repeated runs and task orders, budget-matched comparisons, additional PLC models and physical-controller I/O tests.

## Citation, reuse, and contributions

The manuscript is titled *Recursive Self-Improvement for PLC Structured Text Program Generation*. Until publication metadata is available, cite this repository URL together with the commit used:

```text
RSI_PLC_Generation. https://github.com/warmjademe/RSI_PLC_Generation
Version: <commit SHA>; accessed: <date>.
```

No top-level license is currently included. Reuse permissions and licenses for incorporated baseline code and source data should be checked with their respective owners; implementation provenance is recorded in [`MIGRATION_SOURCES.json`](MIGRATION_SOURCES.json).

Use [GitHub Issues](https://github.com/warmjademe/RSI_PLC_Generation/issues) for reproducibility questions or bug reports. Include the commit, Python and tool versions, method configuration, task ID, and a minimal reproduction with credentials removed.
