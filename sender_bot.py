import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv
from app.paths import (
    ACCOUNTS_FILE,
    BASE_DIR,
    COUNTRY_FILE,
    DAILY_STORE_DIR,
    GROUPS_FILE,
    LOGS_DIR,
    PLATFORMS_FILE,
    RUNTIME_CONFIG_FILE,
    STORE_FILE,
    TOKEN_CACHE_FILE,
)
from app.storage import (
    delete_daily_store,
    get_daily_store,
    list_daily_store_days,
    load_json as db_load_json,
    save_json as db_save_json,
    set_daily_store,
)

TOKEN_TTL_SECONDS = 2 * 60 * 60
TOKEN_REFRESH_SKEW_SECONDS = 5 * 60
PLACEHOLDER_VALUES = {
    "https://your-api-domain.example.com",
    "123456789:EXAMPLE_BOT_TOKEN",
    "-1001234567890",
    "YOUR_PASSWORD",
}

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
logger = logging.getLogger("numplus-bot")
LOG_THROTTLE_SECONDS = 120
_LAST_LOG_AT: dict[str, int] = {}


class ColorFormatter(logging.Formatter):
    RESET = "\033[0m"
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }

    def format(self, record: logging.LogRecord) -> str:
        original = record.levelname
        color = self.COLORS.get(record.levelno, "")
        try:
            if color and sys.stdout.isatty():
                record.levelname = f"{color}{original}{self.RESET}"
            return super().format(record)
        finally:
            record.levelname = original


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    # Reset handlers to avoid duplicate logs when script is reloaded.
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ColorFormatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(console)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=str(LOGS_DIR / "bot.log"),
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(file_handler)


def ask(prompt: str, default: str | None = None) -> str:
    if default is None:
        return input(f"{prompt}: ").strip()
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def ask_missing(prompt: str, current: str) -> str:
    if is_real_value(current):
        return current.strip()
    return ask(prompt)


def is_real_value(value: str | None) -> bool:
    v = str(value or "").strip()
    if not v:
        return False
    if v in PLACEHOLDER_VALUES:
        return False
    low = v.lower()
    if "example" in low or "your-api-domain" in low or "your_password" in low:
        return False
    return True


def digits_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch.isdigit())


def load_json_list(path: Path) -> list[dict]:
    data = db_load_json(path, [])
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def load_countries() -> list[dict[str, str]]:
    rows = [x for x in load_json_list(COUNTRY_FILE) if x.get("dial_code")]
    rows.sort(key=lambda x: len(str(x.get("dial_code", ""))), reverse=True)
    return rows


def load_platforms() -> dict[str, str]:
    rows = load_json_list(PLATFORMS_FILE)
    out: dict[str, str] = {}
    for r in rows:
        key = str(r.get("key", "")).strip().lower()
        short = str(r.get("short", "")).strip()
        if key and short:
            out[key] = short
    return out


def load_accounts() -> list[dict[str, str]]:
    rows = load_json_list(ACCOUNTS_FILE)
    # Backward compatible loader: supports JSON object {"accounts":[...]}
    # and simple line format: "email password".
    if not rows and ACCOUNTS_FILE.exists():
        try:
            raw = ACCOUNTS_FILE.read_text(encoding="utf-8").strip()
            if raw.startswith("{"):
                obj = json.loads(raw)
                maybe_rows = obj.get("accounts") if isinstance(obj, dict) else None
                if isinstance(maybe_rows, list):
                    rows = [x for x in maybe_rows if isinstance(x, dict)]
            elif raw:
                parsed_rows: list[dict[str, str]] = []
                for idx, line in enumerate(raw.splitlines(), start=1):
                    v = line.strip()
                    if not v or v.startswith("#"):
                        continue
                    parts = v.split()
                    if len(parts) >= 2:
                        email = parts[0].strip()
                        password = " ".join(parts[1:]).strip()
                        parsed_rows.append(
                            {
                                "name": f"account_{idx}",
                                "email": email,
                                "password": password,
                                "enabled": True,
                            }
                        )
                rows = parsed_rows
        except Exception:
            rows = []
    out: list[dict[str, str]] = []
    for r in rows:
        enabled = bool(r.get("enabled", True))
        email = str(r.get("email", "")).strip()
        password = str(r.get("password", "")).strip()
        name = str(r.get("name", email)).strip() or email
        if enabled and email and password:
            out.append({"name": name, "email": email, "password": password})
    return out


