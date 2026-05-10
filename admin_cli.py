import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from app.paths import (
    ACCOUNTS_FILE,
    BASE_DIR,
    DAILY_STORE_DIR,
    GROUPS_FILE,
    PLATFORMS_FILE,
    RANGES_STORE_FILE,
    STORE_FILE,
)
from app.storage import (
    clear_daily_store,
    delete_daily_store,
    get_daily_store,
    list_daily_store_days,
    load_json as db_load_json,
    save_json as db_save_json,
)

PLACEHOLDER_VALUES = {
    "https://your-api-domain.example.com",
    "123456789:EXAMPLE_BOT_TOKEN",
    "-1001234567890",
    "YOUR_PASSWORD",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHAT_ID_RE = re.compile(r"^-100\d{6,}$")
USE_COLOR = sys.stdout.isatty()
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def ok(msg: str) -> None:
    prefix = f"{GREEN}[OK]{RESET}" if USE_COLOR else "[OK]"
    print(f"{prefix} {msg}")


def err(msg: str) -> None:
    prefix = f"{RED}[ERR]{RESET}" if USE_COLOR else "[ERR]"
    print(f"{prefix} {msg}")


def warn(msg: str) -> None:
    prefix = f"{YELLOW}[WARN]{RESET}" if USE_COLOR else "[WARN]"
    print(f"{prefix} {msg}")


def heading(msg: str) -> None:
    title = f"{BOLD}{CYAN}{msg}{RESET}" if USE_COLOR else msg
    print(f"\n=== {title} ===")


def load_json(path: Path, fallback):
    return db_load_json(path, fallback)


def save_json(path: Path, data) -> None:
    db_save_json(path, data)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _validate_email(value: str) -> bool:
    return bool(EMAIL_RE.match((value or "").strip()))


def _validate_chat_id(value: str) -> bool:
    chat_id = str(value or "").strip()
    return bool(CHAT_ID_RE.match(chat_id))


def _validate_day(value: str) -> bool:
    v = str(value or "").strip()
    parts = v.split("-")
    if len(parts) != 3 or any(not p.isdigit() for p in parts):
        return False
    y, m, d = parts
    if len(y) != 4:
        return False
    try:
        date(int(y), int(m), int(d))
    except ValueError:
        return False
    return True


def _validate_request_count(value: int) -> tuple[bool, str]:
    if value < 50:
        return False, "count must be >= 50"
    if value > 1000:
        return False, "count must be <= 1000"
    if value % 50 != 0:
        return False, "count must be a multiple of 50"
    return True, ""


def _range_limit_total() -> int:
    raw = env_value("RANGE_MAX_TOTAL", "1000")
    try:
        value = int(raw)
    except Exception:
        value = 1000
    return max(50, value)


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


def env_value(key: str, fallback: str = "") -> str:
    raw = os.getenv(key, "").strip()
    if is_real_value(raw):
        return raw
    return fallback.strip()


def load_active_accounts() -> list[dict]:
    rows = load_json(ACCOUNTS_FILE, [])
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("enabled", True)):
            continue
        email = str(row.get("email", "")).strip()
        password = str(row.get("password", "")).strip()
        name = str(row.get("name", email)).strip() or email
        if email and password:
            out.append({"name": name, "email": email, "password": password})
    return out


