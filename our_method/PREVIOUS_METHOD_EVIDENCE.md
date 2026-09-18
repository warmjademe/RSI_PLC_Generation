# Verified training contract transfer, exploratory protocol v1

This is a training-set method experiment authorized on 2026-09-08. It does not
modify the frozen baseline implementations, their memories, the 1000-task
requirements/interfaces/oracles, or the held-out evaluation data. No independent
test-set results are claimed. Successful training repairs are RSI memory assets,
not evidence of generalization to new PLC tasks.

## Problem and baseline relationship

The production collector used the outcome-gated dual-memory design: structured
retrieval over 175 original successful programs plus 136 repair steps extracted
from ultimately successful original trajectories. It also already supplied a
verified opposite-language implementation of the same task when available and
the current task's prior candidate/validation feedback. These are existing
mechanisms, not contributions of this change.

The final-code RAG baseline provides verified source binding and complete program
retrieval. The raw-trajectory baseline preserves failed attempts and feedback.
MSCE and the existing outcome-gated method motivate retaining evidence-backed
experience and not treating a failed repair as positive knowledge. Their memory
construction counts alone do not establish improved generation performance.

This prototype tests two combined changes: refresh the cross-task donor pool
from the fully verified success catalog at each batch boundary, and retrieve
complete programs by the coverage of the current task's A/B subsystem contracts,
with explicit public-requirement and interface differences. It uses no LLM calls
to construct the memory and no model training or weight updates.

## Generation and admission

1. Run the existing full-evidence admission catalog once for the dual-language
   plan. Freeze its exact rows, source audit hash and a compressed snapshot before
   generating any candidate in this batch. Only train records with all six gates
   passed can be donors. New batch results enter only the next batch's snapshot.
2. Keep the existing least-used-first queue and one request per visit. Exclude the
   query task from cross-task donors and require the exact controller and output
   language. A same-task opposite-language reference remains a separate existing
   mechanism shared by both experimental arms.
3. Parse only public R clauses and interface roles/types. Rank candidate donors
   by marginal A/B contract coverage; weight a subsystem twice when an already
   observed failed feedback item explicitly names one of its R identifiers.
   Otherwise use equal subsystem weights. Break ties by interface agreement,
   supervisory contract similarity, whole requirement similarity, and identity.
4. Select at most two complete programs within the existing 14,000-character
   memory budget. Include the donor's public requirements and an explicit delta:
   query-only/donor-only interface variables, type/role changes, timing literals,
   and query clauses that are not identical. Oversized examples are skipped,
   never truncated. Fall back to the frozen baseline memory if none fits. This is
   reference-guided generation; it does not splice program fragments or assert
   that textual contract similarity proves semantic equivalence.
5. Sonnet produces the entire requested ST or typed LadderIR program. The existing
   response-format, interface, MatIEC, OpenPLC feedback, PLCverif and OpenPLC
   confirmation gates are unchanged. Only a fully admitted implementation joins
   the success pool. LD's deterministic ST shadow is not an additional ST success.
6. Record memory source/donor hashes, exact selection, differences, arm assignment,
   prepared/native request, response/model identity, tokens, duration and all
   outcomes. Admission replays the selection and verifies that the selected
   memory actually occurs in the prepared request. Existing native-wire auditing
   binds that request to transport evidence and the charged ledger reservation.

Vendor ISPSoft/COMMGR validation remains waived for this training phase. Portable
verification is not vendor approval or a proof of every natural-language clause.
The fixed draft oracles and finite test vectors retain their previous limitations.

## First exploratory comparison, fixed before responses

The first 18 ordinary coverage visits are the experimental units: nine frozen
dual-memory controls and nine verified-contract-transfer treatments. Preserve
queue order. Block by controller and output language; pair within each block by
prior Sonnet exposure, prior failure stage, then SHA-256 seeded order. Assign one
of each arm per pair with seed `training-contract-transfer-20260908-v1`; balance
odd block leftovers deterministically. Persist all assignments in the plan.
Later batches use the same rule if the A/B deployment continues.

