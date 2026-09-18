from dataclasses import dataclass
from pathlib import Path
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class EvaluationProtocol:
    source_path: Path
    document: dict

    @property
    def sha256(self):
        return digest(self.document)

    @classmethod
    def from_config(cls, config):
        return cls(Path("explicit_configuration"), {
            "schema_version": 2, "languages": ["st"], "learning_during_test": False,
            "base_model_weights_updated": False,
            "maximum_memory_context_characters": int(config.get("memory_characters", 24000)),
        })
