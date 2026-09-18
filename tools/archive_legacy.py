#!/usr/bin/env python3
"""Reversibly move superseded runs out of source_codes after delivery checks pass."""
import argparse
import gzip
import json
import os
from pathlib import Path
import stat
import time

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT.parent / "source_codes_archive/20260910_pre_st_comparison"
TARGETS = ["baselines", "harness_evolution", "method_experiments", "model_smoke_tests",
           "native_ld_v6_calibration", "semantic_contract_harness", "semantic_ir_v6", "track_router_v7",
           "training_datasets/history_plc_feedback_paths", "training_datasets/plc_feedback_paths_generators"]


def runtime_references(paths):
    references = []
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            args = (process / "cmdline").read_bytes().split(b"\0")
            cwd = os.readlink(process / "cwd")
            comm = (process / "comm").read_text().strip()
            env = (process / "environ").read_bytes().split(b"\0")
            selected_env = [x.split(b"=", 1)[1].decode(errors="replace") for x in env
                            if x.startswith((b"VIRTUAL_ENV=", b"PYTHONPATH="))]
            links = []
            for fd in (process / "fd").iterdir():
                try:
                    links.append(os.readlink(fd))
                except OSError:
                    pass
        except (OSError, PermissionError):
            continue
        values = [cwd] + [a.decode(errors="replace") for a in args] + links
        for path in paths:
            prefix = str(path)
            if any(v == prefix or v.startswith(prefix + "/") for v in values) or any(prefix in v for v in selected_env):
                references.append({"pid": int(process.name), "process": comm, "target": str(path.relative_to(ROOT))})
    return references


def file_inventory(root):
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in files + [n for n in dirs if (Path(base) / n).is_symlink()]:
            path = Path(base) / name
            st = path.lstat()
            yield {"path": str(path.relative_to(root)), "bytes": st.st_size, "inode": st.st_ino,
                   "device": st.st_dev, "mode": stat.S_IMODE(st.st_mode),
                   "symlink": os.readlink(path) if path.is_symlink() else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = [ROOT / name for name in TARGETS if (ROOT / name).exists()]
    active = runtime_references(plan)
    if active:
        print(json.dumps({"status": "blocked_active_references", "references": active}, ensure_ascii=False, indent=2))
        return 1
    if not args.apply:
        print(json.dumps({"status": "plan_ready", "targets": [str(p.relative_to(ROOT)) for p in plan], "archive": str(ARCHIVE)}, ensure_ascii=False, indent=2))
        return 0
    validation = json.loads((ROOT / "DELIVERY_VALIDATION.json").read_text())
    if validation.get("status") != "pass" or not validation.get("real_corpus_asset_builds"):
        raise RuntimeError("Complete code and real-corpus delivery checks must pass before cleanup")
    ARCHIVE.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {"status": "in_progress", "timestamp_epoch": int(time.time()), "action": "same_filesystem_archive_move",
              "archive_root": str(ARCHIVE), "deleted_files": 0, "disk_space_freed_bytes": 0, "moves": []}
    report_path = ROOT / "CLEANUP_REPORT.json"
    for source in plan:
        relative = str(source.relative_to(ROOT))
        destination = ARCHIVE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = list(file_inventory(source))
        # Atomic rename preserves contents and inodes, including uncommitted edits.
        source.rename(destination)
        after = list(file_inventory(destination))
        if sorted(before, key=lambda r: r["path"]) != sorted(after, key=lambda r: r["path"]):
            raise RuntimeError(f"Archive inventory changed: {relative}")
        inventory_path = ARCHIVE / (relative.replace("/", "__") + ".inventory.jsonl.gz")
        with gzip.open(inventory_path, "wt") as f:
            for row in before:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        report["moves"].append({"source": relative, "destination": str(destination), "file_count": len(before),
                                "logical_bytes": sum(r["bytes"] for r in before), "inode_size_mode_inventory": "identical",
                                "inventory": str(inventory_path)})
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    # Preserve the pre-existing exposure-evidence link used by the immutable test package.
    evidence = ROOT / "semantic_contract_harness/README.md"
    evidence.parent.mkdir()
    evidence.write_text("# 历史方法文档索引（旧代码已归档）\n\n"
                        "原 `semantic_contract_harness` 的代码、配置与实验材料已移至：\n\n"
                        f"`{ARCHIVE / 'semantic_contract_harness'}`\n\n"
                        "原 README 记录：当前 100 题属于已经打开过的设计反馈集，不能再称为独立密封测试。\n\n"
                        "本文件保留 `test_dataset` 的历史证据链接；它不是新方法运行入口。"
                        "当前方法见 `../our_method/README.md`。\n")
    report.update(status="complete", total_files_moved=sum(r["file_count"] for r in report["moves"]),
                  total_logical_bytes_moved=sum(r["logical_bytes"] for r in report["moves"]),
                  preserved=["final_train_datasets", "test_dataset", "training_datasets/plc_rsi_1000_100_v1", "infrastructure"],
                  retained_legacy_link="semantic_contract_harness/README.md")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (ARCHIVE / "RESTORE.md").write_text("# 旧实验恢复说明\n\n"
        "本目录是同文件系统移动形成的归档；没有删除原始成功/失败记录。"
        "各 inventory 文件记录原相对路径、字节数、inode 与权限，移动前后完全一致。\n\n"
        "恢复时先确认原目标没有新文件或运行进程，再按 source_codes/CLEANUP_REPORT.json 的 source/destination 逐项反向移动。"
        "不要覆盖当前新代码。semantic_contract_harness 原位置有一个新建的 README 索引，应先另存该索引再恢复旧目录。\n")
    print(json.dumps({k: report[k] for k in ["status", "archive_root", "total_files_moved", "total_logical_bytes_moved", "deleted_files"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