Both arms use the same Sonnet 4.6 provider policy, up to five simultaneous model
requests, nine validation lanes, prior failure context, opposite-language
reference, frozen repair library, validators and original stopping rules. The
current-round allowance remains at most 20 calls per ST/LD implementation across
Aimoniker, EasyMax and TeamRouter. No reset, refund, hidden retry, DeepSeek or Qwen
dispatch is authorized. Every trial call is useful work on an unfinished training
implementation and is charged to its existing allowance.

Primary descriptive outcome: all-six-gate successes divided by native requests,
including transport failures in the denominator. Also report successes per
planned visit, each gate's failure count, infrastructure outcomes, donor fallback
rate, input/output/cache tokens when actually reported, total candidate latency,
and validation latency separately. Report sample sizes and uncertainty; do not
silently exclude unsuccessful or expensive calls. Quoted monetary cost requires
known contemporaneous provider prices; tokens alone are not a price estimate.

The first 18 visits are an operational pilot, not a powered efficacy trial.
Shared workers can interfere through queueing; the residual task pool is selected
by past failures; some train tasks are related compositions; and adaptive memory
refresh changes later batches. Do not pool these into an independent test-set
generalization claim. A later study needs a fresh sealed task set and ablations
separating (a) catalog growth, (b) subsystem retrieval/deltas, and (c) successful
repair memory. Freeze those decisions before examining that study's outcomes.

## Implementation

The collector-side implementation is versioned under
`PLC_Generation_Recursive_Self_Improvement/source_codes/training_datasets/plc_feedback_paths_generators/deployment/`:

- `training_contract_memory_v1.py`: memory, assignment and replay verification.
- `supplement_sonnet_only20_v5.py`: generation integration.
- `sonnet_only20_catalog_v5.py`: historical admission branches plus this method.
- `sonnet_only20_first_validation_v5.py`: binding to the new runner/catalog.
- `supervise_sonnet_only20_v5.py`: ongoing eighteen-visit batches.
- `test_training_contract_memory_v1.py`: negative controls and deterministic checks.
- `test_training_contract_integration_v3.py`: unchanged execution-boundary checks
  and actual fallback-provider attribution regression tests.

The runtime config is `configs/huashuo_sonnet_contract_memory_v1.json`. API secrets
remain in private global environment files. The actual preflight, activation,
per-collection snapshots and experiment results are retained on Huashuo and
mirrored separately; this document is a protocol, not an execution claim.

The first source preflight (collector v3) was superseded before activation. An
independent native-request audit found that eight preceding Sonnet-only v2
success records were exported with the preferred Aimoniker route name even though
their native requests and budget reservations selected TeamRouter. Collector and
catalog v4 preserve the original exported snapshot and correct only the provider,
base URL and resulting record hash. Candidate programs, interfaces, requirements,
usage and verification evidence are unchanged. This correction precedes the
memory experiment so later provider efficiency statistics use actual dispatch.

## First pilot, observed after the protocol was fixed

The first collection was `sonnet-only20-training-supplement-1788860496465130362-sonnet`.
All eighteen visits issued one native TeamRouter request for `claude-sonnet-4-6`;
the reported response identity was `claude-sonnet-4-6-medium` throughout. The
treatment admitted 2/9 implementations and the control admitted 1/9. All three
new implementations were LD; no ST success was observed in this pilot. The
treatment's one infrastructure outcome remains in both the denominator and the
charged request ledger. Every treatment visit selected a complete donor within
the text budget; none fell back to the old frozen memory.

| Descriptive measure | Frozen dual-memory control | Contract transfer |
| --- | ---: | ---: |
| Native requests | 9 | 9 |
| Fully admitted successes | 1 | 2 |
| Success/request | 11.1% | 22.2% |
| Descriptive Wilson 95% interval | 2.0–43.5% | 6.3–54.7% |
| Reported input + cache-creation + cache-read tokens | 126,863 | 142,330 |
| Reported output tokens | 27,657 | 36,000 |
| Sum of model response latencies (s) | 496.332 | 509.757 |
| Sum of validator durations (s) | 250.727 | 214.753 |

These are descriptive training-pilot results, with the dependence and interference
limitations specified above. Latency sums are not wall-clock throughput. Token
types have different prices; their counts do not establish monetary savings.
The treatment used more total reported tokens in this batch. The sample is too
small to establish either efficacy or cost superiority, so the operational run
continues the fixed A/B comparison rather than declaring a confirmed improvement.

