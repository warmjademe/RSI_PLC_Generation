"""Select a stronger same-task repair starting point without claiming success."""
from baseline_common.utils import content_hash
from .repair_history import validate_history, feedback_for_code


def passed_prefix(feedback):
    stages = {row['stage']:row['status'] for row in feedback}
    count = 0
    for stage in ('compile','runtime','formal'):
        if stages.get(stage) != 'pass': break
        count += 1
    return count


def choose_seed(records, task, code, feedback):
    """Switch only when more initial stages have recorded passes; ties retain code.

    No failures are counted against different suites, unknown is never a pass,
    and the selected historical program needs a fresh complete validation.
    """
    validate_history(records, task)
    current = feedback_for_code(records, task, code, feedback)
    before = passed_prefix(current)
    selected_code, selected_feedback, score = code, current, before
    origin = None
    unique = {}
    for record in records:
        unique[record['code_hash']] = record
    for record in unique.values():
        if record['code_hash'] == content_hash(code): continue
        candidate_feedback = feedback_for_code(records, task, record['code'], [])
        candidate_score = passed_prefix(candidate_feedback)
        if candidate_score > score:
            selected_code, selected_feedback, score = record['code'], candidate_feedback, candidate_score
            origin = record['origin']
    decision = {'policy':'strictly_more_initial_recorded_stage_passes',
                'task_id':task['id'], 'previous_code_sha256':content_hash(code),
                'selected_code_sha256':content_hash(selected_code),
                'previous_passed_prefix':before, 'selected_passed_prefix':score,
                'switched':selected_code != code, 'historical_origin':origin,
                'fresh_validation_required':True, 'new_pass_claimed':False,
                'scope':'same-task same-contract continuation seed; no training asset update'}
    return selected_code, selected_feedback, decision
