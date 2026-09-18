from baseline_common.learning.protocol import digest
from baseline_common.memory import freeze


def train(corpus, output, config, **kwargs):
    # Fixed examples are chosen once per target without seeing any test query.
    count = int(config.get("shots", 2))
    if count < 1:
        raise ValueError("shots must be positive")
    examples = corpus[1]
    records = []
    for target in sorted({r["target"] for r in examples}):
        pool = [r for r in examples if r["target"] == target]
        pool.sort(key=lambda r: digest({"seed": config.get("seed", 20260910), "id": r["id"]}))
        records.extend(pool[:count])
    return freeze(output, method="FewShot", records=records, corpus=corpus[0], settings={"shots": count, "seed": config.get("seed", 20260910)})