The catalog now contains 1,270 verified implementations (794 ST, 476 LD), leaving
730 missing. The control's successful candidate required a source-binding repair
before admission: its redundant frozen-reference copy lives outside the finalized
Harness inventory. Catalog v5 proves exact byte equality with the already bound
selected-memory artifact and checks the same finalized memory digest, then retains
the duplicate as separately bound evidence. The selected-memory requirement,
candidate bytes and six PLC gates are unchanged; no extra generation was needed.
The previous 1,269 exported rows are unchanged and the control success is added.
Twenty-five boundary and negative-control tests pass. The complete pilot report
is retained in the history's `training_contract_memory_v2/pilot_reports/` directory;
the resumed v5 service is controlled by `training_contract_memory_v3/`.

## Four-batch exploratory update and operational promotion

The next three completed collections ended in `1788861664597737221`,
`1788862521319056800`, and `1788863123167766334` (each has the same
`sonnet-only20-training-supplement-` prefix and `-sonnet` suffix). All native
requests, including infrastructure outcomes and truncated replies, remain in
the denominators and the existing twenty-call ledger.

| Completed batch | Control successes / requests | Transfer successes / requests |
| --- | ---: | ---: |
| 1 | 1 / 9 | 2 / 9 |
| 2 | 1 / 9 | 2 / 9 |
| 3 | 0 / 9 | 2 / 9 |
| 4 | 0 / 9 | 4 / 9 |
| Total | 2 / 36 | 10 / 36 |

Across these four training batches, the observed requests per successful
implementation were 18.0 for the control and 3.6 for transfer. Total reported
tokens (input, cache creation, cache reads and output added without price
weighting) were 634,563 and 658,713, respectively. Their different billing
categories and unknown actual provider charges prevent a monetary-cost claim.
This is a related-task, adaptively refreshed training sample, not an independent
efficacy trial. The comparison does not establish a fivefold causal improvement.

After inspecting these four completed batches, the operational decision is to
use transfer on every subsequent visit. The already running fifth A/B batch is
allowed to finish under its original assignment; it is not silently reassigned
or added to the decision's evidence. The only configuration change is
`training_memory_mode: contract_transfer` in the new
`huashuo_sonnet_contract_memory_all_v1.json`. Supervisor v6 uses a new control
directory, `training_contract_memory_v4`, and the unchanged collector/catalog
v5. Its preflight passes 25 existing boundary, memory, budget and CLI tests.
Activation waits for the old batch to drain and preserves its export, service
definition, source inventory and charged Sonnet counts. No provider, task,
validator, generation prompt, request concurrency or allowance is changed by
this promotion. Runtime activation and native-request evidence, rather than
this protocol, determine whether promotion has actually taken effect.

The fifth collection (`1788863999217259339`) subsequently completed its frozen
assignments: control 0/9, transfer 2/9. Thus all five A/B batches observed control
2/45 versus transfer 12/45. Their unweighted reported token totals were 781,313
and 774,418. These fifth-batch results were not available when promotion was
chosen. The batch drained before activation at epoch `1788864777.3315032`;
activation preserved 1,281 canonical successes and all 126 current-round Sonnet
reservations, with no budget reset. Supervisor v6 then entered startup admission
and planning under the all-transfer configuration.

The independent nine-row delta audit at checkpoint `1788864056296544394`
verified 1,279 canonical implementations (798 ST, 481 LD), leaving 721 missing.
All 1,270 earlier rows were preserved exactly. The same checkpoint is retained
on Huashuo and the local workspace. Later live progress can exceed this frozen
checkpoint.

## Zero-call repair experiment: ordered Boolean writers

Inspection of the first two batches found three finalized LD candidates rejected
for conflicting normal-coil writers. The complete selection was fixed before
revalidation: all such failures in those two collections that were not already
covered at selection. Infrastructure-truncated replies are ineligible. This is
a failure-informed exploratory repair experiment, not an additional randomized
comparison.

