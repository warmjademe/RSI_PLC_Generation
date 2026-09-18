"""Close output reference slices over persistent state across PLC scans.

This computes data dependencies in the supported scalar function-block subset.
It does not execute a training program or certify termination, arithmetic
definedness, source correctness, or transfer to a different public contract.
"""
from .repair_assets import backward_slice


def cyclic_output_slice(fields, nodes, outputs):
    targets = set(outputs)
    if not targets or any(name not in fields or fields[name]['direction'] != 'VAR_OUTPUT'
                          for name in targets):
        raise ValueError('declared outputs are required')
    rounds = 0
    while True:
        selected, entry = backward_slice(nodes, targets)
        rounds += 1
        persistent = {name for name in entry if fields[name]['direction'] in ('VAR', 'VAR_OUTPUT')}
        expanded = targets | persistent
        if expanded == targets:
            return selected, entry, {'retained_exit_targets': sorted(targets),
                                     'closure_iterations': rounds}
        targets = expanded
