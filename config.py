# -*- coding: utf-8 -*-
"""
==============================================
  تنظیمات ربات بازنشر تلگرام
  • اجرای محلی: این فایل رو باز کن و پر کن
  • اجرای ابری (سرور رایگان): همه این موارد رو
    می‌تونی به‌جای فایل، به‌صورت Secret/متغیر محیطی بدی:
    API_ID، API_HASH، PHONE، DEST_CHANNEL، SOURCES،
    SESSION_STRING، CLOUD_BACKUP، BACKUP_INTERVAL_HOURS
==============================================
"""

import logging
import os

# ------------------------------------------------
# بارگذاری خودکار فایل «.env» کنار ربات (مخصوص سرور serv00)
#   • مقادیر داخل فایل، فقط وقتی پر میشن که متغیر محیطی‌ای ست نشده باشه
#   • روی سرورهای دیگه (Hugging Face و ...) اگر فایل .env نباشد، هیچ اثری ندارد
#   • رشتهٔ نشست را فقط داخل .env بگذار و هرگز جایی منتشرش نکن
# ------------------------------------------------
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
              encoding="utf-8") as _env_file:
        for _env_line in _env_file:
            _env_line = _env_line.strip()
            if _env_line and not _env_line.startswith("#") and "=" in _env_line:
                _k, _v = _env_line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
except OSError:
    pass  # فایل .env وجود ندارد — اشکالی ندارد


def _env(key, default=""):
    return os.getenv(key, default) or default


def _env_int(key, default=0):
    v = os.getenv(key)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def _env_bool(key, default=False):
    v = os.getenv(key)
    if v is None or v == "":
        return default
    return v.lower() in ("1", "true", "yes", "on")


# ------------------------------------------------
# ۱) اطلاعات API — از پیش تنظیم شده، نیازی به گرفتن نداری!
#    این کلیدِ رسمیِ متن‌باز اپ تلگرام‌اندرویده (همونی که
#    میلیون‌ها نصب تلگرام ازش استفاده می‌کنن) و برای
#    یوزربات شخصی کار می‌کنه؛ یعنی لازم نیست برو
#    my.telegram.org (که از ایران بلاک شده).
#    اگه روزی کلید شخصی خودت رو گرفتی، همین‌جا عوضش کن
#    یا با Secret محیطی API_ID / API_HASH بازنویسیش کن.
# ------------------------------------------------
API_ID = _env_int("API_ID", 6)
API_HASH = _env("API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e")

# ------------------------------------------------
# ۲) شماره موبایل اکانت یوزربات (فقط برای ورود محلی)
#    فرمت بین‌المللی، مثال:  +989123456789
#    اگه خالی بذاری موقع اجرا خودش می‌پرسه
# ------------------------------------------------
PHONE = _env("PHONE")

# ------------------------------------------------
# ۳) نشست آماده (برای سرورهای ابری — بدون ورود مجدد)
#    روی Hugging Face نیازی به این Secret نیست: اولین بار که Space
#    بالا بیاد، یه صفحهٔ ورود وب نشون میده (شماره → کد → 2FA) و
#    خودش رشته رو می‌سازه. اگه رشته رو به‌عنوان Secret با همین نام
#    ثبت کنی، دیگه صفحهٔ ورود نمیاد و مستقیم ربات بالا میاد.
#    (روش جایگزین برای سرویس‌های دیگه: python gen_session.py)
# ------------------------------------------------
SESSION_STRING = _env("SESSION_STRING")

# فایلی که صفحهٔ ورود وب بعد از اولین لاگین می‌سازه
SESSION_FILE = _env("SESSION_FILE", "session_string.txt")


def sanitize_session_string(raw):
    """پاکسازی خطاهای رایج کپی/پیست در رشتهٔ نشست

    حتی یک فاصله، خط جدید یا گیومه اضافه باعث ارور Telethon با پیام
    «unpack requires a buffer of 275 bytes» می‌شود (چون Telethon طول رشته
    را قبل از حذف کاراکترهای خارج از base64 چک می‌کند). این تابع:
      • همهٔ فاصله‌ها و خط‌های جدید (اول/وسط/آخر) را حذف می‌کند
      • اگر کل خط «SESSION_STRING=...» پیست شده باشد پیشوند را برمی‌دارد
      • گیومه‌های ابتدا و انتها را حذف می‌کند
    """
    if not raw:
        return ""
    s = "".join(str(raw).split())
    if s.lower().startswith("session_string="):
        s = s.split("=", 1)[1]
    return s.strip("\"'").strip()


def validate_session_string(s):
    """بررسی ساختاری رشتهٔ نشست → (سالم؟، جزئیات خطا)

    Telethon هنگام تجزیهٔ رشتهٔ خراب struct.error/ValueError می‌دهد؛
    اینجا همان تجزیه داخل try انجام می‌شود تا پیام واضح داشته باشیم.
    """
    if not s:
        return False, "empty"
    try:
        from telethon.sessions import StringSession
        StringSession(s)
        return True, ""
    except Exception as e:  # noqa: BLE001 - هر خطای تجزیه یعنی رشته خراب است
        return False, "%s: %s" % (type(e).__name__, e)