def _extract_login_token(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates: list[dict] = [payload]
    for key in ("data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for key in ("token", "access_token", "session_token", "api_token", "jwt"):
            tok = str(candidate.get(key, "")).strip()
            if tok:
                return tok
    return ""


def api_login(api_base: str, email: str, password: str) -> tuple[str | None, str]:
    url = f"{api_base.rstrip('/')}/api/v1/auth/login"
    try:
        r = requests.post(url, json={"email": email, "password": password}, timeout=60)
    except requests.RequestException as exc:
        return None, str(exc)
    payload: object
    try:
        payload = r.json()
    except ValueError:
        payload = None
    if r.status_code != 200:
        err = ""
        if isinstance(payload, dict):
            err = str(payload.get("message") or payload.get("error") or payload.get("detail") or "").strip()
        if not err:
            err = (r.text or "").strip()
        return None, f"status={r.status_code} {err}".strip()
    token = _extract_login_token(payload)
    if token:
        return token, ""
    return None, "login succeeded without token in response"


def api_post(api_base: str, path: str, body: dict, timeout: int = 60) -> tuple[bool, object, str]:
    url = f"{api_base.rstrip('/')}{path}"
    try:
        r = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as exc:
        return False, None, str(exc)
    try:
        payload: object = r.json()
    except ValueError:
        payload = {"raw": r.text}
    if r.status_code != 200:
        msg = ""
        if isinstance(payload, dict):
            msg = str(payload.get("message") or payload.get("error") or payload.get("detail") or "").strip()
        if not msg:
            msg = str(payload)
        return False, payload, f"status={r.status_code} {msg}".strip()
    return True, payload, ""


def _extract_balance_value(payload: object) -> float | None:
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, dict):
        for key in ("balance", "wallet", "credit", "amount"):
            val = payload.get(key)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val.strip())
                except Exception:
                    pass
        for key in ("data", "result"):
            nested = payload.get(key)
            got = _extract_balance_value(nested)
            if got is not None:
                return got
    return None


def fetch_account_balance(api_base: str, token: str) -> tuple[float | None, str, str]:
    ok, payload, err = api_post(api_base, "/api/v1/balance", {"token": token}, timeout=40)
    if not ok:
        return None, "/api/v1/balance", err
    balance = _extract_balance_value(payload)
    if balance is None:
        return None, "/api/v1/balance", "response missing balance value"
    return balance, "/api/v1/balance", ""


def _extract_data(payload: object) -> object:
    if isinstance(payload, dict):
        for key in ("data", "result"):
            if key in payload:
                return payload[key]
    return payload


def _extract_list_payload(payload: object) -> list:
    data = _extract_data(payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "rows", "numbers", "applications", "apps", "services"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def load_ranges_store() -> dict:
    data = load_json(RANGES_STORE_FILE, {})
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("ranges"), dict):
        data["ranges"] = {}
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    return data


def save_ranges_store(store: dict) -> None:
    store["meta"] = {
        **(store.get("meta") if isinstance(store.get("meta"), dict) else {}),
        "updated_at": _now_str(),
    }
    save_json(RANGES_STORE_FILE, store)


def _range_entry(store: dict, range_name: str) -> dict:
    ranges = store.setdefault("ranges", {})
    if range_name not in ranges or not isinstance(ranges.get(range_name), dict):
        ranges[range_name] = {
            "requested_total": 0,
            "last_requested_at": "",
            "available_numbers_count": 0,
            "last_numbers_sync_at": "",
            "sample_numbers": [],
            "accounts": {},
        }
    return ranges[range_name]


def record_range_request(store: dict, range_name: str, account_name: str, requested_numbers: int) -> None:
    entry = _range_entry(store, range_name)
    entry["requested_total"] = int(entry.get("requested_total", 0)) + int(requested_numbers)
    entry["last_requested_at"] = _now_str()
    accounts = entry.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
        entry["accounts"] = accounts
    row = accounts.get(account_name) if isinstance(accounts.get(account_name), dict) else {}
    row["requested_total"] = int(row.get("requested_total", 0)) + int(requested_numbers)
    row["last_requested_at"] = _now_str()
    accounts[account_name] = row


def _extract_number_and_range(row: dict) -> tuple[str, str]:
    number = str(row.get("number") or row.get("phone") or row.get("msisdn") or row.get("mobile") or "").strip()
    range_name = str(row.get("range") or row.get("range_name") or row.get("termination") or "UNKNOWN").strip() or "UNKNOWN"
    return number, range_name


def update_ranges_store_from_numbers(store: dict, rows: list[dict]) -> None:
    grouped_numbers: dict[str, set[str]] = defaultdict(set)
    grouped_rows: dict[str, int] = defaultdict(int)

    for row in rows:
        if not isinstance(row, dict):
            continue
        number, range_name = _extract_number_and_range(row)
        grouped_rows[range_name] += 1
        if number:
            grouped_numbers[range_name].add(number)

    for range_name, row_count in grouped_rows.items():
        entry = _range_entry(store, range_name)
        numbers_set = grouped_numbers.get(range_name, set())
        entry["available_numbers_count"] = len(numbers_set) if numbers_set else row_count
        entry["last_numbers_sync_at"] = _now_str()
        if numbers_set:
            entry["sample_numbers"] = sorted(numbers_set)[:20]


