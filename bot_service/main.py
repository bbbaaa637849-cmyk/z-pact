# ================================================================
# bot_service/main.py — نقطة تشغيل البوت الذكي وموزع الأحداث
# ================================================================

import os
import sys
import html
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ضبط مخرجات الطرفية على ويندوز لطباعة العربية والرموز التعبيرية بوضوح بدون ترميز \u06xx
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# إضافة المسار الجذري لمنع أي مشكلات استيراد
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bot_service.config import bot_config
from bot_service.sqlite_fsm import SQLiteStorage
from bot_service.services.notifier import RealTimeNotifier
from bot_service.handlers import (
    start_router,
    catalog_router,
    order_router,
    payment_router,
    admin_router,
    admin_admins_router,
    admin_studio_router,
    support_router
)

# تهيئة نظام التسجيل المتقدم
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("bot_service")


def _maintain_bot_storage(fsm_db_path: str) -> None:
    """تنظيف الجلسات القديمة ومجلد bot_tmp وضغط سجل WAL لحفظ مساحة القرص."""
    import sqlite3
    import time

    tmp_dir = os.path.join(bot_config.STORAGE_DIR, "bot_tmp")
    if os.path.isdir(tmp_dir):
        now = time.time()
        try:
            for entry in os.scandir(tmp_dir):
                if entry.name == ".gitkeep":
                    continue
                if entry.is_file(follow_symlinks=False) and (now - entry.stat(follow_symlinks=False).st_mtime) > 3600:
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
        except OSError:
            pass

    if fsm_db_path and os.path.isfile(fsm_db_path):
        try:
            with sqlite3.connect(fsm_db_path, timeout=10.0) as conn:
                cur = conn.cursor()
                cols = {row[1] for row in cur.execute("PRAGMA table_info(fsm_states)").fetchall()}
                if "state" in cols and "data" in cols:
                    cur.execute("DELETE FROM fsm_states WHERE state IS NULL AND (data IS NULL OR data = '{}')")
                if "updated_at" in cols:
                    cutoff = time.time() - (7 * 24 * 3600)
                    cur.execute(
                        "DELETE FROM fsm_states WHERE typeof(updated_at) IN ('integer', 'real') AND updated_at < ?",
                        (cutoff,),
                    )
                conn.commit()
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.debug(f"[Bot Storage Maintenance] {e}")


