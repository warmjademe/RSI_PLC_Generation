from .protocol import digest


def deterministic_development_partition(episodes, *, validation_modulus=5):
    if validation_modulus < 2:
        raise ValueError("validation_modulus must be at least two")
    train, validation = [], []
    for episode in episodes:
        bucket = int(digest({"task_id": episode.task_id, "partition": "named_baseline_development_v1"})[:16], 16)
        (validation if bucket % validation_modulus == 0 else train).append(episode)
    if not train or not validation:
        raise ValueError("Development partition has an empty side")
    return tuple(train), tuple(validation)