`ld_writer_repair_v1.py` creates a separately attributed candidate. A conflicting
normal coil is rewritten to a BOOL assign under a TRUE guard, with the original
rung condition used as the assigned value. An affected multi-instruction rung
is split in place, preserving instruction order and guard re-evaluation. Other
instructions are unchanged. The unchanged typed-IR compiler checks the derived
program, including its existing rung and local-variable limits. Eight tests
cover false-condition clearing, retained writes, multiple normal writes, scan
dependencies, name collisions and rejection of out-of-scope malformed programs.
This operational correspondence does not mean the original invalid IR passed
validation or that the rewrite repairs arbitrary requirement errors.

The replay calls no model and preserves the original response, failed format
evaluation, request identity and charged ledger row. It then runs the unchanged
interface, MatIEC, OpenPLC feedback, PLCverif and OpenPLC confirmation checks on
the new candidate. Results were:

| Implementation | Derived-candidate outcome |
| --- | --- |
| `TR_C03_C10_07__AS228T-A__ld` | All six gates passed |
| `TR_C10_C02_05__DVP48ES300R__ld` | OpenPLC confirmation failed |
| `TR_C10_C09_02__AS228T-A__ld` | OpenPLC feedback failed |

Both hosts independently replayed source identity, unchanged source inventories,
the transformation, native request payloads, ledger bindings, formal-checker
input hashes and OpenPLC candidate embedding. They produced the same single
staged derived-success row, SHA-256
`1ee2cfc3ab8a58d8aa074a46ed4a1f431e0256d1cacc385a76343a187bd828c5`.
The canonical catalog has not yet admitted this new candidate-origin category;
the staged row is explicitly marked `canonical_admission_pending: true`. It is
excluded from both the 1,279 canonical count and the original 10/36 Sonnet result.
Its ST shadow must not create another ST success. Integrating this attributed
recovery category into catalog admission and scheduling remains necessary before
the production collector can use it to suppress further calls or retrieve it.

Replay v1 stopped before any model or validator execution because its selection
incorrectly required a trajectory manifest for unrelated infrastructure failures.
Replay v2 fixes that eligibility check without changing the repair or validators.
Audit v1 used the confirmation directory name for both OpenPLC roles; audit v2
also assumed an identical local orchestrator. Audit v3 uses each role's actual
directory and archives the exact remote reference files. It verifies the parser
and lowering dependencies actually imported locally. The local orchestrator's
unrelated changes remain untouched. Failed observer versions are retained.

The first three batches' 27 treatment prompts each contained one donor, averaging
11,673.9 characters: 3,161.6 for donor requirements, 1,888.7 for adaptation deltas,
and 5,965.4 for the program. Requirement/delta duplication is a measured target
for a later compact-rendering experiment; it has not yet been changed in the
production prompt. The evidence mirror now uses bounded-memory rsync after the
earlier whole-history in-memory mirror was killed. The generator continued
running, and the replacement mirror verified all 3,455 transferred file hashes.

An isolated, no-API memoization benchmark replayed the exact memory selection and
receipt for the first four batches' 36 treatment visits. Caching token sets,
clause parsing and interface schemas reduced this component from 10.662 s to
3.546 s, with byte-identical results. Production code was not modified. This
component measurement cannot explain or predict total catalog/export throughput;
a separate read-only full-catalog profile was launched to locate the longer
startup/export overhead before choosing an optimization.

## Production admission of separately derived LD candidates

The first three completed all-transfer collections used 54 native Sonnet calls
and admitted 28 unmodified-model successes (10, 9 and 9 per batch). Three
infrastructure failures in the third batch remain in the denominator and ledger.
These subsequent training batches have no contemporaneous control arm; their
51.9% yield is an operational observation, not a new causal-effect estimate.

On 2026-09-08, 37 regression and real-evidence boundary tests passed. A separate
full-catalog preflight preserved all 1,309 existing rows, admitted the one staged
LD repair, and verified that production planning excludes it while including its
attributed row in the next memory snapshot. Activation at epoch
`1788867667.4053328` followed a successful drain of supervisor v6 and preserved all
180 charged Sonnet calls. Supervisor v7 and collector/catalog v6 use control
directory `training_contract_memory_v5`; the all-transfer configuration, six
gates and twenty-call Sonnet allowance per ST/LD identity remain unchanged.