def _resolve_targets(api_base: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    accounts = load_active_accounts()
    for acc in accounts:
        name = acc["name"]
        token, login_err = api_login(api_base, acc["email"], acc["password"])
        if not token:
            err(f"{name}: login failed ({login_err})")
            continue
        targets.append((name, token))

    if targets:
        return targets

    env_token = env_value("API_SESSION_TOKEN")
    if is_real_value(env_token):
        targets.append(("session", env_token))
        return targets

    err("no valid account/session token found")
    return targets


def add_range_command(api_base: str, range_name: str, count: int) -> None:
    value = str(range_name or "").strip()
    if not value:
        err("range name is required")
        return
    valid_count, count_err = _validate_request_count(int(count))
    if not valid_count:
        err(count_err)
        return
    store = load_ranges_store()
    entry = _range_entry(store, value)
    max_total = _range_limit_total()
    already_requested = int(entry.get("requested_total", 0) or 0)
    remaining = max_total - already_requested
    if remaining <= 0:
        err(f"range '{value}' reached limit ({max_total}). no remaining numbers.")
        return
    if remaining < 50:
        err(f"range '{value}' remaining from limit: {remaining}. minimum request is 50.")
        return
    if count > remaining:
        allowed = remaining - (remaining % 50)
        if allowed < 50:
            err(f"range '{value}' remaining from limit: {remaining}. minimum request is 50.")
        else:
            err(f"requested {count} exceeds remaining {remaining}. max allowed now is {allowed}.")
        return

    targets = _resolve_targets(api_base)
    if not targets:
        return
    heading(f"Add Range | {value} | count={count}")
    ok(f"limit={max_total} | already={already_requested} | remaining={remaining}")
    calls_needed = count // 50
    for name, token in targets:
        success_calls = 0
        last_err = ""
        for idx in range(1, calls_needed + 1):
            ok_req, payload, req_err = api_post(api_base, "/api/v1/order/range", {"token": token, "range_name": value}, timeout=90)
            if not ok_req:
                last_err = req_err
                err(f"{name}: request failed at batch {idx}/{calls_needed} ({req_err})")
                continue
            success_calls += 1
            if idx == calls_needed:
                msg = str((payload.get("message") if isinstance(payload, dict) else "") or "request submitted").strip()
                ok(f"{name}: batch {idx}/{calls_needed} done ({msg})")

        requested_numbers = success_calls * 50
        if success_calls == calls_needed:
            ok(f"{name}: requested {requested_numbers}/{count} numbers for range '{value}'")
            record_range_request(store, value, name, requested_numbers)
        else:
            err(
                f"{name}: partial success {requested_numbers}/{count} for range '{value}'"
                + (f" | last_error={last_err}" if last_err else "")
            )
            if requested_numbers > 0:
                record_range_request(store, value, name, requested_numbers)
    save_ranges_store(store)
    updated = _range_entry(store, value)
    new_remaining = max_total - int(updated.get("requested_total", 0) or 0)
    ok(f"ranges store updated: {RANGES_STORE_FILE.name} | remaining from limit={max(0, new_remaining)}")


def fetch_numbers_command(api_base: str, update_store: bool = True) -> list[dict]:
    targets = _resolve_targets(api_base)
    if not targets:
        return []
    heading("Fetch Numbers")
    all_rows: list[dict] = []
    for name, token in targets:
        ok_req, payload, req_err = api_post(api_base, "/api/v1/numbers/announce", {"token": token}, timeout=120)
        if not ok_req:
            err(f"{name}: fetch numbers failed ({req_err})")
            continue
        rows = _extract_list_payload(payload)
        all_rows.extend([r for r in rows if isinstance(r, dict)])
        if rows:
            ok(f"{name}: numbers count={len(rows)}")
            for idx, row in enumerate(rows[:10], start=1):
                if isinstance(row, dict):
                    number, range_name = _extract_number_and_range(row)
                    app = str(row.get("app_name") or row.get("service_name") or row.get("app") or "-").strip()
                    printable = number or str(row.get("id") or "-").strip()
                    print(f"  {idx}. {printable} | {app} | {range_name}")
                else:
                    print(f"  {idx}. {row}")
            if len(rows) > 10:
                print(f"  ... +{len(rows) - 10} more")
        else:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload.get("status") or "").strip()
            ok(f"{name}: no numbers ({msg or 'empty response'})")
    if update_store and all_rows:
        store = load_ranges_store()
        update_ranges_store_from_numbers(store, all_rows)
        save_ranges_store(store)
        ok(f"ranges store updated from numbers: {RANGES_STORE_FILE.name}")
    return all_rows


