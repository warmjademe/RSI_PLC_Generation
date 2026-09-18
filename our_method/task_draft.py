"""Bounded, unverified model draft for one unchanged task/code version."""
import copy

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from .repair_history import contract_hash


def make_draft(task, code, text, source, *, max_characters=4800):
    if not isinstance(text, str) or not text.strip() or max_characters < 500:
        raise ProtocolError('invalid unfinished draft')
    if len(text) <= max_characters:
        excerpt = text
    else:
        marker = '\n[Middle of unfinished draft omitted]\n'
        head = 400
        excerpt = text[:head]+marker+text[-(max_characters-head-len(marker)):]
    record = {'task_id': task['id'], 'contract_sha256': contract_hash(task),
              'base_code_sha256': content_hash(code), 'source': copy.deepcopy(source),
              'draft_sha256': content_hash(text), 'draft_characters': len(text),
              'excerpt': excerpt, 'excerpt_characters': len(excerpt),
              'middle_omitted': len(text) > max_characters, 'verified': False,
              'scope': 'unfinished model hypothesis for this task/code only; not a tool receipt or training asset'}
    record['id'] = 'task-draft:'+object_hash(record)[:24]
    return record


def draft_packet(record, task, code):
    if record is None:
        return None
    if record.get('id') != 'task-draft:'+object_hash({k: v for k, v in record.items() if k != 'id'})[:24]:
        raise ProtocolError('unfinished draft content hash mismatch')
    if record.get('task_id') != task['id'] or record.get('contract_sha256') != contract_hash(task):
        raise ProtocolError('unfinished draft belongs to another task or contract')
    if record.get('verified') is not False or len(record['excerpt']) != record['excerpt_characters'] or record['excerpt_characters'] > 4800:
        raise ProtocolError('unfinished draft verification or size marker is invalid')
    if record['base_code_sha256'] != content_hash(code):
        return None
    return copy.deepcopy(record)