async def _bot_storage_maintenance_loop(fsm_db_path: str) -> None:
    while True:
        await asyncio.sleep(1800)
        try:
            await asyncio.to_thread(_maintain_bot_storage, fsm_db_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


async def main():
    """الدالة الرئيسية لتشغيل البوت وموزع المهام اللحظي"""
    logger.info("==================================================")
    logger.info("🤖 بدء تشغيل بوت التيليجرام الذكي (Z-PACT Fulfillment Bot)")
    logger.info(f"🌐 عنوان المنصة الأساسية (Core API): {bot_config.CORE_API_URL}")
    logger.info(f"👑 عدد المشرفين المسجلين: {len(bot_config.ADMIN_TELEGRAM_IDS)}")
    logger.info("==================================================")

    # 1. تهيئة البوت ومخزن الجلسات الدائم (SQLite FSM) للحفاظ على خطوات المستخدم عند إعادة التشغيل
    bot = Bot(
        token=bot_config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    fsm_db_path = os.path.join(bot_config.STORAGE_DIR, "bot_fsm_state.db")
    storage = SQLiteStorage(fsm_db_path)
    dp = Dispatcher(storage=storage)

    # 2. تسجيل موجهات الأحداث (Routers) بالترتيب المنطقي
    dp.include_router(start_router)
    dp.include_router(catalog_router)
    dp.include_router(order_router)
    dp.include_router(payment_router)
    dp.include_router(admin_router)
    dp.include_router(admin_admins_router)
    dp.include_router(admin_studio_router)
    dp.include_router(support_router)

    # 3. تهيئة وبدء مستمع الأحداث الفوري (Real-Time WebSocket & Fallback Notifier)
    notifier = RealTimeNotifier(bot)
    maintenance_task: asyncio.Task | None = None

    async def on_startup():
        nonlocal maintenance_task
        await asyncio.to_thread(_maintain_bot_storage, fsm_db_path)
        maintenance_task = asyncio.create_task(_bot_storage_maintenance_loop(fsm_db_path))
        logger.info("🚀 تشغيل خيط الاستماع اللحظي للأحداث وموزع الـ PDF...")
        await notifier.start()

        # ضبط زر الميني آب الدائم وتحديث الكاش فوراً لجميع المحادثات لمنع Error 1033
        live_miniapp_url = bot_config.get_live_miniapp_url(cache_bust=True)
        clean_miniapp_url = bot_config.get_live_miniapp_url(cache_bust=False)
        if live_miniapp_url.lower().startswith("https://"):
            try:
                from aiogram.types import MenuButtonWebApp, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
                from bot_service.services.bridge_client import bridge_client
                from bot_service.keyboards.reply import get_main_keyboard

                menu_btn = MenuButtonWebApp(
                    text="📱 المتجر والتطبيق الذكي",
                    web_app=WebAppInfo(url=live_miniapp_url)
                )
                # 1. تعيين الزر العام للبوت
                await bot.set_chat_menu_button(menu_button=menu_btn)

                # 2. كسر الكاش لكل محادثة مسجلة (المشرفين + المدرسين) صامتاً بدون إزعاج
                target_chats = set(bot_config.ADMIN_TELEGRAM_IDS)
                try:
                    recipients = await bridge_client.get_broadcast_recipients()
                    for cid in recipients:
                        if str(cid).lstrip("-").isdigit():
                            target_chats.add(int(cid))
                except Exception:
                    pass

                for cid in target_chats:
                    try:
                        await bot.set_chat_menu_button(chat_id=int(cid), menu_button=menu_btn)
                    except Exception:
                        pass

                logger.info(f"✨ تم تفعيل وتحديث زر الميني آب لجميع المحادثات: {clean_miniapp_url}")

                # 3. إرسال لوحة الأزرار المحدثة والزر الفوري للمشرفين لمنع الضغط على أي رابط قديم
                apk_admins = set(bot_config.get_admins_for_permission("apks_settings"))
                for admin_id in bot_config.ADMIN_TELEGRAM_IDS:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=(
                                "🟢 <b>تم تشغيل منظومة Z-PACT وتحديث النفق السحابي بنجاح!</b>\n"
                                "━━━━━━━━━━━━━━━━━━\n"
                                f"🌐 <b>الرابط الحي الجديد:</b>\n<code>{html.escape(clean_miniapp_url)}</code>\n\n"
                                "✨ <i>تم تحديث أزرار القائمة السفلية وزر القائمة الأزرق بالرابط الجديد تلقائياً.</i>"
                            ),
                            reply_markup=get_main_keyboard(is_admin=True),
                            parse_mode="HTML",
                        )
                        if admin_id in apk_admins:
                            await bot.send_message(
                                chat_id=admin_id,
                                text="👇 <b>افتح المتجر التفاعلي (Mini App) أو حمّل نسختي تطبيق الأندرويد (الأدمن / المدرسين) من هنا مباشرة:</b>",
                                reply_markup=InlineKeyboardMarkup(
                                    inline_keyboard=[
                                        [
                                            InlineKeyboardButton(
                                                text="🚀 فتح المتجر والتطبيق الذكي الآن (Mini App)",
                                                web_app=WebAppInfo(url=live_miniapp_url),
                                            )
                                        ],
                                        [
                                            InlineKeyboardButton(
                                                text="🛡️ إرسال تطبيق الأدمن والرادار الأسطوري (APK)",
                                                callback_data="adm_view:get_admin_apk",
                                            )
                                        ],
                                        [
                                            InlineKeyboardButton(
                                                text="🎓 إرسال تطبيق المدرسين الآمن بدون أذونات (APK)",
                                                callback_data="adm_view:get_teacher_apk",
                                            )
                                        ],
                                        [
                                            InlineKeyboardButton(
                                                text="🚀 إرسال تطبيق فريق المرسلين بدون أذونات (APK)",
                                                callback_data="adm_view:get_sender_apk",
                                            )
                                        ],
                                    ]
                                ),
                                parse_mode="HTML",
                            )
                    except Exception as notify_err:
                        logger.debug(f"تعذر إرسال إشعار البدء للمشرف {admin_id}: {notify_err}")
            except Exception as e:
                logger.warning(f"⚠️ تعذر تعيين زر القائمة التفاعلي: {e}")

    async def on_shutdown():
        logger.info("🛑 إيقاف مستمع الأحداث وجلسة البوت...")
        if maintenance_task and not maintenance_task.done():
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        await notifier.stop()
        await storage.close()
        await bot.session.close()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # 4. بدء استقبال التحديثات عبر Polling (مع الاحتفاظ برسائل العملاء الواردة أثناء لحظة إعادة التشغيل)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("👋 تم استلام إشارة إيقاف البوت بنجاح.")
    finally:
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("👋 تم إيقاف الخدمة.")

