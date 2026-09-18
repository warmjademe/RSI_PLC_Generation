from baseline_common.memory import freeze


def train(corpus, output, config, **kwargs):
    return freeze(output, method="FinalCodeRAG", records=corpus[1], corpus=corpus[0])
