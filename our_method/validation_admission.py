"""Share four validator admissions before starting any verifier timeout."""
import contextlib
import fcntl
from pathlib import Path
import time

from baseline_common.errors import BudgetExceeded, ProtocolError


@contextlib.contextmanager
def admitted(config, timeout):
    if config.get('slots') != 4:
        raise ProtocolError('the qualified validation pool has four slots')
    root = Path(config['directory']); root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); handles = []; selected = None
    try:
        handles = [(root/f'slot-{i}.lock').open('a') for i in range(4)]
        while selected is None:
            if time.monotonic()-started >= timeout:
                raise BudgetExceeded('task wall budget exhausted waiting for validation admission')
            for i, handle in enumerate(handles):
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    selected = i
                    break
                except BlockingIOError:
                    pass
            if selected is None:
                time.sleep(min(.25, max(0, timeout-(time.monotonic()-started))))
        yield {'slots': 4, 'slot': selected, 'queue_seconds': time.monotonic()-started,
               'queue_in_verifier_timeout': False, 'queue_in_task_wall_budget': True}
    finally:
        for handle in handles:
            handle.close()