def fetch_traffic_command(api_base: str, app_name: str) -> None:
    targets = _resolve_targets(api_base)
    if not targets:
        return
    app = str(app_name or "WhatsApp").strip() or "WhatsApp"
    heading(f"Fetch Traffic | app={app}")
    for name, token in targets:
        ok_req, payload, req_err = api_post(
            api_base,
            "/api/v1/traffic/services",
            {"token": token, "app_name": app},
            timeout=120,
        )
        if not ok_req:
            err(f"{name}: fetch traffic failed ({req_err})")
            continue
        rows = _extract_list_payload(payload)
        if rows:
            ok(f"{name}: traffic rows={len(rows)} for {app}")
            for idx, row in enumerate(rows[:20], start=1):
                if isinstance(row, dict):
                    rname = str(row.get("range") or row.get("range_name") or "UNKNOWN").strip()
                    cnt = str(row.get("count") or row.get("total") or row.get("messages") or "0").strip()
                    last = str(row.get("last_message_time") or row.get("updated_at") or "-").strip()
                    print(f"  {idx}. {rname} | count={cnt} | last={last}")
                else:
                    print(f"  {idx}. {row}")
            if len(rows) > 20:
                print(f"  ... +{len(rows) - 20} more")
        else:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload.get("status") or "").strip()
            ok(f"{name}: no traffic data ({msg or 'empty response'})")


def fetch_platforms_command(api_base: str) -> None:
    targets = _resolve_targets(api_base)
    if not targets:
        return
    heading("Fetch Platforms")
    for name, token in targets:
        ok_req, payload, req_err = api_post(api_base, "/api/v1/applications/available", {"token": token}, timeout=90)
        if not ok_req:
            err(f"{name}: fetch platforms failed ({req_err})")
            continue
        rows = _extract_list_payload(payload)
        if rows:
            ok(f"{name}: platforms count={len(rows)}")
            for idx, row in enumerate(rows[:30], start=1):
                if isinstance(row, dict):
                    pname = str(row.get("name") or row.get("app_name") or row.get("key") or row.get("service_name") or row).strip()
                else:
                    pname = str(row)
                print(f"  {idx}. {pname}")
            if len(rows) > 30:
                print(f"  ... +{len(rows) - 30} more")
        else:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload.get("status") or "").strip()
            ok(f"{name}: no platforms data ({msg or 'empty response'})")


def show_ranges_store_command() -> None:
    store = load_ranges_store()
    ranges = store.get("ranges") if isinstance(store.get("ranges"), dict) else {}
    heading("Ranges Store")
    if not ranges:
        warn("no ranges data yet")
        return
    rows = sorted(ranges.items(), key=lambda kv: str(kv[0]).lower())
    for idx, (range_name, entry) in enumerate(rows, start=1):
        if not isinstance(entry, dict):
            continue
        req_total = int(entry.get("requested_total", 0) or 0)
        available = int(entry.get("available_numbers_count", 0) or 0)
        last_req = str(entry.get("last_requested_at", "")).strip() or "-"
        last_sync = str(entry.get("last_numbers_sync_at", "")).strip() or "-"
        print(f"{idx}. {range_name} | requested={req_total} | available={available} | req_at={last_req} | sync_at={last_sync}")