def load_groups() -> list[dict[str, str]]:
    rows = load_json_list(GROUPS_FILE)
    out: list[dict[str, str]] = []
    for r in rows:
        enabled = bool(r.get("enabled", True))
        chat_id = str(r.get("chat_id", "")).strip()
        name = str(r.get("name", chat_id)).strip() or chat_id
        # Skip placeholder/demo group ids so .env fallback can be used.
        if enabled and is_real_value(chat_id):
            out.append({"name": name, "chat_id": chat_id})
    return out


def detect_country(number: str, countries: list[dict[str, str]]) -> dict[str, str]:
    num = digits_only(number)
    if num.startswith("00"):
        num = num[2:]
    for row in countries:
        dial = str(row.get("dial_code", ""))
        if dial and num.startswith(dial):
            return row
    return {"name_ar": "غير معروف", "name_en": "Unknown", "iso2": "UN", "dial_code": ""}


def iso_to_flag(iso2: str) -> str:
    code = (iso2 or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🏳️"
    base = 127397
    return chr(base + ord(code[0])) + chr(base + ord(code[1]))


def service_short(service_name: str, platforms: dict[str, str]) -> str:
    key = (service_name or "").strip().lower()
    if key in platforms:
        return str(platforms[key]).upper()
    return (service_name[:2] or "NA").upper()


def service_emoji_id(service_name: str, platform_rows: list[dict]) -> str:
    key = (service_name or "").strip().lower()
    for row in platform_rows:
        if str(row.get("key", "")).strip().lower() == key:
            return str(row.get("emoji_id", "")).strip()
    return ""


def service_emoji_alt(service_name: str, platform_rows: list[dict]) -> str:
    key = (service_name or "").strip().lower()
    for row in platform_rows:
        if str(row.get("key", "")).strip().lower() == key:
            alt = str(row.get("emoji", "")).strip()
            if alt:
                return alt
    return "✨"


def extract_code(message: str) -> str:
    text = message or ""
    # Prefer patterns like 123-456 then fallback to plain 4-8 digits.
    m = re.search(r"\b\d{2,4}-\d{2,4}\b", text)
    if m:
        return m.group(0)
    m2 = re.search(r"\b\d{4,8}\b", text)
    if m2:
        return m2.group(0)
    return ""


def build_message(item: dict, countries: list[dict[str, str]], platforms: dict[str, str], platform_rows: list[dict]) -> str:
    raw_number = str(item.get("number", ""))
    number_digits = digits_only(raw_number)
    number_with_plus = f"+{number_digits}" if number_digits else raw_number
    service_name = str(item.get("service_name", "Unknown"))
    short = service_short(service_name, platforms)
    semoji_id = service_emoji_id(service_name, platform_rows)
    semoji_alt = service_emoji_alt(service_name, platform_rows)
    use_custom_emoji = os.getenv("USE_CUSTOM_EMOJI", "0").strip() == "1"
    country = detect_country(raw_number, countries)
    iso2 = country.get("iso2", "UN")
    flag = iso_to_flag(iso2)
    message_text = str(item.get("message", "")).strip()
    escaped_head = _md_escape(f"{short} {iso2} {flag} {number_with_plus}")
    escaped_msg = _md_code_escape(message_text)
    custom = f"![{semoji_alt}](tg://emoji?id={semoji_id}) " if (use_custom_emoji and semoji_id) else f"{semoji_alt} "
    return f"> {custom}*{escaped_head}*\n```\n{escaped_msg}\n```"


def _md_escape(text: str) -> str:
    # MarkdownV2 special chars
    out = re.sub(r"([_\\*\\[\\]\\(\\)~`>#+\\-=|{}.!])", r"\\\1", text or "")
    return out.replace("+", r"\+")


def _md_code_escape(text: str) -> str:
    t = text or ""
    # Keep code block valid.
    t = t.replace("```", "'''")
    return t


def send_telegram_message(bot_token: str, chat_id: str, text: str, copy_value: str) -> dict:
    api = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": f"{copy_value}", "style": "success", "copy_text": {"text": copy_value}}],
            ]
        },
        "disable_web_page_preview": True,
    }
    r = requests.post(api, json=payload, timeout=30)
    data = r.json()
    if data.get("ok"):
        return data

    # Fallback if copy_text is unsupported in the current Bot API/client environment.
    payload["reply_markup"] = {
        "inline_keyboard": [
            [{"text": f"{copy_value}", "style": "success", "url": f"https://t.me/share/url?url={copy_value}"}],
        ]
    }
    r2 = requests.post(api, json=payload, timeout=30)
    return r2.json()


