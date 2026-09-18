"""Lexical prerequisite screening for induced mechanisms, without proof claims.

This optional screen derives its anchors from the frozen training proposal. It
cannot infer that a source's preconditions hold in a new program. Public words
and current diagnostic words are never stored back in the source assets.
"""
import re

from baseline_common.errors import ProtocolError
from .mechanism_retrieval import words

LEGACY = 'legacy_lexical_v1'
ANCHORED = 'mechanism_prerequisites_v1'
# Linguistic and IEC framing terms cannot provide positive mechanism evidence.
# Keep behavioral distinctions such as reset, stop, latch, fault and overflow.
FRAMING = set('''a an the and or not of to for in on at by as is are be been being
this that these those with without from into through within before after when
while then else otherwise only also both each every same once twice first second
any all other another such can could may might must shall should would will do
does did has have had their its our your new one two three more less using use
used uses provide provided implement implementation implementations program
programs function functions block blocks input inputs output outputs bool bools
int dint lint uint udint real lreal byte word dword true false type types typed
value values variable variables code source sources target targets training
successful verified verification required requirement requirements reference
references knowledge reusable reuse mechanism mechanisms behavior behaviour
pattern patterns procedure procedures step steps clause clauses instance
instances iec structured text plc scan scans candidate candidates observed
static runtime formal check checks future preserve confirm ensure exactly
belongs belonging supplied supplied common general generic relevant required
'''.split())
NORMAL = {'states': 'state', 'updates': 'update', 'updated': 'update', 'updating': 'update',
          'clears': 'clear', 'cleared': 'clear', 'clearing': 'clear',
          'latches': 'latch', 'latched': 'latch', 'latching': 'latch',
          'gates': 'gate', 'gated': 'gate', 'gating': 'gate',
          'resets': 'reset', 'resetting': 'reset', 'faults': 'fault',
          'enables': 'enable', 'enabled': 'enable', 'enabling': 'enable',
          'disables': 'disable', 'disabled': 'disable', 'disabling': 'disable',
          'isolates': 'isolate', 'isolated': 'isolate', 'isolation': 'isolate',
          'retains': 'retain', 'retained': 'retain', 'retention': 'retain',
          'executes': 'execute', 'executed': 'execute', 'execution': 'execute',
          'requests': 'request', 'requested': 'request', 'arbitration': 'arbiter'}


def mode(config):
    value = config.get('knowledge_selection_mode', LEGACY)
    if not isinstance(value, str) or value not in (LEGACY, ANCHORED):
        raise ProtocolError('unsupported knowledge applicability screen')
    return value


def content_terms(text):
    tokens = re.findall(r'[a-z]{3,}|[\u3400-\u9fff]{2}', words(text).lower())
    return {NORMAL.get(token, token) for token in tokens if token not in FRAMING}


def signature(proposal):
    """Use the declared mechanism and each associated source precondition.

    The full template text, source quotes, verification boilerplate and negative
    boundaries cannot create positive matches. Empty generic clauses impose no
    lexical requirement; the remaining clause groups are individually exposed.
    """
    anchors = content_terms(proposal['knowledge_need'])
    clauses = []
    for index, clause in enumerate(proposal['preconditions']):
        shared = anchors & content_terms(clause['statement'])
        if shared:
            clauses.append({'index': index, 'terms': sorted(shared)})
    return {'anchors': sorted(anchors), 'prerequisite_clauses': clauses}


def screen(proposal, public_text):
    learned = signature(proposal)
    public = content_terms(public_text)
    anchors = set(learned['anchors'])
    matched = anchors & public
    clauses = [{**clause, 'matched': sorted(set(clause['terms']) & public)}
               for clause in learned['prerequisite_clauses']]
    # Diagnostics may rank an already eligible asset, but cannot supply missing
    # public prerequisites or compensate for a generic public contract match.
    eligible = len(matched) >= 2 and bool(clauses) and all(clause['matched'] for clause in clauses)
    return {'eligible': eligible, 'matched_anchors': sorted(matched),
            'anchor_count': len(anchors), 'prerequisite_clauses': clauses,
            'semantic_applicability_established': False}
