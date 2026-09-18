from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS run_metadata (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_results (
    split TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    generation_attempts INTEGER NOT NULL,
    feedback_count INTEGER NOT NULL,
    infrastructure_retries INTEGER NOT NULL DEFAULT 0,
    worker_ids_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(split, task_id)
);

CREATE TABLE IF NOT EXISTS trajectory_imports (
    collection_id TEXT NOT NULL,
    run_key TEXT NOT NULL,
    task_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    imported_at TEXT NOT NULL,
    PRIMARY KEY(collection_id, run_key),
    UNIQUE(collection_id, task_id),
    UNIQUE(episode_id)
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    split TEXT NOT NULL,
    task_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    retrieval_text TEXT NOT NULL,
    terminal_status TEXT NOT NULL,
    terminal_reward REAL,
    reflection_json TEXT NOT NULL,
    feedback_kinds_json TEXT NOT NULL,
    policy_ids_json TEXT NOT NULL,
    skill_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    step_index INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    reflection_json TEXT NOT NULL,
    backed_value REAL,
    UNIQUE(episode_id, step_index)
);

CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    procedure_json TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    boundary_json TEXT NOT NULL,
    evidence_episode_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    induction_model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    retired INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS policy_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL REFERENCES policies(id),
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    outcome REAL,
    terminal_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(policy_id, episode_id)
);

CREATE TABLE IF NOT EXISTS policy_evidence_links (
    policy_id INTEGER NOT NULL REFERENCES policies(id),
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(policy_id, episode_id)
);