def edit_telegram_message(bot_token: str, chat_id: str, message_id: int, text: str, copy_value: str) -> dict:
    api = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": f"{copy_value}", "style": "success", "copy_text": {"text": copy_value}}],
            ]
        },
        "disable_web_page_preview": True,
    }
    r = requests.post(api, json=payload, timeout=30)
    data = r.json()
    if data.get("ok"):
        return data
    desc = str(data.get("description", "")).lower()
    if "message is not modified" in desc:
        # Treat "not modified" as success to avoid sending duplicate messages.
        return {"ok": True, "result": {"message_id": message_id}, "not_modified": True}

    payload["reply_markup"] = {
        "inline_keyboard": [
            [{"text": f"{copy_value}", "style": "success", "url": f"https://t.me/share/url?url={copy_value}"}],
        ]
    }
    r2 = requests.post(api, json=payload, timeout=30)
    data2 = r2.json()
    if not data2.get("ok"):
        desc2 = str(data2.get("description", "")).lower()
        if "message is not modified" in desc2:
            return {"ok": True, "result": {"message_id": message_id}, "not_modified": True}
    return data2


def _today_key() -> str:
    return date.today().isoformat()


def _daily_store_path(day_key: str) -> Path:
    return DAILY_STORE_DIR / f"messages_{day_key}.json"


def cleanup_old_daily_files(current_day_key: str) -> None:
    for day_key in list_daily_store_days():
        if day_key != current_day_key:
            delete_daily_store(day_key)
    # Keep legacy files clean in case old process created them.
    DAILY_STORE_DIR.mkdir(parents=True, exist_ok=True)
    keep_path = _daily_store_path(current_day_key).resolve()
    for p in DAILY_STORE_DIR.glob("messages_*.json"):
        try:
            if p.resolve() != keep_path:
                p.unlink(missing_ok=True)
        except Exception:
            continue


def load_daily_store(day_key: str) -> dict:
    data = get_daily_store(day_key, {})
    if isinstance(data, dict) and isinstance(data.get("seen_keys"), list) and isinstance(data.get("sent"), list):
        data["day"] = day_key
        if not isinstance(data.get("latest_by_thread"), dict):
            data["latest_by_thread"] = {}
        return data
    return {"day": day_key, "seen_keys": [], "sent": [], "latest_by_thread": {}}


def save_daily_store(day_key: str, store: dict) -> None:
    set_daily_store(day_key, store)


def load_token_cache() -> dict:
    data = db_load_json(TOKEN_CACHE_FILE, {"accounts": {}})
    if isinstance(data, dict) and isinstance(data.get("accounts"), dict):
        return data
    return {"accounts": {}}


def load_runtime_config() -> dict:
    data = db_load_json(RUNTIME_CONFIG_FILE, {"fetch_codes_enabled": True})
    if isinstance(data, dict):
        if "fetch_codes_enabled" not in data:
            data["fetch_codes_enabled"] = True
        return data
    return {"fetch_codes_enabled": True}


def runtime_start_date(default_value: str) -> str:
    cfg = load_runtime_config()
    value = str(cfg.get("messages_start_date", "")).strip()
    if value:
        return normalize_start_date(value)
    return normalize_start_date(default_value)


def runtime_api_base(default_value: str) -> str:
    cfg = load_runtime_config()
    value = str(cfg.get("api_base_url", "")).strip().rstrip("/")
    return value or str(default_value or "").strip().rstrip("/")


