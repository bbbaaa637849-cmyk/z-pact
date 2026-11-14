# ================================================================
# bot_service/sqlite_fsm.py — مخزن حالات المحادثة الدائم على SQLite
# يضمن عدم فقدان خطوات الطلب أو بيانات المدرس عند إعادة تشغيل السيرفر
# ================================================================

import asyncio
import json
import os
import sqlite3
import time
from typing import Any, Dict, Mapping, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    """مخزن FSM دائم على SQLite متوافق مع Aiogram 3 ويعمل عبر إعادة التشغيل."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_states (
                storage_key TEXT PRIMARY KEY,
                state TEXT,
                data_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _serialize_key(key: StorageKey) -> str:
        thread = "" if key.thread_id is None else str(key.thread_id)
        destiny = key.destiny or "default"
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{thread}:{destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        s_key = self._serialize_key(key)
        state_val: Optional[str] = state.state if isinstance(state, State) else state
        now = time.time()
        async with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM fsm_states WHERE storage_key = ?", (s_key,)
            ).fetchone()
            data_json = row[0] if row else "{}"
            if state_val is None and data_json == "{}":
                self._conn.execute("DELETE FROM fsm_states WHERE storage_key = ?", (s_key,))
            else:
                self._conn.execute(
                    """
                    INSERT INTO fsm_states (storage_key, state, data_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(storage_key) DO UPDATE SET
                        state = excluded.state,
                        updated_at = excluded.updated_at
                    """,
                    (s_key, state_val, data_json, now),
                )
            self._conn.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        s_key = self._serialize_key(key)
        async with self._lock:
            row = self._conn.execute(
                "SELECT state FROM fsm_states WHERE storage_key = ?", (s_key,)
            ).fetchone()
            return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        s_key = self._serialize_key(key)
        payload = dict(data) if data else {}
        data_json = json.dumps(payload, ensure_ascii=False, default=str)
        now = time.time()
        async with self._lock:
            row = self._conn.execute(
                "SELECT state FROM fsm_states WHERE storage_key = ?", (s_key,)
            ).fetchone()
            cur_state = row[0] if row else None
            if cur_state is None and not payload:
                self._conn.execute("DELETE FROM fsm_states WHERE storage_key = ?", (s_key,))
            else:
                self._conn.execute(
                    """
                    INSERT INTO fsm_states (storage_key, state, data_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(storage_key) DO UPDATE SET
                        data_json = excluded.data_json,
                        updated_at = excluded.updated_at
                    """,
                    (s_key, cur_state, data_json, now),
                )
            self._conn.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        s_key = self._serialize_key(key)
        async with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM fsm_states WHERE storage_key = ?", (s_key,)
            ).fetchone()
            if not row or not row[0]:
                return {}
            try:
                parsed = json.loads(row[0])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

    async def close(self) -> None:
        async with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