def load_session_string():
    """اول Secret محیطی، وگرنه فایل session_string.txt

    نکته مهم: اگر Secret خراب باشد ولی فایلِ ساخته‌شده توسط صفحهٔ ورود
    سالم باشد، همان فایل استفاده می‌شود تا ربات بالا بیاید (با لاگ هشدار)،
    چون بعد از Factory reboot فایل حذف می‌شود و فقط Secret می‌ماند.
    """
    env_val = sanitize_session_string(
        SESSION_STRING or os.getenv("SESSION_STRING", ""))
    file_val = ""
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            file_val = sanitize_session_string(f.read() or "")
    except OSError:
        pass

    if env_val and file_val:
        env_ok, _ = validate_session_string(env_val)
        file_ok, _ = validate_session_string(file_val)
        if env_ok:
            return env_val
        if file_ok:
            logging.getLogger("config").warning(
                "The SESSION_STRING secret is corrupted - using the valid "
                "session file (%s) instead. Fix the secret before the next "
                "restart!", SESSION_FILE)
            return file_val
    return env_val or file_val

# ------------------------------------------------
# ۴) کانال مقصد (کانال خودت که پست‌ها توش منتشر میشن)
#    مثال:  "@mychannel"
#    نکته: اکانت ربات باید توی این کانال دسترسی ارسال داشته باشه
#    (بعداً داخل تلگرام هم می‌تونی با دستور /setdest عوضش کنی)
# ------------------------------------------------
DEST_CHANNEL = _env("DEST_CHANNEL")

# ------------------------------------------------
# ۵) کانال‌های منبع اولیه (اختیاری)
#    روی سرور ابری می‌تونی با متغیر SOURCES بدی (با کاما جدا کن)
#    بعداً با دستور /add و /remove داخل تلگرام هم قابل مدیریت هست
# ------------------------------------------------
_src_env = os.getenv("SOURCES", "")
if _src_env:
    SOURCE_CHANNELS = [s.strip() for s in _src_env.split(",") if s.strip()]
else:
    SOURCE_CHANNELS = [
        # "@example_channel1",
        # "@example_channel2",
    ]

# ------------------------------------------------
# ۶) پروکسی (برای اجرا داخل ایران لازمه!)
#    تلگرام توی ایران فیلتره و telethon برخلاف اپ تلگرام
#    خودش پروکسی نداره. اگه ارور «Connection to Telegram
#    failed» دیدی، یکی از دو حالت زیر رو پر کن:
#
#    حالت A — MTProto Proxy (همون پروکسی اپ تلگرامت):
#       توی اپ تلگرام: Settings → Data and Storage → Proxy
#       → روی پروکسی فعالت بزن و Server/Port/Secret رو بردار
#       PROXY_TYPE   = "mtproto"
#       PROXY_HOST   = "آدرس سرور پروکسی"
#       PROXY_PORT   = 443
#       PROXY_SECRET = "همون secret لینک t.me/proxy (با ee/dd)"
#
#    حالت B — پروکسی محلی v2rayN / Nekoray / Hiddify:
#       PROXY_TYPE   = "socks5"      (یا "http")
#       PROXY_HOST   = "127.0.0.1"
#       PROXY_PORT   = 10808         (v2rayN: 10808، Nekoray: 2080، Hiddify: 12334)
#
#    روی سرور خارج از ایران (مثل Hugging Face) همه رو خالی بذار
# ------------------------------------------------
PROXY_TYPE = _env("PROXY_TYPE")
PROXY_HOST = _env("PROXY_HOST")
PROXY_PORT = _env_int("PROXY_PORT", 0)
PROXY_SECRET = _env("PROXY_SECRET")

# ===============================================
#        ظاهر پست‌ها (لینک منبع)
# ===============================================

# متن دکمه شیشه‌ای منبع (زیر هر پست عادی نمایش داده میشه)
SOURCE_BUTTON_TEXT = _env("SOURCE_BUTTON_TEXT", "📺 منبع")

# برای آلبوم‌ها (پست‌های چندعکسی) تلگرام اجازه دکمه نمیده،
# به جاش این خط متنی ته کپشن اضافه میشه. {link} جای لینک میشینه
SOURCE_LINK_TEXT = _env("SOURCE_LINK_TEXT", "🔗 منبع: {link}")

# ===============================================
#        ضد تکرار
# ===============================================
DEDUP_BY_TEXT = _env_bool("DEDUP_BY_TEXT", True)    # متنِ یکسان = تکراری
DEDUP_BY_MEDIA = _env_bool("DEDUP_BY_MEDIA", True)  # فایلِ یکسان = تکراری

# ===============================================
#        پاکسازی هوشمند تبلیغات
# ===============================================
ENABLE_TEXT_CLEANING = _env_bool("ENABLE_TEXT_CLEANING", True)

# خط‌هایی که فقط لینک / @آیدی / #هشتگ / ایموجی دارن حذف میشن
REMOVE_HASHTAG_LINES = _env_bool("REMOVE_HASHTAG_LINES", True)

