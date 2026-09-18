from baseline_common.learning.protocol import EvaluationProtocol
from .learning import MemSkillTrainer


def train(corpus, output, config, *, embedder, provider, **kwargs):
    return MemSkillTrainer(protocol=EvaluationProtocol.from_config(config), embedder=embedder, provider=provider,
                           device=config.get("controller_device", "cpu")).train_and_freeze(
        corpus[2], output_root=output, corpus_binding=corpus[0], source_revision="st-comparison-v1", expected_count=len(corpus[2]))