def sync_ranges_command(api_base: str, interval_minutes: int, once: bool) -> None:
    if interval_minutes < 1:
        err("interval-minutes must be >= 1")
        return
    heading("Range Sync")
    ok(f"store file: {RANGES_STORE_FILE}")
    ok(f"interval: {interval_minutes} minute(s)")
    while True:
        started = _now_str()
        ok(f"sync started at {started}")
        rows = fetch_numbers_command(api_base, update_store=True)
        ok(f"sync completed | rows={len(rows)} | at={_now_str()}")
        if once:
            return
        sleep_seconds = interval_minutes * 60
        ok(f"sleeping {sleep_seconds} seconds")
        time.sleep(sleep_seconds)


def _daily_store_file(day_key: str) -> Path:
    return DAILY_STORE_DIR / f"messages_{day_key}.json"


def _load_daily_sent_rows(day_key: str) -> list[dict]:
    data = get_daily_store(day_key, {})
    if not isinstance(data, dict):
        return []
    sent = data.get("sent")
    if not isinstance(sent, list):
        return []
    return [row for row in sent if isinstance(row, dict)]


def stats_command(day: str | None, all_days: bool) -> None:
    heading("Stats")
    day_keys: list[str] = []
    if all_days:
        day_keys = sorted(list_daily_store_days())
    else:
        day_key = (day or date.today().isoformat()).strip()
        if not _validate_day(day_key):
            err("invalid day format, expected YYYY-MM-DD")
            return
        day_keys = [day_key]

    sent_rows: list[dict] = []
    used_days: list[str] = []
    for day_key in day_keys:
        try:
            rows = _load_daily_sent_rows(day_key)
            if rows:
                used_days.append(day_key)
                sent_rows.extend(rows)
        except Exception:
            continue

    if not sent_rows:
        warn("no sent messages found for selected range")
        return

    by_service: dict[str, int] = defaultdict(int)
    by_group: dict[str, int] = defaultdict(int)
    unique_numbers: set[str] = set()
    total_revenue = 0.0
    revenue_count = 0
    delivery_count = 0

    for row in sent_rows:
        service = str(row.get("service_name", "unknown")).strip() or "unknown"
        by_service[service] += 1
        number = str(row.get("number", "")).strip()
        if number:
            unique_numbers.add(number)

        revenue = row.get("revenue")
        if isinstance(revenue, (int, float)):
            total_revenue += float(revenue)
            revenue_count += 1
        elif isinstance(revenue, str):
            try:
                total_revenue += float(revenue.strip())
                revenue_count += 1
            except Exception:
                pass

        groups = row.get("groups")
        if isinstance(groups, list):
            for g in groups:
                if isinstance(g, dict):
                    gname = str(g.get("group") or g.get("chat_id") or "unknown").strip()
                    by_group[gname] += 1
                    delivery_count += 1

    top_group = "-"
    if by_group:
        top_group = sorted(by_group.items(), key=lambda kv: kv[1], reverse=True)[0][0]

    if len(used_days) == 1:
        day_label = used_days[0]
    elif used_days:
        day_label = f"{used_days[0]} -> {used_days[-1]}"
    else:
        day_label = (day or date.today().isoformat()).strip()

    print(f"اليوم: {day_label}")
    print(f"اتبعت: {len(sent_rows)} رسالة")
    print(f"وصلت: {delivery_count} مرة")
    print(f"الجروب الأساسي: {top_group}")
    print(f"إجمالي الربح: {round(total_revenue, 4)}")


def balances_command(api_base: str) -> None:
    heading("Balances")
    accounts = load_active_accounts()
    if not accounts:
        err("no enabled accounts found in database")
        return

    for acc in accounts:
        name = acc["name"]
        token, login_err = api_login(api_base, acc["email"], acc["password"])
        if not token:
            err(f"{name}: login failed ({login_err})")
            continue
        balance, _endpoint, bal_err = fetch_account_balance(api_base, token)
        if balance is None:
            err(f"{name}: balance fetch failed ({bal_err})")
            continue
        ok(f"{name}: {balance}")


def add_account(name: str, email: str, password: str, enabled: bool) -> None:
    rows = load_json(ACCOUNTS_FILE, [])
    if not isinstance(rows, list):
        rows = []
    if not _validate_email(email):
        err("invalid email format")
        return
    if not str(password or "").strip():
        err("password is required")
        return
    rows = [x for x in rows if not (x.get("email") == email)]
    rows.append({"name": name, "email": email, "password": password, "enabled": enabled})
    save_json(ACCOUNTS_FILE, rows)
    ok(f"added account: {email}")