# کلیدواژه‌های تبلیغاتی: خط‌هایی که این کلمه‌ها رو داشته باشن
# (و لینک یا @ داشته باشن، یا خیلی کوتاه باشن) حذف میشن
AD_KEYWORDS = [
    "تبلیغ", "تبلیغات", "ادمین", "پشتیبانی", "همکاری",
    "عضویت", "جوین", "کانال اصلی", "کانال ما", "پیج ما",
    "فالو", "ads", "admin", "support",
]

# اگه پاکسازی بیش از این نسبت از متن رو حذف کنه،
# ربات محتاط میشه و متن اصلی رو دست‌نخورده می‌ذاره (0 تا 1)
CLEAN_MAX_RATIO = float(_env("CLEAN_MAX_RATIO", "0.7") or 0.7)

# ===============================================
#        محدودیت سرعت
# ===============================================
MIN_SEND_INTERVAL = _env_int("MIN_SEND_INTERVAL", 15)              # حداقل فاصله پست‌ها (ثانیه)
MAX_POSTS_PER_HOUR_PER_SOURCE = _env_int("MAX_POSTS_PER_HOUR_PER_SOURCE", 25)
MAX_MESSAGE_AGE_MINUTES = _env_int("MAX_MESSAGE_AGE_MINUTES", 15)

# ===============================================
#        فایل‌ها
# ===============================================
SESSION_NAME = _env("SESSION_NAME", "reposter_session")  # فایل نشست (اجرای محلی)
DB_FILE = _env("DB_FILE", "reposter.db")                 # دیتابیس ضد تکرار
LOG_FILE = _env("LOG_FILE", "reposter.log")              # لاگ ربات
ALBUM_WAIT_SECONDS = float(_env("ALBUM_WAIT_SECONDS", "2.0") or 2.0)

# ===============================================
#        حالت ابری (سرور رایگان)
# ===============================================
# پورت سرور پینگ سلامت:
#   • Render/Koyeb خودشون PORT رو ست می‌کنن → خودکار فعال میشه
#   • Hugging Face Space: متغیر SPACE_ID هست → پورت 7860
#   • اجرای محلی: خاموش (0)
_PORT_RAW = os.getenv("PORT") or os.getenv("KEEPALIVE_PORT") \
    or ("7860" if os.getenv("SPACE_ID") else "0")
KEEPALIVE_PORT = _env_int("KEEPALIVE_PORT", int(_PORT_RAW or 0))

# بکاپ خودکار دیتابیس ضد تکرار در «پیام‌های ذخیره‌شده» تلگرام.
# روی سرور ابری که فایل‌ش بعد از هر ری‌استارت پاک میشه، به‌صورت
# پیش‌فرض روشنه تا تاریخچه ضد تکرار و منابع از دست نره
_cloud_detected = bool(os.getenv("PORT") or os.getenv("SPACE_ID") or SESSION_STRING)
CLOUD_BACKUP = _env_bool("CLOUD_BACKUP", _cloud_detected)

# هر چند ساعت یک بار بکاپ گرفته بشه
BACKUP_INTERVAL_HOURS = _env_int("BACKUP_INTERVAL_HOURS", 6)


def client_kwargs():
    """آرگومان‌های اضافه TelegramClient بر اساس تنظیمات پروکسی

    فرمت‌ها از سورس telethon تایید شده:
      • socks/http : proxy = (proxy_type, addr, port)  ← نوع اول!
        (network/connection.py → _parse_proxy(proxy_type, addr, port, ...))
      • mtproto    : proxy = (host, port, secret)
        (network/connection/tcpmtproxy.py → address_info = proxy[:2], secret = proxy[2])
    """
    ptype = (PROXY_TYPE or "").strip().lower()
    if not ptype:
        return {}

    if ptype in ("socks5", "socks4", "socks", "sock5", "http"):
        if not PROXY_HOST or not PROXY_PORT:
            raise ValueError(
                "Proxy is enabled but PROXY_HOST/PROXY_PORT are empty. "
                "Example: PROXY_HOST = \"127.0.0.1\" and PROXY_PORT = 10808")
        ptype = "socks5" if ptype.startswith("sock") else "http"
        return {"proxy": (ptype, PROXY_HOST, PROXY_PORT)}

    if ptype == "mtproto":
        if not PROXY_HOST or not PROXY_PORT or not PROXY_SECRET:
            raise ValueError(
                "MTProto proxy needs PROXY_HOST, PROXY_PORT and PROXY_SECRET "
                "(the secret from the t.me/proxy?secret=... link)")
        # سکرت رو همون‌طور که توی لینک t.me/proxy هست بده (با پیشوند ee/dd)؛
        # telethon خودش نرمالایزش می‌کنه
        from telethon import connection as _connection
        return {
            "connection": _connection.ConnectionTcpMTProxyRandomizedIntermediate,
            "proxy": (PROXY_HOST, PROXY_PORT, PROXY_SECRET),
        }

    raise ValueError(
        "Unknown PROXY_TYPE '%s' (use \"mtproto\", \"socks5\" or \"http\")" % ptype)
