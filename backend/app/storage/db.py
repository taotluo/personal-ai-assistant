"""SQLite 持久化：任务/子任务/消息/事件全量落盘。

复盘（M3）直接读这些表——自研管道里每一步的 prompt 输出与决策天然
是自有数据结构，不依赖任何框架 callback。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY,
  title TEXT,
  status TEXT,
  plan_json TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS subtasks(
  task_id TEXT,
  id TEXT,
  status TEXT,
  result_json TEXT,
  PRIMARY KEY(task_id, id)
);
CREATE TABLE IF NOT EXISTS messages(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  role TEXT,
  content TEXT,
  ts REAL
);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  subtask_id TEXT,
  type TEXT,
  data_json TEXT,
  ts REAL
);
CREATE TABLE IF NOT EXISTS memories(  -- M3 复盘记忆库
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  content TEXT,
  task_id TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS schedules(  -- M4 定时任务
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  prompt TEXT,
  cron TEXT,
  enabled INTEGER DEFAULT 1,
  last_run REAL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS skills(  -- M4 技能库（用户批准的流程模板）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  description TEXT,
  plan_hint TEXT,
  source_task TEXT,
  created_at REAL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database.init() 尚未调用"
        return self._db

    # ---- 消息 ----

    async def add_message(self, role: str, content: str, task_id: str | None = None) -> None:
        await self._conn().execute(
            "INSERT INTO messages(task_id, role, content, ts) VALUES(?,?,?,?)",
            (task_id, role, content, time.time()),
        )
        await self._conn().commit()

    async def recent_messages(self, n: int = 12) -> list[dict]:
        cur = await self._conn().execute(
            "SELECT role, content FROM messages ORDER BY seq DESC LIMIT ?", (n,)
        )
        rows = await cur.fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    # ---- 任务 / 子任务 ----

    async def save_task(self, task) -> None:
        await self._conn().execute(
            "INSERT OR REPLACE INTO tasks(id, title, status, plan_json, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                task.id,
                task.title,
                task.state.value,
                task.plan.model_dump_json() if task.plan else None,
                task.created_at,
                time.time(),
            ),
        )
        await self._conn().commit()

    async def update_task_status(self, task) -> None:
        await self._conn().execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (task.state.value, time.time(), task.id),
        )
        await self._conn().commit()

    async def save_subtasks(self, task_id: str, subtasks) -> None:
        for st in subtasks:
            await self._conn().execute(
                "INSERT OR REPLACE INTO subtasks(task_id, id, status, result_json) "
                "VALUES(?,?,?,NULL)",
                (task_id, st.id, "queued"),
            )
        await self._conn().commit()

    async def update_subtask(
        self, task_id: str, subtask_id: str, status: str, result: dict | None = None
    ) -> None:
        await self._conn().execute(
            "UPDATE subtasks SET status=?, result_json=? WHERE task_id=? AND id=?",
            (
                status,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                task_id,
                subtask_id,
            ),
        )
        await self._conn().commit()

    # ---- 事件 ----

    async def log_event(self, ev: dict) -> None:
        data = ev.get("data") or {}
        await self._conn().execute(
            "INSERT INTO events(task_id, subtask_id, type, data_json, ts) VALUES(?,?,?,?,?)",
            (
                ev.get("task_id"),
                data.get("subtask_id"),
                ev.get("type"),
                json.dumps(data, ensure_ascii=False),
                time.time(),
            ),
        )
        await self._conn().commit()

    # ---- 记忆（M3 复盘沉淀）----

    async def add_memories(self, items: list[tuple[str, str]], task_id: str | None = None) -> None:
        """items: [(kind, content), ...]"""
        for kind, content in items:
            await self._conn().execute(
                "INSERT INTO memories(kind, content, task_id, created_at) VALUES(?,?,?,?)",
                (kind, content, task_id, time.time()),
            )
        await self._conn().commit()

    async def relevant_memories(self, query: str, k: int = 10) -> list[dict]:
        """v1 检索：关键词相关优先；无命中则回退最近几条。embedding 检索留待后续。"""
        cur = await self._conn().execute(
            "SELECT kind, content FROM memories ORDER BY seq DESC LIMIT 100"
        )
        rows = [{"kind": kind, "content": content} for kind, content in await cur.fetchall()]
        words = re.findall(r"[\w\u4e00-\u9fff]{2,}", query)
        positives = [m for m in rows if any(w in m["content"] for w in words)]
        if positives:
            return positives[:k]
        return rows[: min(k, 3)]

    # ---- 定时任务（M4）----

    async def add_schedule(self, name: str, prompt: str, cron: str) -> int:
        cur = await self._conn().execute(
            "INSERT INTO schedules(name, prompt, cron, enabled, created_at) VALUES(?,?,?,1,?)",
            (name, prompt, cron, time.time()),
        )
        await self._conn().commit()
        return int(cur.lastrowid)

    async def list_schedules(self) -> list[dict]:
        cur = await self._conn().execute(
            "SELECT id, name, prompt, cron, enabled, last_run, created_at FROM schedules ORDER BY id"
        )
        cols = ["id", "name", "prompt", "cron", "enabled", "last_run", "created_at"]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def get_schedule(self, schedule_id: int) -> dict | None:
        cur = await self._conn().execute(
            "SELECT id, name, prompt, cron, enabled FROM schedules WHERE id=?", (schedule_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "cron": row[3], "enabled": row[4]}

    async def remove_schedule(self, schedule_id: int) -> None:
        await self._conn().execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        await self._conn().commit()

    async def touch_schedule(self, schedule_id: int) -> None:
        await self._conn().execute(
            "UPDATE schedules SET last_run=? WHERE id=?", (time.time(), schedule_id)
        )
        await self._conn().commit()

    # ---- 技能库（M4）----

    async def add_skill(self, name: str, description: str, plan_hint: str, source_task: str | None) -> None:
        await self._conn().execute(
            "INSERT INTO skills(name, description, plan_hint, source_task, created_at) VALUES(?,?,?,?,?)",
            (name, description, plan_hint, source_task, time.time()),
        )
        await self._conn().commit()

    async def list_skills(self, limit: int = 50) -> list[dict]:
        cur = await self._conn().execute(
            "SELECT name, description, plan_hint FROM skills ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [{"name": n, "description": d, "plan_hint": p} for n, d, p in await cur.fetchall()]

    # ---- 任务详情（M4 回溯）----

    async def get_task_detail(self, task_id: str) -> dict | None:
        cur = await self._conn().execute(
            "SELECT id, title, status, plan_json, created_at, updated_at FROM tasks WHERE id=?",
            (task_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        cur = await self._conn().execute(
            "SELECT id, status, result_json FROM subtasks WHERE task_id=?", (task_id,)
        )
        subs = [
            {"id": r[0], "status": r[1], "result": json.loads(r[2]) if r[2] else None}
            for r in await cur.fetchall()
        ]
        cur = await self._conn().execute(
            "SELECT role, content FROM messages WHERE task_id=? ORDER BY seq", (task_id,)
        )
        msgs = [{"role": r[0], "content": r[1]} for r in await cur.fetchall()]
        return {
            "id": row[0],
            "title": row[1],
            "status": row[2],
            "plan": json.loads(row[3]) if row[3] else None,
            "created_at": row[4],
            "updated_at": row[5],
            "subtasks": subs,
            "messages": msgs,
        }

    # ---- 查询 ----

    async def list_tasks(self, limit: int = 50) -> list[dict]:
        cur = await self._conn().execute(
            "SELECT id, title, status, created_at FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [{"id": i, "title": t, "status": s, "created_at": c} for i, t, s, c in rows]