def remove_account(name: str | None, email: str | None) -> None:
    n = str(name or "").strip()
    e = str(email or "").strip()
    rows = load_json(ACCOUNTS_FILE, [])
    if not isinstance(rows, list):
        err("accounts data is invalid in database")
        return

    if not n and not e:
        valid_rows = [row for row in rows if isinstance(row, dict)]
        if not valid_rows:
            warn("no accounts found")
            return
        print("Choose account to remove:")
        for idx, row in enumerate(valid_rows, start=1):
            row_name = str(row.get("name", "")).strip() or "-"
            row_email = str(row.get("email", "")).strip() or "-"
            status = "enabled" if bool(row.get("enabled", True)) else "disabled"
            print(f"{idx}) {row_name} | {row_email} | {status}")
        picked = _ask("Account number to remove")
        if not picked.isdigit():
            err("invalid selection")
            return
        selected_index = int(picked)
        if selected_index < 1 or selected_index > len(valid_rows):
            err("invalid selection")
            return
        selected = valid_rows[selected_index - 1]
        n = str(selected.get("name", "")).strip()
        e = str(selected.get("email", "")).strip()

    before = len(rows)
    kept = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        row_name = str(row.get("name", "")).strip()
        row_email = str(row.get("email", "")).strip()
        matched = False
        if n and row_name == n:
            matched = True
        if e and row_email == e:
            matched = True
        if not matched:
            kept.append(row)

    removed = before - len(kept)
    if removed <= 0:
        warn("no matching account found")
        return
    save_json(ACCOUNTS_FILE, kept)
    ok(f"removed accounts: {removed}")


def add_group(name: str, chat_id: str, enabled: bool) -> None:
    rows = load_json(GROUPS_FILE, [])
    if not isinstance(rows, list):
        rows = []
    if not _validate_chat_id(chat_id):
        err("invalid chat_id format (expected: -100xxxxxxxxxx)")
        return
    rows = [x for x in rows if not (str(x.get("chat_id")) == str(chat_id))]
    rows.append({"name": name, "chat_id": str(chat_id), "enabled": enabled})
    save_json(GROUPS_FILE, rows)
    ok(f"added group: {chat_id}")


def clear_store(start_date: str | None) -> None:
    if start_date:
        if not _validate_day(start_date):
            err("invalid start date, expected YYYY-MM-DD")
            return
        rows = _load_daily_sent_rows(start_date)
        if rows:
            delete_daily_store(start_date)
            ok(f"cleared daily store for day={start_date}")
        else:
            warn(f"no daily store found for day={start_date}")
        return

    clear_daily_store()
    save_json(STORE_FILE, {"by_start_date": {}})
    ok("cleared all stored messages")


def list_accounts() -> None:
    heading("Accounts")
    rows = load_json(ACCOUNTS_FILE, [])
    if not isinstance(rows, list) or not rows:
        warn("no accounts found")
        return
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip() or "-"
        email = str(row.get("email", "")).strip() or "-"
        status = "enabled" if bool(row.get("enabled", True)) else "disabled"
        print(f"{idx}. {name} | {email} | {status}")


def list_groups() -> None:
    heading("Groups")
    rows = load_json(GROUPS_FILE, [])
    if not isinstance(rows, list) or not rows:
        warn("no groups found")
        return
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip() or "-"
        chat_id = str(row.get("chat_id", "")).strip() or "-"
        status = "enabled" if bool(row.get("enabled", True)) else "disabled"
        print(f"{idx}. {name} | {chat_id} | {status}")


def set_platform_emoji_id(key: str, emoji_id: str) -> None:
    rows = load_json(PLATFORMS_FILE, [])
    updated = False
    for row in rows:
        if str(row.get("key", "")).strip().lower() == key.strip().lower():
            row["emoji_id"] = emoji_id.strip()
            updated = True
            break
    if not updated:
        rows.append(
            {
                "key": key.strip().lower(),
                "name_ar": key,
                "name_en": key,
                "short": key[:2].upper(),
                "emoji": "",
                "emoji_id": emoji_id.strip(),
            }
        )
    save_json(PLATFORMS_FILE, rows)
    ok(f"set emoji_id for platform '{key}'")