The additional `plc-training-ld-writer-recovery.service` selects saved, finalized,
uncovered writer-conflict attempts and revalidates their deterministic repair
without a model client. Each original candidate is recovered at most once;
rejected candidates and infrastructure errors remain recorded. A derived winner
is stable across future catalog builds and counts only as LD. The report script
explicitly excludes this origin from native-model pilot success numerators.

The first canonical export at 11:43:44 UTC contains 1,310 implementations
(817 ST, 493 LD), including one derived LD, leaving 690 missing. This is portable
training validation, with vendor verification still waived. Independent audit
v4 supports the original three-case experiment and closed production recovery
batches, recomputes the transformation, and checks native checker inputs and
worker bindings. It does not call a model or modify the canonical export.

## Continued observations and equivalent catalog optimization

The next two all-transfer batches admitted 11/18 and 7/18 native Sonnet outputs.
The first five all-transfer batches therefore total 46/90 (51.1%), excluding
deterministic recoveries. In the first four batches, ST yielded 25/36 and LD
14/36. These correlated training observations warrant separate language-level
monitoring; they do not establish a stable generalization or billing advantage.

An independent delta checkpoint at `1788869121063837838` verified 1,324 canonical
rows (823 ST, 501 LD), preserving all previous 1,310 rows. Its 14 additions are
11 native-model successes and three independently recomputed deterministic LD
recoveries. The compressed checkpoint and evidence report are identical on both
hosts. Production may have advanced beyond this frozen checkpoint.

`catalog_path_comparison_v1.py` replaces lexical POSIX parent-membership and
relative-text calculations with equivalent component operations. It retains all
existing filesystem resolution, file reads, hashes and admission conditions;
it has no filesystem or verdict cache. Non-POSIX and outside-path behavior falls
back to the standard library. Forty-three boundary and regression checks pass.

An ABBA experiment froze enumeration of closed training collections, completed
recovery reports and the prior winner map. Every pass re-read original evidence
and cleared the existing memory-snapshot decode cache. The four full catalog
passes produced byte-identical 1,324 rows and two rejections, taking 173.399,
159.518, 151.890 and 176.548 seconds. Mean verification time decreased from
174.973 to 155.704 seconds (11.0%); this is a component timing experiment, not a
measurement of complete generation throughput. The predeclared operational
selection threshold was a speed ratio of at least 1.10, and the observed ratio
was 1.124. No model request or canonical-export write occurred in the benchmark.

The new real CLI plan and resume then passed with 1,334 verified snapshot rows
and 666 remaining identities. Supervisor v8 and collector/catalog v7 were
activated after the previous batch drained, at epoch `1788869788.8482554`.
Control directory is now `training_contract_memory_v6`; the unchanged recovery
service continues under V5. Provider configuration, request ledger, synthesis
prompt, reference retrieval and all six validators remain unchanged. New native
collection manifests bind the path-comparison policy and implementation hash.
Benchmark, preflight and activation receipts are retained under the V6 control
directory; historical source versions remain available and immutable.

## Completion-export handoff and continued observations

The first eight all-transfer batches admitted 69 native successes from 143 charged
requests. One additional LD visit ended before a request was charged: the visit
yield is therefore 69/144, while request yield is 69/143. ST yielded 47/72 requests
and LD 22/71. Deterministic recoveries remain excluded. The canonical export at
2026-09-08 13:10:35 UTC contains 1,360 implementations (845 ST, 515 LD), including
ten separately attributed LD recoveries. These remain correlated training
observations without a new independent control or billing comparison.

Supervisor V9 accepts a successfully exited collector V7's already completed full
export after checking its native summary, process/collection/source/configuration
identity, timestamp, canonical file digest, unique implementations and dual-language
coverage. Missing, stale or inconsistent evidence triggers the original full export.
Periodic supervisor exports during a running batch and its duplicate normal-end
export are removed; collector admission, initial export and fresh full verification
for every new plan remain. Canonical counts now refresh at batch completion.