CREATE TABLE IF NOT EXISTS cognition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement TEXT NOT NULL,
    applicability_json TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    boundary_json TEXT NOT NULL,
    evidence_policy_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    induction_model TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL UNIQUE REFERENCES policies(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    procedure_json TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    boundary_json TEXT NOT NULL,
    evidence_episode_ids_json TEXT NOT NULL,
    gain_json TEXT NOT NULL,
    induction_model TEXT NOT NULL,
    pass_count INTEGER NOT NULL DEFAULT 0,
    trial_count INTEGER NOT NULL DEFAULT 0,
    reliability REAL NOT NULL DEFAULT 0.5,
    state TEXT NOT NULL DEFAULT 'probation',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL REFERENCES skills(id),
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    passed INTEGER,
    terminal_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(skill_id, episode_id)
);
"""


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            # Pilot databases created before model provenance was mandatory
            # remain readable, but are explicitly labelled as legacy rather
            # than being silently attributed to the current model.
            skill_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(skills)")
            }
            if "induction_model" not in skill_columns:
                connection.execute(
                    """ALTER TABLE skills ADD COLUMN induction_model TEXT
                       NOT NULL DEFAULT 'unknown-legacy'"""
                )

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_metadata(self, key: str, value: Any) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO run_metadata(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key, _json(value)),
            )

    def get_metadata(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute("SELECT value_json FROM run_metadata WHERE key=?", (key,)).fetchone()
        return _loads(row["value_json"], default) if row else default

    def completed_task_ids(self, split: str) -> set[str]:
        """Return tasks with a model-level terminal verdict.

        Infrastructure failures are deliberately stored as ``inconclusive`` for
        auditability, but they remain pending when the same run is resumed.
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT task_id FROM task_results WHERE split=? AND status IN ('pass','fail')",
                (split,),
            ).fetchall()
        return {row["task_id"] for row in rows}

    def record_task_result(self, split: str, task_id: str, result: dict[str, Any]) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO task_results(
                    split,task_id,status,generation_attempts,feedback_count,infrastructure_retries,
                    worker_ids_json,result_json,completed_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    split,
                    task_id,
                    result["status"],
                    int(result["generation_attempts"]),
                    int(result["feedback_count"]),
                    int(result.get("infrastructure_retries", 0)),
                    _json(result.get("worker_ids", [])),
                    _json(result),
                    utc_now(),
                ),
            )

    @staticmethod
    def _insert_episode(
        connection: sqlite3.Connection,
        *,
        split: str,
        task_id: str,
        signature: str,
        retrieval_text: str,
        terminal_status: str,
        terminal_reward: float | None,
        reflection: dict[str, Any],
        feedback_kinds: list[str],
        policy_ids: list[int],
        skill_ids: list[int],
        trace_steps: list[dict[str, Any]],
    ) -> int:
        cursor = connection.execute(
                """INSERT INTO episodes(
                    split,task_id,signature,retrieval_text,terminal_status,terminal_reward,
                    reflection_json,feedback_kinds_json,policy_ids_json,skill_ids_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    split, task_id, signature, retrieval_text, terminal_status, terminal_reward,
                    _json(reflection), _json(feedback_kinds), _json(policy_ids), _json(skill_ids), utc_now(),
                ),
            )
        episode_id = int(cursor.lastrowid)
        for step in trace_steps:
            connection.execute(
                    """INSERT INTO trace_steps(
                        episode_id,step_index,state_json,action_json,observation_json,reflection_json,backed_value
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        episode_id,
                        int(step["step_index"]),
                        _json(step["state"]),
                        _json(step["action"]),
                        _json(step["observation"]),
                        _json(step.get("reflection", {})),
                        step.get("backed_value"),
                    ),
                )
        for policy_id in sorted(set(policy_ids)):
            connection.execute(
                    "INSERT OR IGNORE INTO policy_invocations(policy_id,episode_id,outcome,terminal_status,created_at) VALUES(?,?,?,?,?)",
                    (policy_id, episode_id, terminal_reward, terminal_status, utc_now()),
                )
        for skill_id in sorted(set(skill_ids)):
            passed = None if terminal_reward is None else int(terminal_reward > 0.0)
            connection.execute(
                    "INSERT OR IGNORE INTO skill_invocations(skill_id,episode_id,passed,terminal_status,created_at) VALUES(?,?,?,?,?)",
                    (skill_id, episode_id, passed, terminal_status, utc_now()),
                )
            if passed is not None:
                row = connection.execute("SELECT pass_count,trial_count FROM skills WHERE id=?", (skill_id,)).fetchone()
                if row:
                    pass_count = int(row["pass_count"]) + passed
                    trial_count = int(row["trial_count"]) + 1
                    reliability = (pass_count + 1) / (trial_count + 2)
                    state = "active" if trial_count >= 1 and reliability >= 0.6 else "probation"
                    if reliability < 0.2:
                        state = "archived"
                    connection.execute(
                            "UPDATE skills SET pass_count=?,trial_count=?,reliability=?,state=?,updated_at=? WHERE id=?",
                            (pass_count, trial_count, reliability, state, utc_now(), skill_id),
                        )
        return episode_id

    def add_episode(
        self,
        *,
        split: str,
        task_id: str,
        signature: str,
        retrieval_text: str,
        terminal_status: str,
        terminal_reward: float | None,
        reflection: dict[str, Any],
        feedback_kinds: list[str],
        policy_ids: list[int],
        skill_ids: list[int],
        trace_steps: list[dict[str, Any]],
    ) -> int:
        with self._lock, self.connect() as connection:
            return self._insert_episode(
                connection,
                split=split,
                task_id=task_id,
                signature=signature,
                retrieval_text=retrieval_text,
                terminal_status=terminal_status,
                terminal_reward=terminal_reward,
                reflection=reflection,
                feedback_kinds=feedback_kinds,
                policy_ids=policy_ids,
                skill_ids=skill_ids,
                trace_steps=trace_steps,
            )

    def import_trajectory_episode(
        self,
        *,
        collection_id: str,
        run_key: str,
        manifest_sha256: str,
        episode: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[int, bool]:
        """Atomically import one immutable common trajectory.

        The collection/run identity makes interrupted imports resumable without
        duplicating L1 evidence. Reusing an identity with another manifest or
        task result fails closed.
        """

        task_id = str(episode["task_id"])
        with self._lock, self.connect() as connection:
            existing = connection.execute(
                """SELECT ti.task_id,ti.manifest_sha256,ti.episode_id,
                          tr.result_json
                   FROM trajectory_imports ti
                   JOIN task_results tr
                     ON tr.split='train' AND tr.task_id=ti.task_id
                   WHERE ti.collection_id=? AND ti.run_key=?""",
                (collection_id, run_key),
            ).fetchone()
            if existing is not None:
                expected_result = {
                    **result,
                    "episode_id": int(existing["episode_id"]),
                }
                if (
                    str(existing["task_id"]) != task_id
                    or str(existing["manifest_sha256"]) != manifest_sha256
                    or _loads(existing["result_json"], {}) != expected_result
                ):
                    raise ValueError(
                        "trajectory import identity was reused with different evidence"
                    )
                return int(existing["episode_id"]), False

            conflicting = connection.execute(
                """SELECT collection_id,run_key,manifest_sha256
                   FROM trajectory_imports
                   WHERE collection_id=? AND task_id=?""",
                (collection_id, task_id),
            ).fetchone()
            if conflicting is not None:
                raise ValueError(
                    "trajectory collection contains duplicate task evidence"
                )

            episode_id = self._insert_episode(
                connection,
                split="train",
                task_id=task_id,
                signature=str(episode["signature"]),
                retrieval_text=str(episode["retrieval_text"]),
                terminal_status=str(episode["terminal_status"]),
                terminal_reward=episode.get("terminal_reward"),
                reflection=dict(episode.get("reflection", {})),
                feedback_kinds=list(episode.get("feedback_kinds", [])),
                policy_ids=[],
                skill_ids=[],
                trace_steps=list(episode["trace_steps"]),
            )
            result_document = {**result, "episode_id": episode_id}
            connection.execute(
                """INSERT INTO task_results(
                    split,task_id,status,generation_attempts,feedback_count,
                    infrastructure_retries,worker_ids_json,result_json,completed_at
                ) VALUES('train',?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    str(result_document["status"]),
                    int(result_document["generation_attempts"]),
                    int(result_document["feedback_count"]),
                    int(result_document.get("infrastructure_retries", 0)),
                    _json(result_document.get("worker_ids", [])),
                    _json(result_document),
                    str(result_document.get("completed_at") or utc_now()),
                ),
            )
            connection.execute(
                """INSERT INTO trajectory_imports(
                    collection_id,run_key,task_id,manifest_sha256,episode_id,imported_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    collection_id,
                    run_key,
                    task_id,
                    manifest_sha256,
                    episode_id,
                    utc_now(),
                ),
            )
        return episode_id, True

    def episodes_for_signature(self, signature: str, min_value: float, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM episodes
                   WHERE split='train' AND signature=? AND terminal_reward IS NOT NULL AND terminal_reward>=?
                   ORDER BY id DESC LIMIT ?""",
                (signature, min_value, limit),
            ).fetchall()
        return [self._episode(row) for row in rows]

    def recent_episodes(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._episode(row) for row in rows]

    def _episode(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "split": row["split"],
            "task_id": row["task_id"],
            "signature": row["signature"],
            "retrieval_text": row["retrieval_text"],
            "terminal_status": row["terminal_status"],
            "terminal_reward": row["terminal_reward"],
            "reflection": _loads(row["reflection_json"], {}),
            "feedback_kinds": _loads(row["feedback_kinds_json"], []),
            "policy_ids": _loads(row["policy_ids_json"], []),
            "skill_ids": _loads(row["skill_ids_json"], []),
        }

    def policies(self, include_retired: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM policies" + ("" if include_retired else " WHERE retired=0") + " ORDER BY id DESC"
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [{
            "id": row["id"], "signature": row["signature"],
            "trigger": _loads(row["trigger_json"], {}),
            "procedure": _loads(row["procedure_json"], []),
            "verification": _loads(row["verification_json"], []),
            "boundary": _loads(row["boundary_json"], []),
            "evidence_episode_ids": _loads(row["evidence_episode_ids_json"], []),
            "confidence": row["confidence"], "induction_model": row["induction_model"],
        } for row in rows]

    def add_policy(self, signature: str, document: dict[str, Any], model: str) -> int:
        with self._lock, self.connect() as connection:
            evidence_episode_ids = sorted({
                int(item) for item in document["evidence_episode_ids"]
            })
            if not evidence_episode_ids:
                raise ValueError("policy evidence is empty")
            placeholders = ",".join("?" for _ in evidence_episode_ids)
            evidence_rows = connection.execute(
                f"""SELECT id,terminal_reward FROM episodes
                    WHERE id IN ({placeholders}) AND split='train'
                      AND terminal_reward IS NOT NULL""",
                evidence_episode_ids,
            ).fetchall()
            if {int(row["id"]) for row in evidence_rows} != set(
                evidence_episode_ids
            ):
                raise ValueError(
                    "policy evidence must reference persisted conclusive training episodes"
                )
            cursor = connection.execute(
                """INSERT INTO policies(signature,trigger_json,procedure_json,verification_json,boundary_json,
                   evidence_episode_ids_json,confidence,induction_model,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    signature, _json(document["trigger"]), _json(document["procedure"]),
                    _json(document["verification"]), _json(document["boundary"]),
                    _json(evidence_episode_ids), float(document["confidence"]), model, utc_now(),
                ),
            )
            policy_id = int(cursor.lastrowid)
            for episode_id in evidence_episode_ids:
                connection.execute(
                    """INSERT INTO policy_evidence_links(
                        policy_id,episode_id,created_at
                    ) VALUES(?,?,?)""",
                    (policy_id, episode_id, utc_now()),
                )
            return policy_id

    def policy_gain_observations(self, policy_id: int) -> dict[str, list[float]]:
        with self.connect() as connection:
            with_rows = connection.execute(
                """SELECT DISTINCT e.id,e.terminal_reward AS outcome
                   FROM episodes e
                   JOIN (
                       SELECT episode_id FROM policy_evidence_links WHERE policy_id=?
                       UNION
                       SELECT episode_id FROM policy_invocations WHERE policy_id=?
                   ) linked ON linked.episode_id=e.id
                   WHERE e.terminal_reward IS NOT NULL""",
                (policy_id, policy_id),
            ).fetchall()
            policy = connection.execute("SELECT signature FROM policies WHERE id=?", (policy_id,)).fetchone()
            without_rows = []
            if policy:
                without_rows = connection.execute(
                    """SELECT terminal_reward FROM episodes
                       WHERE split='train' AND signature=? AND terminal_reward IS NOT NULL
                       AND id NOT IN (
                           SELECT episode_id FROM policy_evidence_links WHERE policy_id=?
                           UNION
                           SELECT episode_id FROM policy_invocations WHERE policy_id=?
                       )""",
                    (policy["signature"], policy_id, policy_id),
                ).fetchall()
        return {
            "with": [float(row["outcome"]) for row in with_rows],
            "without": [float(row[0]) for row in without_rows],
        }

    def cognition(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM cognition ORDER BY id DESC").fetchall()
        return [{
            "id": row["id"], "statement": row["statement"],
            "applicability": _loads(row["applicability_json"], []),
            "verification": _loads(row["verification_json"], []),
            "boundary": _loads(row["boundary_json"], []),
            "evidence_policy_ids": _loads(row["evidence_policy_ids_json"], []),
            "confidence": row["confidence"],
            "induction_model": row["induction_model"],
        } for row in rows]

    def add_cognition(self, document: dict[str, Any], model: str) -> int:
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO cognition(statement,applicability_json,verification_json,boundary_json,
                   evidence_policy_ids_json,confidence,induction_model,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(document["statement"]), _json(document["applicability"]),
                    _json(document["verification"]), _json(document["boundary"]),
                    _json(document["evidence_policy_ids"]), float(document["confidence"]), model, utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def skills(self, states: tuple[str, ...] = ("active", "probation")) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in states)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM skills WHERE state IN ({placeholders}) ORDER BY reliability DESC,id DESC",
                states,
            ).fetchall()
        return [{
            "id": row["id"], "policy_id": row["policy_id"], "name": row["name"],
            "description": row["description"], "trigger": _loads(row["trigger_json"], {}),
            "procedure": _loads(row["procedure_json"], []),
            "verification": _loads(row["verification_json"], []),
            "boundary": _loads(row["boundary_json"], []),
            "evidence_episode_ids": _loads(row["evidence_episode_ids_json"], []),
            "gain": _loads(row["gain_json"], {}), "pass_count": row["pass_count"],
            "trial_count": row["trial_count"], "reliability": row["reliability"], "state": row["state"],
            "induction_model": row["induction_model"],
        } for row in rows]

    def add_skill(
        self,
        policy_id: int,
        document: dict[str, Any],
        gain: dict[str, Any],
        model: str,
    ) -> int:
        if not str(model).strip():
            raise ValueError("skill induction model is empty")
        evidence = list(document.get("evidence_episode_ids", []))[:6]
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO skills(policy_id,name,description,trigger_json,procedure_json,verification_json,
                   boundary_json,evidence_episode_ids_json,gain_json,induction_model,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    policy_id, str(document["name"]), str(document["description"]),
                    _json(document["trigger"]), _json(document["procedure"]),
                    _json(document["verification"]), _json(document["boundary"]),
                    _json(evidence), _json(gain), str(model), utc_now(), utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def task_results(self, split: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT result_json FROM task_results WHERE split=? ORDER BY task_id", (split,)).fetchall()
        return [_loads(row["result_json"], {}) for row in rows]

    def counts(self) -> dict[str, int]:
        names = ("episodes", "trace_steps", "policies", "cognition", "skills")
        with self.connect() as connection:
            return {name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}
