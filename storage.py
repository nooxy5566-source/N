import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .paths import (
    ACCOUNTS_FILE,
    COUNTRY_FILE,
    DAILY_STORE_DIR,
    DB_FILE,
    GROUPS_FILE,
    PLATFORMS_FILE,
    RANGES_STORE_FILE,
    RUNTIME_CONFIG_FILE,
    STORE_FILE,
    TOKEN_CACHE_FILE,
    ensure_dirs,
)

JSON_KEY_BY_NAME = {
    ACCOUNTS_FILE.name: "accounts",
    GROUPS_FILE.name: "groups",
    RANGES_STORE_FILE.name: "ranges_store",
    RUNTIME_CONFIG_FILE.name: "runtime_config",
    STORE_FILE.name: "sent_codes_store",
    TOKEN_CACHE_FILE.name: "token_cache",
    PLATFORMS_FILE.name: "platforms",
    COUNTRY_FILE.name: "country_codes",
}


class JsonSQLiteStore:
    def __init__(self, db_path: Path = DB_FILE) -> None:
        self.db_path = db_path
        ensure_dirs()
        self._init_db()
        self._migrate_from_legacy_once()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_store (
                    day_key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _meta_get(self, key: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else ""

    def _meta_set(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _has_key(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM kv_store WHERE key = ?", (key,)).fetchone()
        return bool(row)

    def get_json(self, key: str, fallback: Any) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        if not row:
            return fallback
        try:
            return json.loads(str(row[0]))
        except Exception:
            return fallback

    def set_json(self, key: str, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO kv_store(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, payload, self._now()),
            )

    def get_daily(self, day_key: str, fallback: Any) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM daily_store WHERE day_key = ?", (day_key,)).fetchone()
        if not row:
            return fallback
        try:
            return json.loads(str(row[0]))
        except Exception:
            return fallback

    def set_daily(self, day_key: str, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO daily_store(day_key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(day_key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (day_key, payload, self._now()),
            )

    def delete_daily(self, day_key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM daily_store WHERE day_key = ?", (day_key,))

    def list_daily_keys(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT day_key FROM daily_store ORDER BY day_key").fetchall()
        return [str(r[0]) for r in rows]

    def clear_daily(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM daily_store")

    def _migrate_from_legacy_once(self) -> None:
        if self._meta_get("legacy_migrated_v1"):
            return

        for filename, key in JSON_KEY_BY_NAME.items():
            path = ACCOUNTS_FILE.parent / filename
            if not path.exists() or self._has_key(key):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            self.set_json(key, data)

        for path in DAILY_STORE_DIR.glob("messages_*.json"):
            day_key = path.stem.replace("messages_", "", 1)
            if not day_key:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            existing = self.get_daily(day_key, None)
            if existing is None:
                self.set_daily(day_key, data)

        self._meta_set("legacy_migrated_v1", self._now())


_STORE = JsonSQLiteStore()


def json_key_for_path(path: Path | str) -> str:
    name = Path(path).name
    return JSON_KEY_BY_NAME.get(name, "")


def load_json(path: Path, fallback: Any) -> Any:
    key = json_key_for_path(path)
    if key:
        return _STORE.get_json(key, fallback)
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def save_json(path: Path, data: Any) -> None:
    key = json_key_for_path(path)
    if key:
        _STORE.set_json(key, data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_daily_store(day_key: str, fallback: Any) -> Any:
    return _STORE.get_daily(day_key, fallback)


def set_daily_store(day_key: str, data: Any) -> None:
    _STORE.set_daily(day_key, data)


def delete_daily_store(day_key: str) -> None:
    _STORE.delete_daily(day_key)


def list_daily_store_days() -> list[str]:
    return _STORE.list_daily_keys()


def clear_daily_store() -> None:
    _STORE.clear_daily()
