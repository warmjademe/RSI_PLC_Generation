# PLC Named Paper Baselines

This package reproduces the seven named comparison methods used by the
Memory-to-Skills paper and adapts them to the same Delta PLC generation task.
It is an API-only study: DeepSeek V4 Flash is called through TeamRouter, and no
base-model weights are fine-tuned.

The implementation registry still contains all seven methods. The formal PLC
study selects four. EvoSkill was stopped because its per-mutation full PLC
validation loop exceeded the intended operational cost and latency envelope.
OpenSpace was excluded before launch because static inspection showed a
single-threaded 1000-step analysis/evolution loop with multiple possible model
calls per step. SkillFlow-Evolve was stopped after one controlled resume because
provider stalls and recent throughput projected beyond its four-hour guard.
These decisions were made before the sealed test inventory was opened. Partial
development evidence is retained rather than deleted.

## Experiment boundary

- Training input is the immutable 1000-trajectory PLC corpus.
- The sealed 100-task test split must remain unopened until all four selected
  methods, the baseline selection, and the evaluation protocol are frozen.
- Every method uses the same requested model, candidate budget, PLC verifier
  chain, two controller targets, two output languages, and memory-context cap.
- The mature generation--compile--feedback implementation is imported
  read-only from `our_method_Deploy`; this repository never edits it.
- API credentials are read only from `TEAMOROUTER_API_KEY` and must not appear
  in configuration, artifacts, logs, prompts, or manifests.

The authoritative experiment contract is
[`configs/common_evaluation_v1.json`](configs/common_evaluation_v1.json). The
training corpus and upstream revisions are pinned in
[`configs/huashuo_training_freeze_v1.json`](configs/huashuo_training_freeze_v1.json)
and [`configs/upstream_sources_v1.json`](configs/upstream_sources_v1.json).
The selected four-method study and the retained cost-exclusion evidence are
pinned in
[`configs/formal_baseline_selection_v4.json`](configs/formal_baseline_selection_v4.json).
Because the read-only mature PLC project currently has local changes, its Git
commit alone is insufficient. `configs/mature_reference_freeze_v1.json`
therefore pins the exact 18 prompt, policy, generator, and validator files used
on Huashuo. EvoSkill and sealed evaluation fail before a rollout if any of
those bytes changes.

## Methods

| Method | What is learned from the 1000 trajectories | Frozen test-time behavior |
|---|---|---|
| Vanilla Agent | Nothing; it only binds the common corpus as a no-learning control | No external memory |
| Memento | State--action--result cases and a frozen dense index | Retrieve four scoped cases |
| EverMemOS / EverOS | Episode narratives, atomic facts, foresight, and semantic scenes | Hierarchical dense/BM25 scene retrieval |
| MemSkill | A memory bank plus a PPO-selected, dynamically evolved memory-operation bank | Retrieve up to 20 scoped procedural memories |
| EvoSkill (excluded from the formal study) | Failure-driven skill programs selected by live mature-PLC validation | Partial development artifacts retained; no sealed-test run |
| OpenSpace (excluded from the formal study) | A sequential local skill DAG maintained by selection, post-execution analysis, and skill evolution | Cost-review evidence retained; no formal training or sealed-test run |
| SkillFlow-Evolve (excluded from the formal study) | Four parallel patch-based skill libraries | 170 development checkpoints retained; no frozen method or sealed-test run |

"Training" here means constructing and freezing external memory or skills. It
does not mean updating DeepSeek parameters.

## Reproduction commands

Run these commands on the Huashuo host from this directory. Set
`TEAMOROUTER_API_KEY` through the protected local key file before a method that
uses the model.

```bash
.venv/bin/plc-paper-baselines \
  --config configs/huashuo_training_freeze_v1.json verify-corpus

.venv/bin/plc-paper-baselines \
  --config configs/huashuo_training_freeze_v1.json \
  freeze --method evermemos --device cuda

.venv/bin/plc-paper-baselines \
  --config configs/huashuo_training_freeze_v1.json \
  verify-freeze --method evermemos

# Expected to fail closed until all four selected methods are frozen.
.venv/bin/plc-paper-baselines \
  --config configs/huashuo_training_freeze_v1.json verify-all-freezes
```

Replace `evermemos` with any identifier listed by `plc-paper-baselines --help`.
Each trainer writes atomic checkpoints and resumes rather than overwriting
completed model work. A `freeze_manifest.json` binds every final artifact to
the corpus, source revision, and common protocol.

