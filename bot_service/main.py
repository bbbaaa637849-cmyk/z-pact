# ================================================================
# bot_service/main.py — نقطة تشغيل البوت الذكي وموزع الأحداث
# ================================================================

import os
import sys
import html
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
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
from bot_service.services.notifier import RealTimeNotifier
from bot_service.handlers import (
    start_router,
    catalog_router,
    order_router,
    payment_router,
    admin_router,
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


async def main():
    """الدالة الرئيسية لتشغيل البوت وموزع المهام اللحظي"""
    logger.info("==================================================")
    logger.info("🤖 بدء تشغيل بوت التيليجرام الذكي (Z-PACT Fulfillment Bot)")
    logger.info(f"🌐 عنوان المنصة الأساسية (Core API): {bot_config.CORE_API_URL}")
    logger.info(f"👑 عدد المشرفين المسجلين: {len(bot_config.ADMIN_TELEGRAM_IDS)}")
    logger.info("==================================================")

    # 1. تهيئة البوت ومخزن الجلسات
    bot = Bot(
        token=bot_config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # 2. تسجيل موجهات الأحداث (Routers) بالترتيب المنطقي
    dp.include_router(start_router)
    dp.include_router(catalog_router)
    dp.include_router(order_router)
    dp.include_router(payment_router)
    dp.include_router(admin_router)
    dp.include_router(admin_studio_router)
    dp.include_router(support_router)

    # 3. تهيئة وبدء مستمع الأحداث الفوري (Real-Time WebSocket & Fallback Notifier)
    notifier = RealTimeNotifier(bot)

    async def on_startup():
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
        await notifier.stop()
        await bot.session.close()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # 4. بدء استقبال التحديثات عبر Polling
    try:
        # حذف أي ويب هوك قديم لضمان عمل الـ polling بدون تعارض
        await bot.delete_webhook(drop_pending_updates=True)
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

