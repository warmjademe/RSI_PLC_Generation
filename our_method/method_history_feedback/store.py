"""Transactional task-boundary publication and immutable knowledge versions."""
import json
import sqlite3
from pathlib import Path

from baseline_common.errors import ProtocolError
from baseline_common.utils import Redactor, canonical_json, content_hash, object_hash


class KnowledgeStore:
    def __init__(self, path, manifest=None):
        self.path = Path(path)
        if manifest is None and not self.path.is_file():
            raise ProtocolError("knowledge store does not exist")
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        if manifest is not None:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (ordinal INTEGER PRIMARY KEY, task_id TEXT UNIQUE NOT NULL,
                    result_hash TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL,
                    task_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    ordinal INTEGER NOT NULL, claim_id TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('manifest', ?)", (canonical_json(manifest),))
            if self.manifest != manifest:
                raise ProtocolError("store belongs to a different study")

    @property
    def manifest(self):
        row = self.db.execute("SELECT payload FROM metadata WHERE key='manifest'").fetchone()
        if row is None:
            raise ProtocolError("uninitialized knowledge store")
        return json.loads(row[0])

    def close(self):
        self.db.close()

    def completed(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM tasks ORDER BY ordinal")]

    def snapshot(self, before_order):
        latest = {}
        revision = 0
        for row in self.db.execute("SELECT * FROM versions WHERE ordinal < ? ORDER BY sequence", (before_order,)):
            revision = row["sequence"]
            latest[row["claim_id"]] = json.loads(row["payload"])
        snapshot = {"before_task_order": before_order, "revision": revision,
                    "records": sorted(latest.values(), key=lambda r: r["id"])}
        return {**snapshot, "sha256": object_hash(snapshot)}

    def evidence(self, ids, before_order):
        out = {}
        for aid in sorted(set(ids)):
            row = self.db.execute("SELECT payload FROM attempts WHERE id=? AND ordinal < ?", (aid, before_order)).fetchone()
            if row:
                out[aid] = json.loads(row[0])
        return out

    def commit(self, bundle):
        """Result + all its observations + publication happen exactly once."""
        if Redactor().clean(bundle) != bundle:
            raise ProtocolError("knowledge publication contains a recognizable credential")
        ordinal, tid = bundle["order"], bundle["task_id"]
        if (type(ordinal) is not int or not 1 <= ordinal <= len(self.manifest["task_ids"])
                or self.manifest["task_ids"][ordinal - 1] != tid):
            raise ProtocolError("task publication is outside the frozen order")
        previous = self.db.execute("SELECT payload FROM tasks WHERE ordinal=?", (ordinal,)).fetchone()
        if previous:
            if json.loads(previous[0]) != bundle:
                raise ProtocolError("conflicting duplicate task publication")
            return False
        if len(self.completed()) != ordinal - 1:
            raise ProtocolError("earlier task has not committed; future knowledge is forbidden")
        available = self.evidence([e["attempt_id"] for u in bundle["updates"]
                                   for e in u.get("evidence", [])], ordinal)
        for attempt in bundle["attempts"]:
            if (attempt["order"] != ordinal or attempt["task_id"] != tid
                    or attempt["code_hash"] != content_hash(attempt["code"])):
                raise ProtocolError("attempt provenance mismatch")
            available[attempt["id"]] = attempt
        from .evidence import validate_citations
        for update in bundle["updates"]:
            validate_citations(update["evidence"], available)
            if update["published_after_order"] != ordinal:
                raise ProtocolError("knowledge version has a false publication time")
        with self.db:
            for attempt in bundle["attempts"]:
                self.db.execute("INSERT INTO attempts VALUES (?,?,?,?)", (
                    attempt["id"], ordinal, tid, canonical_json(attempt)))
            for update in bundle["updates"]:
                self.db.execute("INSERT INTO versions (ordinal,claim_id,payload) VALUES (?,?,?)", (
                    ordinal, update["id"], canonical_json(update)))
            self.db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (
                ordinal, tid, bundle["result_hash"], canonical_json(bundle)))
        return True
