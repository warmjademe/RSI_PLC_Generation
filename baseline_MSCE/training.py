from pathlib import Path
import json
from baseline_common.datasets import write_json
from baseline_common.utils import object_hash
from .engine.induction import InductionEngine, InductionError, backfill_values, validate_reflection
from .engine.models import Task, with_evaluation_scope
from .engine.provider import OpenAIChatProvider
from .engine.storage import MemoryStore


REFLECTION_SYSTEM = (
    'Reflect on the supplied recorded PLC training episode. Return a JSON object with '
    'summary, causal_diagnosis, reusable_lesson, boundary, and scores. '
    'scores MUST be a nested object containing correctness, specificity, evidence_grounding, '
    'and reusability, each a numeric value in [0,1]. Do not put those four keys at the top level. '
    'Do not claim a causal repair benefit merely because the terminal outcome passed. '
    'Final-artifact-only episodes have no observed repair chain.'
)


def episode_reflection(ep, output, provider):
    trace = ep.compact_trace()
    binding = object_hash(trace)
    directory = output/'reflection_journal'/ep.task_id
    directory.mkdir(parents=True, exist_ok=True)
    complete = directory/'complete.json'
    if complete.exists():
        saved = json.loads(complete.read_text())
        if saved['episode_sha256'] != binding or saved['reflection_sha256'] != object_hash(saved['reflection']):
            raise InductionError('reflection journal binding or content changed')
        return validate_reflection(saved['reflection'])
    messages = [{'role':'system','content':REFLECTION_SYSTEM},
                {'role':'user','content':json.dumps(trace,ensure_ascii=False)}]
    error = None
    existing = [int(p.stem.split('-')[-1]) for p in directory.glob('response-*.json')]
    start = max(existing, default=0)+1
    for attempt in range(start,start+3):
        document, call = provider.json_chat(messages)
        receipt = {'episode_sha256':binding,'raw_document':document,'model_audit':call.sanitized()}
        try:
            reflection = validate_reflection(document)
        except InductionError as exc:
            receipt.update(status='schema_rejected',error=str(exc))
            write_json(directory/f'response-{attempt:04d}.json',receipt)
            error = exc
            messages = [messages[0], messages[1], {'role':'user','content':
                'The previous response failed schema validation: '+str(exc)+
                '. Return the complete reflection again with all required fields and the nested scores object. '
                'Ground every value in the supplied episode; do not supply default scores.'}]
            continue
        receipt.update(status='accepted',normalized=reflection != document)
        write_json(directory/f'response-{attempt:04d}.json',receipt)
        write_json(complete, {'episode_sha256':binding,'reflection':reflection,
                             'reflection_sha256':object_hash(reflection),'response_file':f'response-{attempt:04d}.json'})
        return reflection
    raise error


def train(corpus, output, config, *, provider, **kwargs):
    output.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(output / "memory.sqlite3")
    # The experiment controller binds the input/config before recovery. Resume
    # committed reflections instead of paying to generate the same episode again.
    with store.connect() as connection:
        completed = {row["task_id"] for row in connection.execute("SELECT task_id FROM episodes WHERE split='train'")}
    for ep in corpus[2]:
        if ep.task_id in completed:
            continue
        reflection = episode_reflection(ep, output, provider)
        task = with_evaluation_scope(Task(ep.task_id, "train", Path("."), None, ep.requirement, ep.interface, ep.public_metadata),
                                     target=ep.target, output_language="st")
        values = backfill_values(len(ep.attempts), ep.reward)
        trace = [{"step_index": i + 1, "state": {"requirement": ep.requirement, "interface": ep.interface},
                  "action": {"candidate_st": a.candidate_st, "repair_mode": a.repair_mode},
                  "observation": {"feedback": a.feedback}, "backed_value": values[i]}
                 for i, a in enumerate(ep.attempts)]
        store.add_episode(split="train", task_id=ep.task_id, signature=task.signature,
                          retrieval_text=task.retrieval_text, terminal_status="pass" if ep.success else "fail",
                          terminal_reward=ep.reward, reflection=reflection,
                          feedback_kinds=sorted({g.get("name", "") for a in ep.attempts for g in a.feedback}),
                          policy_ids=[], skill_ids=[], trace_steps=trace)
    operators = InductionEngine(store, OpenAIChatProvider(provider), config.get("induction", {}))
    if (output / "induction.json").exists():
        audit = __import__('json').loads((output / "induction.json").read_text())
    else:
        audit = operators.induce()
        write_json(output / "induction.json", audit)
    write_json(output / "counts.json", store.counts())
    # No invented policy invocations: without measured gain, the original skill
    # crystallization gate remains closed. Report zero skills if that is the result.
    return {"method": "MSCE", "counts": store.counts(), "induction_errors": audit["errors"],
            "skill_admission": "original_policy_gain_gate; no fabricated invocation evidence"}