def _ask(prompt: str, default: str | None = None) -> str:
    if default is None:
        return input(f"{prompt}: ").strip()
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def interactive_menu() -> None:
    load_dotenv(BASE_DIR / ".env")
    while True:
        heading("Bot CLI Menu")
        print(" 1) Add account")
        print(" 2) Add group")
        print(" 3) List accounts")
        print(" 4) List groups")
        print(" 5) Remove account")
        print(" 6) Stats")
        print(" 7) Balances (all accounts)")
        print(" 8) Add range")
        print(" 9) Fetch numbers")
        print("10) Fetch traffic")
        print("11) Fetch platforms")
        print("12) Show ranges store")
        print("13) Sync ranges (every 30 min)")
        print("14) Exit")
        choice = input("Choose (1-14): ").strip()

        if choice == "1":
            name = _ask("Account name")
            email = _ask("Email")
            password = _ask("Password")
            enabled_raw = _ask("Enabled? (y/n)", "y").lower()
            add_account(name, email, password, enabled=enabled_raw != "n")
        elif choice == "2":
            name = _ask("Group name")
            chat_id = _ask("Telegram chat_id (example: -1001234567890)")
            enabled_raw = _ask("Enabled? (y/n)", "y").lower()
            add_group(name, chat_id, enabled=enabled_raw != "n")
            print("Run bot.py and messages will be sent to enabled groups.")
        elif choice == "3":
            list_accounts()
        elif choice == "4":
            list_groups()
        elif choice == "5":
            remove_account(name=None, email=None)
        elif choice == "6":
            mode = _ask("All days? (y/n)", "n").lower().strip()
            if mode == "y":
                stats_command(None, all_days=True)
            else:
                day_key = _ask("Day YYYY-MM-DD", date.today().isoformat())
                stats_command(day_key, all_days=False)
        elif choice == "7":
            api_base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
            if not is_real_value(api_base):
                api_base = _ask("API base URL", "http://127.0.0.1:8000")
            balances_command(api_base)
        elif choice == "8":
            api_base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
            if not is_real_value(api_base):
                api_base = _ask("API base URL", "http://127.0.0.1:8000")
            range_name = _ask("Range name")
            count_raw = _ask("Count (multiple of 50, max 1000)", "50")
            if not count_raw.isdigit():
                err("count must be a number")
                continue
            add_range_command(api_base, range_name, int(count_raw))
        elif choice == "9":
            api_base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
            if not is_real_value(api_base):
                api_base = _ask("API base URL", "http://127.0.0.1:8000")
            fetch_numbers_command(api_base)
        elif choice == "10":
            api_base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
            if not is_real_value(api_base):
                api_base = _ask("API base URL", "http://127.0.0.1:8000")
            app_name = _ask("App name", "WhatsApp")
            fetch_traffic_command(api_base, app_name)
        elif choice == "11":
            api_base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
            if not is_real_value(api_base):
                api_base = _ask("API base URL", "http://127.0.0.1:8000")
            fetch_platforms_command(api_base)
        elif choice == "12":
            show_ranges_store_command()
        elif choice == "13":
            api_base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
            if not is_real_value(api_base):
                api_base = _ask("API base URL", "http://127.0.0.1:8000")
            interval_raw = _ask("Interval minutes", "30")
            if not interval_raw.isdigit():
                err("interval must be a number")
                continue
            sync_ranges_command(api_base, int(interval_raw), once=False)
        elif choice == "14":
            ok("bye")
            return
        else:
            err("invalid choice")


