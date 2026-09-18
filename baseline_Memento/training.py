from baseline_common.learning.protocol import EvaluationProtocol
from .learning import MementoTrainer


def train(corpus, output, config, *, embedder, **kwargs):
    return MementoTrainer(protocol=EvaluationProtocol.from_config(config), embedder=embedder).train_and_freeze(
        corpus[2], output_root=output, corpus_binding=corpus[0], source_revision="st-comparison-v1")