def runtime_api_session_token(default_value: str) -> str:
    cfg = load_runtime_config()
    value = str(cfg.get("api_session_token", "")).strip()
    return value or str(default_value or "").strip()


def runtime_bot_limit(default_value: int) -> int:
    cfg = load_runtime_config()
    raw = str(cfg.get("bot_limit", default_value)).strip()
    try:
        n = int(raw)
    except Exception:
        n = int(default_value)
    return max(1, min(100, n))


def runtime_api_base(default_value: str) -> str:
    cfg = load_runtime_config()
    value = str(cfg.get("api_base_url", "")).strip().rstrip("/")
    return value or default_value


def runtime_api_session_token(default_value: str) -> str:
    cfg = load_runtime_config()
    value = str(cfg.get("api_session_token", "")).strip()
    return value or default_value


def runtime_bot_limit(default_value: int) -> int:
    cfg = load_runtime_config()
    try:
        n = int(str(cfg.get("bot_limit", default_value)).strip())
    except Exception:
        n = int(default_value)
    return max(1, min(100, n))


def runtime_messages_update_marker() -> str:
    cfg = load_runtime_config()
    return str(cfg.get("messages_update_requested_at", "")).strip()


def is_fetch_codes_enabled() -> bool:
    cfg = load_runtime_config()
    return bool(cfg.get("fetch_codes_enabled", True))


def save_token_cache(cache: dict) -> None:
    db_save_json(TOKEN_CACHE_FILE, cache)


def cache_get_valid_token(cache: dict, account_name: str) -> str | None:
    row = (cache.get("accounts") or {}).get(account_name)
    if not isinstance(row, dict):
        return None
    token = str(row.get("token", "")).strip()
    expires_at = int(row.get("expires_at", 0) or 0)
    if not token or expires_at <= int(time.time()) + TOKEN_REFRESH_SKEW_SECONDS:
        return None
    return token


def cache_set_token(cache: dict, account_name: str, token: str) -> None:
    now = int(time.time())
    cache.setdefault("accounts", {})[account_name] = {
        "token": token,
        "obtained_at": now,
        "expires_at": now + TOKEN_TTL_SECONDS,
    }


def get_or_refresh_account_token(
    api_base: str,
    account: dict[str, str],
    account_tokens: dict[str, str],
    token_cache: dict,
) -> str | None:
    name = account["name"]
    mem_tok = account_tokens.get(name)
    if mem_tok and cache_get_valid_token(token_cache, name):
        return mem_tok

    cached_tok = cache_get_valid_token(token_cache, name)
    if cached_tok:
        account_tokens[name] = cached_tok
        return cached_tok

    new_tok = api_login(api_base, account["email"], account["password"])
    if not new_tok:
        return None
    account_tokens[name] = new_tok
    cache_set_token(token_cache, name, new_tok)
    save_token_cache(token_cache)
    return new_tok


def msg_key(item: dict) -> str:
    number = str(item.get("number", ""))
    service_name = str(item.get("service_name", ""))
    message = str(item.get("message", ""))
    rng = str(item.get("range", ""))
    return f"{number}|{service_name}|{rng}|{message}"


def thread_key(item: dict) -> str:
    number = str(item.get("number", ""))
    service_name = str(item.get("service_name", ""))
    rng = str(item.get("range", ""))
    return f"{number}|{service_name}|{rng}"


def normalize_start_date(raw: str) -> str:
    v = (raw or "").strip()
    parts = v.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        if len(y) == 4:
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return date.today().isoformat()


