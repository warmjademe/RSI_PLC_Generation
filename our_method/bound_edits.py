"""Apply simultaneous, unambiguous edits bound to one exact candidate version."""
from baseline_common.errors import ModelResponseError
from baseline_common.utils import content_hash


def apply_revision(base, response):
    if response.get('base_code_sha256') != content_hash(base):
        raise ModelResponseError('base_code_sha256 must equal the supplied current code hash')
    if ('code' in response) == ('edits' in response):
        raise ModelResponseError('return exactly one of edits or complete code')
    if 'code' in response:
        result = response['code']
        if not isinstance(result, str) or not result.strip():
            raise ModelResponseError('replacement code must be a nonempty string')
        return result, {'representation': 'complete_code', 'edit_count': None}
    edits = response['edits']
    if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
        raise ModelResponseError('edits must contain one to twelve replacements')
    located = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'old', 'new'}:
            raise ModelResponseError('each edit must have exactly old and new strings')
        old, new = edit['old'], edit['new']
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ModelResponseError('old must be nonempty; new must be a string')
        start = base.find(old)
        if start < 0 or base.find(old, start + 1) >= 0:
            raise ModelResponseError('old must match exactly once in the original code; include more surrounding context')
        located.append((start, start + len(old), new))
    located.sort()
    if any(left[1] > right[0] for left, right in zip(located, located[1:])):
        raise ModelResponseError('edits overlap; combine them into a single replacement')
    result = base
    for start, end, new in reversed(located):
        result = result[:start] + new + result[end:]
    if not result.strip():
        raise ModelResponseError('edits removed the complete program')
    return result, {'representation': 'simultaneous_exact_edits', 'edit_count': len(edits)}
