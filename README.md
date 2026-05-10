# NumPlus Telegram Bot Client

بوت يقرأ رسائل OTP من API ويرسل الرسائل الجديدة إلى جروبات تيليجرام.

## المتطلبات
- Python 3.10+
- ملف `requirements.txt`

## التثبيت
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## ملفات الإعداد

### 1) `.env`
ملف متغيرات البيئة الأساسية (توكن البوت، رابط API، تاريخ البداية الافتراضي...)

مثال توضيحي:
```env
API_BASE_URL=https://your-api-domain.example.com
API_START_DATE=2025-01-01
API_SESSION_TOKEN=
TELEGRAM_BOT_TOKEN=123456789:EXAMPLE_BOT_TOKEN
TELEGRAM_CHAT_ID=-1001234567890
BOT_LIMIT=30
USE_CUSTOM_EMOJI=0
LOG_LEVEL=INFO
```

### 2) التخزين
- كل البيانات الآن محفوظة في قاعدة SQLite:
  - `data/storage.db`
- لا حاجة لملفات JSON للتشغيل.

## CLI سهل (تفاعلي)
شغّل:
```bash
python cli.py
```
ستظهر قائمة اختيار مباشرة:
1. Add account
2. Add group
3. List accounts
4. List groups
5. Remove account
6. Stats
7. Balances (all accounts)
8. Exit

مهم:
- عند اختيار `Add group` وحفظ `chat_id` بشكل صحيح، البوت يقرأه من قاعدة البيانات ويرسل له تلقائيًا.

## أوامر CLI المباشرة
```bash
python cli.py add-account --name acc1 --email you@example.com --password "YOUR_PASSWORD"
python cli.py add-group --name main --chat-id -1001234567890
python cli.py list-accounts
python cli.py list-groups
python cli.py clear-store
python cli.py clear-store --start-date 2025-01-01
python cli.py set-platform-emoji-id --key whatsapp --emoji-id 5472096095280572227
python cli.py remove-account --name acc1
python cli.py stats --all-days
python cli.py balances --api-base https://api.elwe.qzz.io
python cli.py add-range --range-name "EGYPT 3805" --count 100 --api-base https://api.elwe.qzz.io
python cli.py fetch-numbers --api-base https://api.elwe.qzz.io
python cli.py fetch-traffic --app-name WhatsApp --api-base https://api.elwe.qzz.io
python cli.py fetch-platforms --api-base https://api.elwe.qzz.io
python cli.py show-ranges
python cli.py sync-ranges --api-base https://api.elwe.qzz.io --once
python cli.py sync-ranges --api-base https://api.elwe.qzz.io --interval-minutes 30
```

ملاحظات `add-range`:
- `--count` لازم يكون مضاعف `50`.
- كل `50` = طلب واحد للـ API.
- مثال: `100` يرسل طلبين.
- الحد الأقصى لكل رينج: `1000`.
- لو طلبت أكثر من المتبقي من الحد، الأمر يترفض ويعرض المتبقي الحالي.
- لو المتبقي أقل من `50`، الأمر يترفض ويعرض كم متبقي من الحد.

## التخزين (DB)
- التخزين الرئيسي أصبح في SQLite:
  - `data/storage.db`
- يوجد ترحيل تلقائي من ملفات JSON القديمة عند أول تشغيل.
- ملفات JSON القديمة ما زالت مدعومة كـ bootstrap للتهيئة الأولى.

## ملف الرينجات والمتابعة
- يتم حفظ بيانات الرينجات المطلوبة تلقائيًا داخل قاعدة البيانات.
- الملف يحتوي:
  - إجمالي الأرقام المطلوبة لكل `range`.
  - عدد الأرقام المتاحة حاليًا في كل `range`.
  - وقت آخر طلب ووقت آخر مزامنة.
- أمر `sync-ranges` يقوم بجلب الأرقام وتحديث الملف.
- الوضع الافتراضي كل `30` دقيقة، ويمكن تشغيل دورة واحدة عبر `--once`.

## Aliases (تلقيب الأوامر)
- `balances` -> `bal`
- `stats` -> `st`
- `add-range` -> `ar`
- `fetch-numbers` -> `fn`
- `fetch-traffic` -> `ft`
- `fetch-platforms` -> `fp`
- `show-ranges` -> `sr`
- `sync-ranges` -> `sync`

## التشغيل
تشغيل مستمر:
```bash
python bot.py
```

تشغيل دورة واحدة ثم خروج:
```bash
python bot.py --once
```

## بوت لوحة التحكم بالأزرار
لتشغيل بوت التيليجرام التفاعلي (لوحة الأزرار):
```bash
python panel_bot.py
```

الأدمن المسموح له بالتحكم:
- افتراضيًا: `7011309417`
- ويمكن تخصيصه من `.env`:
```env
PANEL_ADMIN_IDS=7011309417
```
(يمكن إضافة أكثر من ID بفواصل)

تشغيل البوتين معًا (الإرسال + التحكم) بملف واحد:
```bash
python main.py
```

اللوحة الرئيسية تحتوي:
- زر جلب الاكواد (مفعل/مغلق) ويتحكم مباشرة في سحب الأكواد من `bot.py` عبر `runtime_config.json`.
- الترافيك + المنصات المتاحة.
- ارقام + رصيدي.
- حساباتي (إضافة/حذف/تعديل/عرض).

تدفقات إضافية:
- الترافيك: اختيار المنصة ثم عرض الرينج/عدد الرسائل/آخر رسالة + زر نسخ للرينج + رجوع.
- الأرقام: عرض الأرقام مع زر نسخ للـ ID، وتصدير (`txt/csv/json`) شامل أو مخصص (رينج/دولة)، وطلب أرقام، وحذف أرقام من نص أو ملف.

## ملاحظات التشغيل
- البوت يسأل عن `Start date` كل مرة تشغيل.
- باقي القيم الأساسية تُقرأ من `.env`.
- إذا كان عندك حسابات صالحة في `accounts.json` لا تحتاج `API_SESSION_TOKEN` غالبًا.
- اللوجز تُحفظ في `logs/bot.log` (مع تدوير يومي تلقائي).
- ملفات JSON القديمة يمكن ترحيلها وحذفها عبر:
```bash
python scripts/migrate_json_to_db.py --delete-json
```