def _extract_login_token(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""

    candidates: list[object] = [payload]
    for key in ("data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)

    token_keys = ("token", "access_token", "session_token", "api_token", "jwt")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in token_keys:
            value = str(candidate.get(key, "")).strip()
            if value:
                return value
    return ""


def _extract_login_error(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail", "errors"):
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def _short_text(value: object, max_len: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _should_log(key: str, throttle_seconds: int = LOG_THROTTLE_SECONDS) -> bool:
    now = int(time.time())
    last = int(_LAST_LOG_AT.get(key, 0))
    if now - last < throttle_seconds:
        return False
    _LAST_LOG_AT[key] = now
    return True


def _classify_request_error(exc: Exception) -> str:
    txt = str(exc)
    low = txt.lower()
    if "name or service not known" in low or "failed to resolve" in low or "nameresolutionerror" in low:
        return "dns_error"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "connection refused" in low:
        return "connection_refused"
    return "network_error"


def check_api_health(api_base: str) -> bool:
    url = f"{api_base}/api/v1/health"
    try:
        r = requests.get(url, timeout=20)
    except requests.RequestException as exc:
        reason = _classify_request_error(exc)
        logger.error("api health failed | endpoint=%s | reason=%s | error=%s", url, reason, _short_text(exc))
        return False

    body_snippet = ""
    try:
        payload = r.json()
        body_snippet = _short_text(payload)
    except ValueError:
        body_snippet = _short_text(r.text)

    if r.status_code != 200:
        logger.warning(
            "api health responded non-200 | endpoint=%s | status=%s | body=%s",
            url,
            r.status_code,
            body_snippet,
        )
        return False

    logger.info("api health ok | endpoint=%s | body=%s", url, body_snippet)
    return True


def api_login(api_base: str, email: str, password: str) -> str | None:
    url = f"{api_base}/api/v1/auth/login"
    try:
        r = requests.post(url, json={"email": email, "password": password}, timeout=90)
    except requests.RequestException as exc:
        reason = _classify_request_error(exc)
        key = f"login_req_{email}_{reason}"
        if _should_log(key):
            logger.error(
                "login request failed | account=%s | reason=%s | endpoint=%s | error=%s",
                email,
                reason,
                url,
                _short_text(exc),
            )
        return None

    try:
        payload: object = r.json()
    except ValueError:
        payload = None

    if r.status_code != 200:
        err = _extract_login_error(payload) or (r.text or "").strip()
        logger.warning(
            "login failed | account=%s | endpoint=%s | status=%s | error=%s",
            email,
            url,
            r.status_code,
            _short_text(err or "no error message"),
        )
        return None

    token = _extract_login_token(payload if isinstance(payload, dict) else {})
    if token:
        return token

    logger.warning("login response missing token | account=%s | endpoint=%s", email, url)
    return None


def fetch_messages(api_base: str, api_token: str, start_date: str, limit: int) -> list[dict]:
    endpoint = f"{api_base}/api/v1/biring/code"
    try:
        r = requests.post(endpoint, json={"token": api_token, "start_date": start_date}, timeout=600)
    except requests.RequestException as exc:
        reason = _classify_request_error(exc)
        raise RuntimeError(f"request failed ({reason}): {exc}") from exc
    try:
        j = r.json()
    except ValueError as exc:
        raise RuntimeError(f"invalid json response | status={r.status_code} | body={_short_text(r.text)}") from exc
    if r.status_code != 200:
        raise RuntimeError(str(j))
    return ((j.get("data") or {}).get("messages") or [])[:limit]


def run_loop(start_date: str, api_base: str, api_token: str, tg_token: str, target_groups: list[dict[str, str]], limit: int, once: bool) -> None:
    current_api_base = runtime_api_base(api_base)
    current_api_token = runtime_api_session_token(api_token)
    current_start_date = runtime_start_date(start_date)
    current_limit = runtime_bot_limit(limit)

    countries = load_countries()
    platform_rows = load_json_list(PLATFORMS_FILE)
    platforms = load_platforms()
    active_day = _today_key()
    cleanup_old_daily_files(active_day)
    day_store = load_daily_store(active_day)
    seen_keys = set(day_store.get("seen_keys", []))
    latest_by_thread = day_store.get("latest_by_thread", {})
    if not isinstance(latest_by_thread, dict):
        latest_by_thread = {}
        day_store["latest_by_thread"] = latest_by_thread

    accounts = load_accounts()
    token_cache = load_token_cache()
    account_tokens: dict[str, str] = {}
    update_marker = runtime_messages_update_marker()
    for acc in accounts:
        tok = get_or_refresh_account_token(current_api_base, acc, account_tokens, token_cache)
        if tok:
            logger.info("account ready | account=%s", acc["name"])
        else:
            logger.warning("account login failed | account=%s", acc["name"])

    logger.info("started polling | interval=30s | start_date=%s | limit=%s", current_start_date, current_limit)
    logger.info("press Ctrl+C to stop")

    while True:
        latest_marker = runtime_messages_update_marker()
        if latest_marker and latest_marker != update_marker:
            update_marker = latest_marker
            current_api_base = runtime_api_base(current_api_base)
            current_api_token = runtime_api_session_token(current_api_token)
            current_start_date = runtime_start_date(current_start_date)
            current_limit = runtime_bot_limit(current_limit)
            countries = load_countries()
            platform_rows = load_json_list(PLATFORMS_FILE)
            platforms = load_platforms()
            accounts = load_accounts()
            token_cache = load_token_cache()
            account_tokens = {}
            logger.info(
                "runtime refresh requested | marker=%s | api_base=%s | start_date=%s | limit=%s",
                latest_marker,
                current_api_base,
                current_start_date,
                current_limit,
            )

        if not is_fetch_codes_enabled():
            if _should_log("fetch_paused", throttle_seconds=120):
                logger.info("fetch codes is paused by runtime config")
            if once:
                return
            time.sleep(30)
            continue

        now_day = _today_key()
        if now_day != active_day:
            active_day = now_day
            cleanup_old_daily_files(active_day)
            day_store = load_daily_store(active_day)
            seen_keys = set(day_store.get("seen_keys", []))
            latest_by_thread = day_store.get("latest_by_thread", {})
            if not isinstance(latest_by_thread, dict):
                latest_by_thread = {}
                day_store["latest_by_thread"] = latest_by_thread
            logger.info("rotated daily store | day=%s", active_day)

        all_rows: list[dict] = []

        if current_api_token:
            try:
                all_rows.extend(fetch_messages(current_api_base, current_api_token, current_start_date, current_limit))
            except Exception as exc:
                logger.warning("api token fetch failed | error=%s", _short_text(exc))

        for acc in accounts:
            name = acc["name"]
            tok = get_or_refresh_account_token(current_api_base, acc, account_tokens, token_cache)
            if not tok:
                continue
            try:
                all_rows.extend(fetch_messages(current_api_base, tok, current_start_date, current_limit))
            except Exception:
                new_tok = api_login(current_api_base, acc["email"], acc["password"])
                if not new_tok:
                    continue
                account_tokens[name] = new_tok
                cache_set_token(token_cache, name, new_tok)
                save_token_cache(token_cache)
                try:
                    all_rows.extend(fetch_messages(current_api_base, new_tok, current_start_date, current_limit))
                except Exception:
                    continue

        uniq: dict[str, dict] = {}
        for row in all_rows:
            uniq[msg_key(row)] = row
        rows = list(uniq.values())[:current_limit]
        new_rows = [x for x in rows if msg_key(x) not in seen_keys]

        if not new_rows:
            if _should_log("no_new_messages", throttle_seconds=300):
                logger.info("no new messages")
            if once:
                return
            time.sleep(30)
            continue

        logger.info("new messages | count=%s", len(new_rows))
        for idx, item in enumerate(new_rows, start=1):
            number = str(item.get("number", ""))
            message_text = str(item.get("message", ""))
            code = extract_code(message_text) or number
            text = build_message(item, countries, platforms, platform_rows)
            tkey = thread_key(item)
            prev_map = latest_by_thread.get(tkey, {})
            if not isinstance(prev_map, dict):
                prev_map = {}

            any_sent = False
            sent_info: list[dict[str, str | int | None]] = []
            next_map: dict[str, int] = {}
            for grp in target_groups:
                gid = grp["chat_id"]
                gname = grp["name"]
                prev_msg_id_raw = prev_map.get(gid)
                j: dict = {}
                action = "send"
                if isinstance(prev_msg_id_raw, int):
                    try:
                        j = edit_telegram_message(tg_token, gid, prev_msg_id_raw, text, code)
                        action = "edit"
                    except Exception as exc:
                        logger.warning("edit failed | idx=%s | group=%s | error=%s", idx, gname, _short_text(exc))
                        j = {}

                if not j or not j.get("ok"):
                    try:
                        j = send_telegram_message(tg_token, gid, text, code)
                        action = "send"
                    except Exception as exc:
                        logger.error("send failed | idx=%s | group=%s | error=%s", idx, gname, _short_text(exc))
                        continue
                    if not j.get("ok"):
                        logger.error("send failed | idx=%s | group=%s | response=%s", idx, gname, _short_text(j))
                        continue

                any_sent = True
                result_row = j.get("result") or {}
                msg_id = result_row.get("message_id") or prev_msg_id_raw
                if isinstance(msg_id, int):
                    next_map[gid] = msg_id
                sent_info.append({"group": gname, "chat_id": gid, "message_id": msg_id})
                logger.info("%s ok | idx=%s | group=%s | message_id=%s | code=%s", action, idx, gname, msg_id, code)

            if any_sent:
                mkey = msg_key(item)
                seen_keys.add(mkey)
                latest_by_thread[tkey] = next_map
                day_store["sent"].append(
                    {
                        "number": number,
                        "code": code,
                        "service_name": item.get("service_name"),
                        "range": item.get("range"),
                        "message": item.get("message"),
                        "revenue": item.get("revenue"),
                        "groups": sent_info,
                        "thread_key": tkey,
                        "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                day_store["seen_keys"] = list(seen_keys)
                day_store["latest_by_thread"] = latest_by_thread
                save_daily_store(active_day, day_store)

        if once:
            return
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser(description="NumPlus Telegram Bot Client")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle then exit")
    parser.add_argument("--no-input", action="store_true", help="Run without interactive prompts using .env/config files")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    setup_logging()
    default_api = runtime_api_base(os.getenv("API_BASE_URL", "").strip())
    default_start = runtime_start_date(os.getenv("API_START_DATE", "2025-01-01").strip())
    default_api_token = runtime_api_session_token(os.getenv("API_SESSION_TOKEN", "").strip())
    default_tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    default_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    default_limit = str(runtime_bot_limit(int(str(os.getenv("BOT_LIMIT", "30") or "30").strip() or "30")))

    print("=== NumPlus Telegram Bot Client ===")
    if args.no_input:
        api_base = (default_api if is_real_value(default_api) else "http://127.0.0.1:8000").rstrip("/")
        tg_token = default_tg_token.strip()
        groups = load_groups()
        if groups:
            target_groups = groups
        else:
            fallback_chat = default_chat_id.strip()
            target_groups = [{"name": "default_group", "chat_id": fallback_chat}] if fallback_chat else []
        accounts = load_accounts()
        api_token = default_api_token if is_real_value(default_api_token) else ""
        start_date_raw = default_start or date.today().isoformat()
        start_date = normalize_start_date(start_date_raw)
        try:
            limit = max(1, min(100, int(default_limit or "30")))
        except Exception:
            limit = 30
    else:
        if is_real_value(default_api):
            api_base = ask_missing("API domain", default_api).rstrip("/")
        else:
            api_base = ask("API domain", "http://127.0.0.1:8000").rstrip("/")
        tg_token = ask_missing("Telegram bot token", default_tg_token)
        groups = load_groups()
        if groups:
            target_groups = groups
        else:
            chat_id = ask_missing("Telegram group/chat id", default_chat_id)
            target_groups = [{"name": "default_group", "chat_id": chat_id}]

        # Ask only if token missing and no usable accounts file.
        accounts = load_accounts()
        api_token = default_api_token if is_real_value(default_api_token) else ""
        if not api_token and not accounts:
            api_token = ask("API session token (missing and no accounts found)")

        # Keep start date interactive every run, while other core settings stay persisted.
        start_date_raw = ask("Start date YYYY-MM-DD", default_start or date.today().isoformat())
        start_date = normalize_start_date(start_date_raw)
        if start_date != start_date_raw:
            print(f"Normalized/invalid date input. Using: {start_date}")

        limit_raw = ask("Messages limit", default_limit or "30")
        try:
            limit = max(1, min(100, int(limit_raw)))
        except Exception:
            limit = 30

    if not tg_token:
        logger.error("telegram bot token missing")
        return
    if not target_groups:
        logger.error("no target groups configured")
        return

    check_api_health(api_base)

    try:
        run_loop(start_date, api_base, api_token, tg_token, target_groups, limit, args.once)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