def main() -> int:
    load_dotenv(BASE_DIR / ".env")

    p = argparse.ArgumentParser(
        description="Manage bot accounts, groups, stats and balances",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    p_add_acc = sub.add_parser("add-account", aliases=["acc-add"])
    p_add_acc.add_argument("--name", required=True)
    p_add_acc.add_argument("--email", required=True)
    p_add_acc.add_argument("--password", required=True)
    p_add_acc.add_argument("--disabled", action="store_true")

    p_add_grp = sub.add_parser("add-group", aliases=["grp-add"])
    p_add_grp.add_argument("--name", required=True)
    p_add_grp.add_argument("--chat-id", required=True)
    p_add_grp.add_argument("--disabled", action="store_true")

    p_clear = sub.add_parser("clear-store")
    p_clear.add_argument("--start-date")

    sub.add_parser("list-accounts")
    sub.add_parser("list-groups")

    p_set_emoji = sub.add_parser("set-platform-emoji-id", aliases=["set-emoji"])
    p_set_emoji.add_argument("--key", required=True)
    p_set_emoji.add_argument("--emoji-id", required=True)

    p_remove_acc = sub.add_parser("remove-account", aliases=["acc-rm"])
    p_remove_acc.add_argument("--name")
    p_remove_acc.add_argument("--email")

    p_stats = sub.add_parser("stats", aliases=["st"])
    p_stats.add_argument("--day", help="YYYY-MM-DD")
    p_stats.add_argument("--all-days", action="store_true")

    p_balances = sub.add_parser("balances", aliases=["bal"])
    p_balances.add_argument("--api-base", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))

    p_add_range = sub.add_parser("add-range", aliases=["ar", "range-add"])
    p_add_range.add_argument("--range-name", required=True)
    p_add_range.add_argument("--count", required=True, type=int, help="Requested numbers count (multiple of 50, max 1000)")
    p_add_range.add_argument("--api-base", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))

    p_fetch_numbers = sub.add_parser("fetch-numbers", aliases=["fn", "numbers"])
    p_fetch_numbers.add_argument("--api-base", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))

    p_fetch_traffic = sub.add_parser("fetch-traffic", aliases=["ft", "traffic"])
    p_fetch_traffic.add_argument("--app-name", default="WhatsApp")
    p_fetch_traffic.add_argument("--api-base", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))

    p_fetch_platforms = sub.add_parser("fetch-platforms", aliases=["fp", "platforms"])
    p_fetch_platforms.add_argument("--api-base", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))

    sub.add_parser("show-ranges", aliases=["sr", "ranges"])

    p_sync_ranges = sub.add_parser("sync-ranges", aliases=["sync"])
    p_sync_ranges.add_argument("--api-base", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))
    p_sync_ranges.add_argument("--interval-minutes", type=int, default=30)
    p_sync_ranges.add_argument("--once", action="store_true")

    args = p.parse_args()
    if not args.cmd:
        interactive_menu()
        return 0

    if args.cmd in ("add-account", "acc-add"):
        add_account(args.name, args.email, args.password, enabled=not args.disabled)
    elif args.cmd in ("add-group", "grp-add"):
        add_group(args.name, args.chat_id, enabled=not args.disabled)
    elif args.cmd == "clear-store":
        clear_store(args.start_date)
    elif args.cmd == "list-accounts":
        list_accounts()
    elif args.cmd == "list-groups":
        list_groups()
    elif args.cmd in ("set-platform-emoji-id", "set-emoji"):
        set_platform_emoji_id(args.key, args.emoji_id)
    elif args.cmd in ("remove-account", "acc-rm"):
        remove_account(args.name, args.email)
    elif args.cmd in ("stats", "st"):
        stats_command(args.day, args.all_days)
    elif args.cmd in ("balances", "bal"):
        balances_command(args.api_base)
    elif args.cmd in ("add-range", "ar", "range-add"):
        add_range_command(args.api_base, args.range_name, args.count)
    elif args.cmd in ("fetch-numbers", "fn", "numbers"):
        fetch_numbers_command(args.api_base)
    elif args.cmd in ("fetch-traffic", "ft", "traffic"):
        fetch_traffic_command(args.api_base, args.app_name)
    elif args.cmd in ("fetch-platforms", "fp", "platforms"):
        fetch_platforms_command(args.api_base)
    elif args.cmd in ("show-ranges", "sr", "ranges"):
        show_ranges_store_command()
    elif args.cmd in ("sync-ranges", "sync"):
        sync_ranges_command(args.api_base, args.interval_minutes, args.once)
    else:
        err("unknown command")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
