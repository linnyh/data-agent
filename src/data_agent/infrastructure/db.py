"""用户与会话持久化（同步 sqlite3；FastAPI def 路由自动走线程池）。

ponytail: 同步 SQLite 查询为微秒级，线程池并发足够；高并发时换 aiosqlite
（全链路同一事件循环）或 Postgres。
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Database:
    """用户表 + 会话表的 SQLite 仓储。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            " user_id TEXT PRIMARY KEY,"
            " username TEXT UNIQUE NOT NULL,"
            " password_hash TEXT NOT NULL,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            " session_id TEXT PRIMARY KEY,"
            " owner_id TEXT NOT NULL REFERENCES users(user_id),"
            " created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- 用户 -----------------------------------------------------------------

    def create_user(self, username: str, password_hash: str) -> str | None:
        """创建用户，返回 user_id；用户名已存在返回 None。"""
        user_id = _new_id()
        try:
            self._conn.execute(
                "INSERT INTO users (user_id, username, password_hash) VALUES (?, ?, ?)",
                (user_id, username, password_hash),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            return None
        return user_id

    def get_user_by_username(self, username: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT user_id, username, password_hash FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
        return (
            {"user_id": row[0], "username": row[1], "password_hash": row[2]}
            if row
            else None
        )

    def ensure_local_user(self) -> str:
        """本地单用户模式：返回固定本地用户的 user_id，不存在则创建。"""
        user = self.get_user_by_username("local")
        if user is not None:
            return user["user_id"]
        user_id = _new_id()
        self._conn.execute(
            "INSERT INTO users (user_id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, "local", ""),
        )
        self._conn.commit()
        return user_id

    # -- 会话 -----------------------------------------------------------------

    def create_session(self, owner_id: str) -> str:
        session_id = _new_id()
        self._conn.execute(
            "INSERT INTO sessions (session_id, owner_id) VALUES (?, ?)",
            (session_id, owner_id),
        )
        self._conn.commit()
        return session_id

    def session_owner(self, session_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT owner_id FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def list_sessions(self, owner_id: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT session_id FROM sessions WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        )
        return [r[0] for r in cur.fetchall()]
