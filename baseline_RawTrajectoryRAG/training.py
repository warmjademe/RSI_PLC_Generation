from dataclasses import asdict
from baseline_common.memory import freeze


def train(corpus, output, config, **kwargs):
    records = []
    for ep in corpus[2]:
        record = asdict(ep)
        record.update(id=ep.task_id, metadata=ep.public_metadata)
        records.append(record)
    return freeze(output, method="RawTrajectoryRAG", records=records, corpus=corpus[0])
