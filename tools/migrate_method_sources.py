"""One-time, non-destructive migration of existing local implementation sources."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
records = []


def copy(source, destination, replacements=()):
    target = ROOT / destination
    if target.exists():
        raise RuntimeError(f"Refusing to overwrite {target}")
    data = source.read_text()
    for old, new in replacements:
        data = data.replace(old, new)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data)
    records.append({"source": str(source.relative_to(ROOT.parent)), "destination": destination,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "migrated_sha256": hashlib.sha256(target.read_bytes()).hexdigest()})


def main():
    previous = ROOT.parent / "source_codes2"
    for source in sorted((previous / "baseline_common").glob("*.py")):
        copy(source, f"baseline_common/{source.name}")
    copy(previous / "VALIDATOR_PROTOCOL.md", "VALIDATOR_PROTOCOL.md")
    named = ROOT / "baselines/named_paper_baselines/src/named_plc_baselines"
    for name in ["artifacts.py", "embeddings.py", "lexical.py", "models.py"]:
        copy(named / name, f"baseline_common/learning/{name}")
    for folder, source in [("baseline_Memento", "memento.py"), ("baseline_EverMemOS", "evermemos.py"),
                           ("baseline_MemSkill", "memskill.py")]:
        copy(named / "methods" / source, f"{folder}/learning.py",
             [("from ..", "from baseline_common.learning."),
              ("TeamRouterDeepSeekFlash", "LearningProvider")])
    msce = ROOT / "baselines/msce_plc/src/msce_plc"
    for name in ["models.py", "storage.py", "prompts.py", "induction.py", "retrieval.py"]:
        copy(msce / name, f"baseline_MSCE/engine/{name}")
    for old, new in [("baselines/named_paper_baselines/README.md", "docs/previous_named_baselines.md"),
                     ("baselines/msce_plc/README.md", "docs/previous_msce.md"),
                     ("method_experiments/training_contract_transfer_v1/README.md", "our_method/PREVIOUS_METHOD_EVIDENCE.md")]:
        copy(ROOT / old, new)
    (ROOT / "MIGRATION_SOURCES.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(f"Migrated {len(records)} source files; originals retained")


if __name__ == "__main__":
    main()
