# Project Structure

- `main.py`: runs sender bot + panel bot together.
- `bot.py`: wrapper entrypoint for sender bot.
- `panel_bot.py`: wrapper entrypoint for control panel bot.
- `cli.py`: wrapper entrypoint for admin CLI.

## Apps Layer
- `apps/sender_bot.py`: sender bot implementation.
- `apps/panel_bot.py`: panel bot implementation.
- `apps/admin_cli.py`: CLI implementation.

## App Layer
- `app/paths.py`: central paths/constants.
- `app/storage.py`: SQLite-backed JSON/daily store with legacy migration.

## Runtime Data
- `data/storage.db`: main database.
- `logs/`: logs.
- `exports/`: exported files.

## Legacy Import
- استخدم `scripts/migrate_json_to_db.py` لترحيل أي JSON قديمة إلى DB.
- Backups تحفظ في: `data/legacy_json_backup/`
