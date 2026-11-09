# -*- coding: utf-8 -*-
"""
نقطه شروع ربات بازنشر تلگرام

اجرای محلی:   python main.py          (اولین بار شماره و کد می‌پرسه)
اجرای ابری:   اگه SESSION_STRING ست شده باشه مستقیم ربات بالا میاد؛
              وگرنه یه صفحهٔ ورود وب (شماره → کد → 2FA) سرو میشه تا
              از مرورگر لاگین کنی و رشته ساخته بشه — بدون Colab!
راهنمای کامل: README.md
"""

import asyncio
import logging
import os
import sys

# فایل‌های ساخته‌شده (دیتابیس/نشست/لاگ) همیشه کنار همین اسکریپت ذخیره بشن
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

import config  # noqa: E402
import database as db  # noqa: E402
from handlers import Reposter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("main")


def check_config():
    problems = []
    if not isinstance(config.API_ID, int) or config.API_ID <= 0 or config.API_ID == 12345:
        problems.append("API_ID is not set (config.py or API_ID env variable).")
    if not config.API_HASH or "0123456789abcdef" in config.API_HASH:
        problems.append("API_HASH is not set (config.py or API_HASH env variable).")
    if problems:
        print()
        print("=" * 56)
        for p in problems:
            print("  ! " + p)
        print("  See README.md for how to get API_ID and API_HASH.")
        print("=" * 56)
        print()
        return False
    return True


def _cloud_mode():
    """آیا روی سرور ابری اجرا میشیم؟ (Hugging Face: SPACE_ID، Render: PORT)"""
    return bool(config.KEEPALIVE_PORT or os.getenv("SPACE_ID"))


async def connect_client():
    """ساخت کلاینت تلگرام — محلی: ورود تعاملی، ابری: با SESSION_STRING"""
    kwargs = config.client_kwargs()
    if config.PROXY_TYPE:
        log.info("Using proxy: %s (%s:%s)",
                 config.PROXY_TYPE, config.PROXY_HOST, config.PROXY_PORT)
    session_str = config.load_session_string()
    if session_str:
        ok, detail = config.validate_session_string(session_str)
        if not ok:
            raise RuntimeError(
                "The SESSION_STRING is corrupted (%s) - usually an extra "
                "space/quote or an incomplete copy/paste. Fix: Space page > "
                "Settings > Variables and secrets > delete the SESSION_STRING "
                "secret > Factory reboot > log in again on the app page and "
                "copy the new string with the copy button." % detail)
        session = StringSession(session_str)
        client = TelegramClient(session, config.API_ID, config.API_HASH, **kwargs)
        log.info("Connecting with SESSION_STRING ...")
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "SESSION_STRING is invalid or expired - log in again through "
                "the web login page (delete the SESSION_STRING secret and "
                "Factory reboot the Space, or run gen_session.py locally).")
        return client
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH, **kwargs)
    log.info("Logging in to Telegram ...")
    if config.PHONE:
        await client.start(phone=config.PHONE)
    else:
        # شماره توی config نیست → بذار خود telethon تعاملی بپرسه.
        # نکته: اگه صریحاً phone=None بدیم، telethon به‌جای پرسیدن ValueError میده؛
        # پس بدون آرگومان صدا می‌زنیم تا مقدار پیش‌فرضش (input) فعال بشه
        await client.start()
    return client


async def run_bot(client):
    """راه‌اندازی کامل ربات روی یک کلاینت آماده (لاگین‌شده)"""
    me = await client.get_me()

    # اگه از مسیر صفحهٔ ورود وب اومدیم، وضعیت رو اونجا به‌روز کن
    try:
        import weblogin
        weblogin.set_bot_status("⏳ Restoring database and starting services…")
    except ImportError:
        weblogin = None

    # بازیابی دیتابیس از بکاپ تلگرام (قبل از باز کردن دیتابیس)
    restored = False
    if config.CLOUD_BACKUP:
        try:
            import backup as backup_mod
            restored = await backup_mod.restore_backup(client)
        except Exception:
            log.exception("Backup restore failed")

    db.init()
    bot = Reposter(client)
    await bot.setup()

    if config.CLOUD_BACKUP:
        try:
            import backup as backup_mod
            asyncio.create_task(backup_mod.periodic_backup(client))
        except Exception:
            log.exception("Periodic backup failed to start")

    if weblogin is not None:
        weblogin.set_bot_status("✅ Bot is running — you can close this page now.")

    print()
    print("=" * 56)
    print("  ✅ Reposter bot is running!")
    print(f"  👤 Account:  {getattr(me, 'first_name', '')}  (@{getattr(me, 'username', '') or '-'})")
    print(f"  📡 Active sources: {len(bot.sources)}")
    if restored:
        print("  ☁️ Dedup database restored from Telegram backup")
    if config.KEEPALIVE_PORT:
        print(f"  🌐 HTTP server: port {config.KEEPALIVE_PORT}")
    print("  📌 To manage it, message yourself in Saved Messages:")
    print("     /help   /list   /add @channel   /setdest @mychannel")
    print("=" * 56)
    print()

    try:
        await client.run_until_disconnected()
    finally:
        # بکاپ نهایی موقع خاموش شدن (خیلی مهم روی سرور ابری)
        if config.CLOUD_BACKUP:
            try:
                import backup as backup_mod
                await backup_mod.backup_now(client)
            except Exception:
                log.exception("Final backup failed")


async def main():
    if not check_config():
        return

    session_str = config.load_session_string()

    # ---- اولین اجرا روی سرور ابری: صفحهٔ ورود وب ----
    if not session_str and _cloud_mode():
        try:
            import weblogin
        except ImportError:
            print()
            print("=" * 56)
            print("  ! SESSION_STRING is not set.")
            print("  Put your session string in the app's environment")
            print("  variables as SESSION_STRING, then restart the app.")
            print("  (See RAHNAMA-CLAWCLOUD.txt - section 7 and 9)")
            print("=" * 56)
            print()
            return
        port = config.KEEPALIVE_PORT or 7860
        log.info("No SESSION_STRING found - starting first-time login page "
                 "on port %s", port)
        log.info("Open your Space URL in the browser and log in to Telegram.")
        weblogin.run(port=port, on_success=run_bot)
        return

    # ---- اجرای محلی غیرتعاملی بدون نشست: امکان‌پذیر نیست ----
    if not session_str and not (sys.stdin and sys.stdin.isatty()):
        print()
        print("=" * 56)
        print("  ! No session found and no interactive terminal available.")
        print("  1) Run interactively once:  python main.py")
        print("  2) Or generate a string:    python gen_session.py")
        print("     and set it as the SESSION_STRING environment variable.")
        print("=" * 56)
        print()
        return

    if config.KEEPALIVE_PORT:
        from keepalive import start_keepalive
        start_keepalive(config.KEEPALIVE_PORT)

    client = await connect_client()
    await run_bot(client)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot stopped.")
    except (RuntimeError, ValueError) as e:
        print(f"\nERROR: {e}")
