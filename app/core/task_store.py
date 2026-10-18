"""任务持久化层（v1：SQLite，stdlib sqlite3，零额外依赖）。

设计要点：
- Store 只负责"读写"，不负责"合法性"（状态合法性由状态机保证）。
  这样 v2 换 Redis/Postgres 时，workflow 代码一行不用动。
- 三张表：tasks（主体）/ steps（步骤历史，含重试）/ task_events（生命周期留痕）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.models import TaskRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
    id           TEXT PRIMARY KEY,
    topic        TEXT NOT NULL,
    product_info TEXT NOT NULL DEFAULT '',
    scene_count  INTEGER NOT NULL DEFAULT 3,
    status       TEXT NOT NULL,
    current_step TEXT,
    error        TEXT,
    warnings     TEXT NOT NULL DEFAULT '[]',
    artifacts    TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 1,
    output      TEXT,
    error       TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS task_events(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    ts      TEXT NOT NULL,
    step    TEXT,
    message TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------- tasks ----------------

    def create_task(self, rec: TaskRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks(id, topic, product_info, scene_count, status,"
                " current_step, error, warnings, artifacts, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.id, rec.topic, rec.product_info, rec.scene_count, rec.status,
                    rec.current_step, rec.error,
                    json.dumps(rec.warnings, ensure_ascii=False),
                    json.dumps(rec.artifacts, ensure_ascii=False),
                    rec.created_at, rec.updated_at,
                ),
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._to_record(row) if row else None

    def list_tasks(self, limit: int = 50) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_record(r) for r in rows]

    def list_unfinished_tasks(self) -> list[TaskRecord]:
        """启动恢复使用完整集合，不能受 API 列表分页上限影响。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status IN ('PENDING', 'RUNNING')"
                " ORDER BY created_at, id"
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        error: str | None = None,
    ) -> None:
        fields, values = ["updated_at=?"], [utc_now()]
        if status is not None:
            fields.append("status=?"); values.append(status)
        if current_step is not None:
            fields.append("current_step=?"); values.append(current_step)
        if error is not None:
            fields.append("error=?"); values.append(error)
        values.append(task_id)
        with self._lock:
            self._conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
            self._conn.commit()

    def merge_artifact(self, task_id: str, key: str, value) -> None:
        """把某个 Agent 的结构化产物写进 artifacts JSON（key 级覆盖）。"""
        with self._lock:
            row = self._conn.execute("SELECT artifacts FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return
            artifacts = json.loads(row["artifacts"] or "{}")
            artifacts[key] = value
            self._conn.execute(
                "UPDATE tasks SET artifacts=?, updated_at=? WHERE id=?",
                (json.dumps(artifacts, ensure_ascii=False), utc_now(), task_id),
            )
            self._conn.commit()

    def add_warning(self, task_id: str, message: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT warnings FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return
            warnings = json.loads(row["warnings"] or "[]")
            warnings.append(message)
            self._conn.execute(
                "UPDATE tasks SET warnings=?, updated_at=? WHERE id=?",
                (json.dumps(warnings, ensure_ascii=False), utc_now(), task_id),
            )
            self._conn.commit()

    # ---------------- steps ----------------

    def start_step(self, task_id: str, name: str, attempt: int = 1) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO steps(task_id, name, status, attempt, started_at)"
                " VALUES(?,?,?,?,?)",
                (task_id, name, "RUNNING", attempt, utc_now()),
            )
            self._conn.commit()
            return cur.lastrowid

    def finish_step(self, step_id: int, status: str, output=None, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE steps SET status=?, output=?, error=?, finished_at=? WHERE id=?",
                (
                    status,
                    json.dumps(output, ensure_ascii=False) if output is not None else None,
                    error,
                    utc_now(),
                    step_id,
                ),
            )
            self._conn.commit()

    def list_steps(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, status, attempt, error, started_at, finished_at"
                " FROM steps WHERE task_id=? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def fail_running_steps(self, task_id: str, error: str) -> None:
        """把停留在 RUNNING 的步骤标记为 FAILED（异常中断后的收尾）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM steps WHERE task_id=? AND status='RUNNING'", (task_id,)
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE steps SET status='FAILED', error=?, finished_at=? WHERE id=?",
                    (error, utc_now(), row["id"]),
                )
            self._conn.commit()

    # ---------------- events（留痕） ----------------

    def append_event(self, task_id: str, message: str, step: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO task_events(task_id, ts, step, message) VALUES(?,?,?,?)",
                (task_id, utc_now(), step, message),
            )
            self._conn.commit()

    def list_events(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, step, message FROM task_events WHERE task_id=? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- internal ----------------

    @staticmethod
    def _to_record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            topic=row["topic"],
            product_info=row["product_info"],
            scene_count=row["scene_count"],
            status=row["status"],
            current_step=row["current_step"],
            error=row["error"],
            warnings=json.loads(row["warnings"] or "[]"),
            artifacts=json.loads(row["artifacts"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
