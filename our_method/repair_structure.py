"""Exact typed alpha-matching for training repair antecedents.

This is a diagnostic prototype, not part of the frozen generation workflow.
Current code is a transient query: no query code, diagnostics or bindings are
written back to the index. A structural match is not semantic applicability.
"""
from collections import defaultdict
import copy

from baseline_common.errors import ProtocolError
from baseline_common.utils import object_hash
from .repair_assets import backward_slice, parsed_program
from .typed_fragments import rename, variables


def output_shape(fields, nodes, output):
    """Keep the complete output slice; canonicalize only variable identities."""
    if fields[output]['direction'] != 'VAR_OUTPUT':
        raise ValueError('an output is required')
    selected, entry = backward_slice(nodes, {output})
    if not selected:
        return None
    reads, writes = variables(selected)
    names = list(dict.fromkeys([output] + reads + writes))
    mapping = {name: 'v'+str(i) for i, name in enumerate(names)}
    shape = {'tree': rename(selected, mapping),
             'roles': [{'slot': mapping[name],
                        **{k: fields[name][k] for k in ('type', 'direction', 'initial')}}
                       for name in names],
             'entry_references': sorted(mapping[name] for name in entry)}
    return object_hash(shape), mapping


class RepairStructureIndex:
    """Index source structures only; after-only roles remain explicitly unbound."""
    def __init__(self, records):
        self.by_shape = defaultdict(list)
        self.assets_indexed = 0
        self.empty_antecedents = 0
        for asset in records:
            if asset.get('representation_version') != 'training_repair_slices_v2':
                continue
            ids = asset.get('source_task_ids', [])
            if not ids or any(not tid.startswith('TR_') for tid in ids):
                raise ProtocolError('structure index requires training-only sources')
            fields = {r['slot']: r for r in asset['roles']}
            shaped = output_shape(fields, asset['before_tree'], asset['output_role'])
            if shaped is None:
                self.empty_antecedents += 1
                continue
            key, mapping = shaped
            self.by_shape[key].append({'asset_id': asset['id'],
                'source_record_sha256': object_hash(asset),
                'source_role_to_canonical': mapping,
                'unbound_after_roles': sorted(set(fields) - set(mapping))})
            self.assets_indexed += 1

    def query(self, code):
        """Return matches without retaining the candidate or applying any edit.

        Unsupported syntax raises Unsupported. Callers must distinguish it
        from a supported program with no match, and must not trim syntax to
        obtain a match. The caller must additionally check public obligations,
        platform and source identity before any generation use.
        """
        fields, nodes = parsed_program(code)
        result = []
        for output, field in fields.items():
            if field['direction'] != 'VAR_OUTPUT':
                continue
            shaped = output_shape(fields, nodes, output)
            if shaped is None:
                continue
            key, mapping = shaped
            inverse = {slot: name for name, slot in mapping.items()}
            for source in self.by_shape.get(key, []):
                result.append({'asset_id': source['asset_id'],
                    'source_record_sha256': source['source_record_sha256'],
                    'current_output': output,
                    'role_binding': {slot: inverse[canonical]
                                     for slot, canonical in source['source_role_to_canonical'].items()},
                    'unbound_after_roles': list(source['unbound_after_roles']),
                    'typed_antecedent_sha256': key,
                    'semantic_applicability_established': False,
                    'automatic_patch_allowed': False})
        return copy.deepcopy(result)
