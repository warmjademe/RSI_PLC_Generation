from baseline_common.memory import freeze


def train(corpus, output, config, **kwargs):
    return freeze(output, method="Vanilla", records=[], corpus=corpus[0], settings={"learning_calls": 0})