Twenty-six boundary and control-flow tests passed. A native 1,360-row snapshot
replay took 0.463, 0.520 and 0.505 seconds for this handoff check; its original
terminal-status-to-export interval was 205.976 seconds. This is a component check,
not a measured end-to-end speedup. The first observer rejected a concurrently
changed snapshot; the second stopped on an empty planning log. Observer V3 retains
those failures, skips empty logs and waits for matching evidence and a terminated
process. It made no model call or production-export write.

Activation followed a normal V8 drain at epoch `1788873040.7631567`, preserving
269 charged Sonnet requests. The configuration, collector/catalog V7, generation
prompt, memory retrieval and separate recovery service remain unchanged. Control
directory is `training_contract_memory_v7`; actual completion handoffs are written
under `completed_exports/`. The first V9 batch subsequently admitted 10 native
successes from 18 charged requests, retaining six program failures and two
infrastructure errors. Its complete export finished at 13:25:16 UTC; the accepted
handoff was recorded 5.04 seconds later and the next fresh plan began after 5.19
seconds. Its receipt records `supervisor_full_export_skipped: true`. The live
catalog then contained 1,370 rows (853 ST, 517 LD), including ten derived LD rows.
The first nine all-transfer batches total 79/161 charged requests, with one
additional uncharged visit. This verifies removal of a duplicate export in
production; multi-batch end-to-end throughput remains to be measured.

An independent frozen delta checkpoint at `1788873185146200328` preserves all
1,334 preceding rows and verifies 25 additions: 23 native successes and two
independently recomputed LD repairs. It contains 1,359 rows, one fewer than the
then-live export. The interrupted local mirror is retained. Two concurrently
updated collection bookkeeping files (`index.jsonl`, `run_status.json`) were
excluded from transfer-content proof after confirming that neither is referenced
by any admitted row; no gate, input, source, configuration or ledger evidence was
excluded. All 10,985 retained transfer hashes and the independent admission audit
passed. Local archived export copies were losslessly compressed after checking
their remote originals; per-file restoration and hash receipts are preserved.

## Deterministic reuse of admitted ST programs, exploratory pilot

The separate ST-to-LD pilot translated four admitted ST programs without model
requests. All four passed the unchanged six portable gates and an independent
source/native-evidence audit. They also matched original ST outputs and retained
locals over 10,000 MatIEC C scans each with a corrected, zero-initialized driver;
a deliberate forced-output negative control was detected. The first driver's
uninitialized-storage failures remain preserved. These candidates are not yet
canonical admissions or fresh model-generated successes. Selection, source
versions, the additional symbol-boundary checks and the remaining integration
work are recorded in [the pilot result](st_translation_pilot_result_20260908.md).

The subsequent deployment started on Huashuo at 2026-09-08 15:33:54 UTC. A second,
two-case bulk trial passed the same six gates and initialized differential check.
Both trials use zero model calls. Catalog V8 binds the source ST, deterministic
translation, task authority, native validator inputs/results and C differential
artifacts before admitting an explicitly derived LD record. Derived records have
empty candidate usage and retain the source's historical usage separately.

Collector V8 and supervisor V10 defer paid LD visits only when a live free worker's
fresh claim matches the verified source ST. An expired/dead worker or failed free
validation releases work to the unchanged remaining Sonnet allowance. The free
worker uses two validation threads and closes batches of at most six; it does not
write the canonical export or call any model. The collector remains responsible
for full admission and export. Unsupported constructs fail closed. Missing
finalized validation contexts currently limit eligibility and are not counted as
converter failures. The first free-worker selection found 87 eligible tasks.

Fifty-seven checks, four native admission negatives and the revised full-catalog
preflight passed. The activation preserved all 1,445 existing successes and 449
charged Sonnet rows without changing the configuration or validation tools. The
first canonical export and live plan still require the separately saved
`training_contract_memory_v8/activation_export_audit.json` and
`activation_plan_audit.json`; consult these instead of inferring success from a
running service. The prior admission and startup/queue-identity diagnostics are
retained with the source snapshots that produced them.

The latest closed training observation is [recorded here](observations/20260908T152610Z/interpretation.md):
144 native successes from 305 charged all-transfer requests, with one additional
uncharged visit. It excludes every deterministic derivation. The original
exploratory control comparison remains 2/45 versus 12/45 and does not establish
independent generalization or stable monetary savings.
