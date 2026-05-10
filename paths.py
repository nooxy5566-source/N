from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "storage.db"

COUNTRY_FILE = BASE_DIR / "country_codes.json"
PLATFORMS_FILE = BASE_DIR / "platforms.json"
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
GROUPS_FILE = BASE_DIR / "groups.json"
STORE_FILE = BASE_DIR / "sent_codes_store.json"
TOKEN_CACHE_FILE = BASE_DIR / "token_cache.json"
RUNTIME_CONFIG_FILE = BASE_DIR / "runtime_config.json"
RANGES_STORE_FILE = BASE_DIR / "ranges_store.json"

DAILY_STORE_DIR = BASE_DIR / "daily_messages"
EXPORT_DIR = BASE_DIR / "exports"
LOGS_DIR = BASE_DIR / "logs"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_STORE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