For the current one-method-at-a-time Huashuo run,
`scripts/continue_evermemos_to_memskill.sh` provides a fail-closed handoff. It
waits for the recorded EverMemOS process to exit, verifies that method against
the common protocol, and only then launches formal MemSkill formation. It exits
without launching MemSkill if the process identity changed, the credential
file permissions are unsafe, EverMemOS failed to freeze, or artifact
verification fails. The script neither reads nor evaluates the sealed test
split.

`scripts/continue_remaining_formal_baselines.sh` then waits for that handoff,
verifies EverMemOS and MemSkill, and runs the readiness gate over the four
already frozen selected methods. It
verifies every selected method and then runs
`verify-all-freezes`; it has no sealed-test path and cannot open the test
inventory. PID identity checks prevent a stale PID from being treated as a
running trainer. The stopped EvoSkill run and its incomplete first-generation
development validation remain under `runtime`; the formal queue never resumes
or evaluates it. OpenSpace is likewise skipped. The stopped SkillFlow-Evolve
run retains 170 atomic checkpoints but is not considered frozen and is never
eligible for the sealed test set.

`verify-all-freezes` is the machine-enforced sealed-test readiness gate. It
hash-verifies every selected method, confirms the shared selection, protocol,
corpus, and upstream revision bindings, and rejects any test-split evidence or
base-weight update.
It never receives or opens a sealed-test path.

After that gate passes, `seal-task-order` accepts one explicit assignment
document, verifies exactly 100 immutable tasks with 25 tasks per track, and
seals their shared order. `evaluate-sealed-method` evaluates one already-frozen
method with a shared pool of all five Huashuo Windows spools. Every retrieval,
worker assignment, infrastructure execution, and conclusive metric is written
before the next state transition, so restart does not repeat completed paid
work. `finalize-sealed-evaluation` succeeds only after all 400 method/task
trials exist; it produces the four-method table, paired bootstrap intervals,
three pre-registered comparisons against Vanilla, exact McNemar tests, and
BH--FDR-adjusted results. A process-level lock on the common output root rejects
accidental simultaneous method commands, because independent processes cannot
safely lease the same physical ISPSoft/COMMGR pool.
The readiness attestation also binds the five-worker collector configuration
and mature-reference snapshot. Their hashes are carried through the common
task order, retrieval journal, worker assignment, execution record, trial row,
and method result, preventing old paid results from being silently reused after
an infrastructure or Harness change.

```bash
# These commands are intentionally invalid until verify-all-freezes passes.
.venv/bin/plc-paper-baselines --config configs/huashuo_training_freeze_v1.json \
  seal-task-order --assignments /protected/sealed_assignments.json \
  --output runtime/common_sealed_task_order.json

.venv/bin/plc-paper-baselines --config configs/huashuo_training_freeze_v1.json \
  evaluate-sealed-method --method vanilla_agent \
  --task-order runtime/common_sealed_task_order.json \
  --output-root runtime/common_sealed_evaluation --device cuda

.venv/bin/plc-paper-baselines --config configs/huashuo_training_freeze_v1.json \
  finalize-sealed-evaluation \
  --task-order runtime/common_sealed_task_order.json \
  --output-root runtime/common_sealed_evaluation
```

The assignment document has schema
`{"schema_version":1,"kind":"named_baseline_sealed_assignments",` followed by
an `assignments` array. Each item contains only `task_root`, `target`, and
`output_language`. No credential or model setting belongs in this file.
After all four selected freezes exist, `scripts/run_common_sealed_evaluation.sh` wraps
the gate, single task-order seal, four sequential method runs, and final
paired analysis. The script accepts the assignment document and an explicit
output root; it is prepared but must not be launched before formal training
finishes.

## Metrics

The source paper's main EvoAgentBench comparison reports Pass@1 and cost, where
cost is mean interaction turns or output characters. The PLC mapping preserves
those two constructs:

- `trial_pass_at_1`: whether any of at most three generated candidates passes
  the conclusive PLC Oracle;
- `interaction_turns`: candidate-generation calls;
- `output_characters`: total generated candidate characters.

The study additionally records first-candidate pass, feedback rounds, tokens,
wall time, actual provider requests, resolved model labels, memory operations,
retrieved items, injected skills, and infrastructure retries. Infrastructure
errors are rerun and reported separately; they are never counted as model
failures.

See [`docs/NAMED_BASELINES_REPRODUCTION_SPEC.md`](docs/NAMED_BASELINES_REPRODUCTION_SPEC.md)
for the method-by-method reproduction and evaluation rules.
