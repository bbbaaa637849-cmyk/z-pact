# ================================================================
# bot_service/services/notifier.py — مستمع الأحداث وموزع المستندات التلقائي
# ================================================================

import asyncio
import html
import json
import logging
import os
import urllib.parse
from typing import Optional, Set
import websockets
from aiogram import Bot
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from bot_service.config import bot_config
from bot_service.services.bridge_client import bridge_client

logger = logging.getLogger("bot_service.notifier")


class RealTimeNotifier:
    """
    محرك الاستماع اللحظي للأحداث من السيرفر وتوزيع المستندات المختومة فورياً.
    يدعم:
      - اتصال WebSocket دائم مع إعادة الاتصال التلقائي (Auto-Reconnect)
      - طابور تحقق احتياطي كل 10 ثوانٍ (Fallback Polling) لضمان عدم ضياع أي طلب
      - إرسال الـ PDF كملف مستند Telegram Document عالي الجودة للعميل
      - إشعار قنوات/حسابات الإدارة بكل عملية تسليم ناجحة
    """
    def __init__(self, bot: Bot):
        self.bot = bot
        self.is_running = False
        self._processed_orders: Set[str] = set()
        self._last_layout_alert_ts: dict[int, float] = {}

    async def start(self):
        """بدء تشغيل المستمع اللحظي ومراقب التسليم الاحتياطي"""
        self.is_running = True
        logger.info("🚀 [Notifier] تم تشغيل محرك الاستماع والتوزيع اللحظي للـ Bot")
        
        # تشغيل مهمتين متزامنتين: الـ WebSocket والـ Polling Fallback
        asyncio.create_task(self._websocket_listener_loop())
        asyncio.create_task(self._fallback_delivery_poller())

    async def stop(self):
        self.is_running = False
        logger.info("🛑 [Notifier] تم إيقاف محرك الاستماع اللحظي")

    # -------------------------------------------------------------
    # 1. حلقة اتصال WebSocket اللحظية
    # -------------------------------------------------------------
    async def _websocket_listener_loop(self):
        retry_delay = 2
        base_ws_url = bot_config.CORE_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
        token_param = urllib.parse.quote(bot_config.INTERNAL_SERVICE_KEY)
        ws_url = f"{base_ws_url}/api/internal/events/ws?token={token_param}"

        while self.is_running:
            try:
                logger.info(f"🔗 [Notifier WS] جاري الاتصال بناقل الأحداث الداخلي: {ws_url}")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("✅ [Notifier WS] متصل بنجاح مع سيرفر المنصة الأساسية!")
                    retry_delay = 2  # تصفير عداد الانتظار عند نجاح الاتصال

                    while self.is_running:
                        message_raw = await ws.recv()
                        try:
                            event_data = json.loads(message_raw)
                            logger.info(f"⚡ [Notifier WS] حدث وارد: {event_data.get('event')} للطلب {event_data.get('order_code')}")
                            await self._handle_event(event_data)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.warning(f"⚠️ [Notifier WS] انقطع اتصال الـ WebSocket ({e})، إعادة المحاولة خلال {retry_delay} ثوانٍ...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 30)

    # -------------------------------------------------------------
    # 2. معالج الأحداث الواردة
    # -------------------------------------------------------------
    async def _notify_all_admins(
        self,
        text: str,
        reply_markup=None,
        photo_fs_path: Optional[str] = None,
        permission: Optional[str] = None,
    ):
        targets = (
            bot_config.get_admins_for_permission(permission)
            if permission
            else list(bot_config.ADMIN_TELEGRAM_IDS)
        )
        for admin_id in targets:
            try:
                if photo_fs_path and os.path.isfile(photo_fs_path):
                    await self.bot.send_photo(
                        chat_id=admin_id,
                        photo=FSInputFile(photo_fs_path),
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    await self.bot.send_message(
                        chat_id=admin_id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
            except Exception as e:
                logger.warning(f"⚠️ [Notifier] تعذر إرسال تنبيه المشرف {admin_id}: {e}")

    async def _handle_event(self, data: dict):
        event = data.get("event")
        order_code = data.get("order_code")
        chat_id = data.get("telegram_chat_id")

        # ---------------------------------------------------------
        # أحداث الإدارة الفورية للإصدار الثالث V3 (المرسلون / المدرسون / السلال)
        # ---------------------------------------------------------
        if event == "OTP_TELEGRAM_PUSH":
            tg_uid = data.get("telegram_user_id")
            code = data.get("otp_code")
            target_role = "المدرسين" if data.get("target_role") == "teacher" else "فريق المرسلين"
            phone = data.get("phone", "")
            if tg_uid:
                try:
                    await self.bot.send_message(
                        chat_id=int(tg_uid),
                        text=(
                            f"🔐 <b>رمز التحقق السريع (OTP) لمنظومة Z-PACT:</b>\n\n"
                            f"<code>{code}</code>\n\n"
                            f"📱 <b>الحساب:</b> {target_role} (<code>{phone}</code>)\n"
                            f"⏱️ <i>صالح لمدة 10 دقائق للاستخدام لمرة واحدة.</i>\n\n"
                            f"اضغط على الكود لنسخه والصقه في شاشة الدخول بالتطبيق فوراً! ✨"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"⚠️ [Notifier] تعذر إرسال OTP لتليجرام {tg_uid}: {e}")
            return

        if event == "ADMIN_LOGIN_ALERT":
            lbl = html.escape(str(data.get("admin_label", "")))
            is_founder = data.get("is_founding_admin", False)
            dev_id = html.escape(str(data.get("device_id", "")))
            ip = html.escape(str(data.get("ip_address", "")))
            surface = html.escape(str(data.get("surface", "")))
            tstamp = html.escape(str(data.get("timestamp", "")))
            await self._notify_all_admins(
                f"🛡️ <b>تنبيه أمني: تسجيل دخول بحساب أدمن</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>الحساب:</b> {lbl} ({'👑 أول أدمن المؤسس' if is_founder else '🛡️ أدمن فرعي'})\n"
                f"📱 <b>المنصة:</b> {surface}\n"
                f"🔑 <b>سيريال الجهاز:</b> <code>{dev_id}</code>\n"
                f"🌐 <b>عنوان الـ IP:</b> <code>{ip}</code>\n"
                f"⏰ <b>التوقيت:</b> <code>{tstamp}</code>",
                permission="manage_admins",
            )
            return

        if event == "FOUNDING_ADMIN_INITIALIZED":
            lbl = html.escape(str(data.get("admin_label", "")))
            dev_id = html.escape(str(data.get("device_id", "")))
            ip = html.escape(str(data.get("ip_address", "")))
            await self._notify_all_admins(
                f"👑 <b>تم تأسيس كلمة السر السيادية لأول أدمن بنجاح!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>الأدمن المؤسس:</b> {lbl}\n"
                f"📱 <b>سيريال هاتف الإدارة:</b> <code>{dev_id}</code>\n"
                f"🌐 <b>عنوان الـ IP:</b> <code>{ip}</code>\n\n"
                f"تم قفل صلاحية الإدارة العليا وتفعيل نظام الموافقة على المشرفين الجدد بنجاح! 🛡️",
                permission="manage_admins",
            )
            return

        if event == "CO_ADMIN_APPROVAL_REQUEST":
            uid = data.get("user_id")
            lbl = html.escape(str(data.get("admin_label", "")))
            dev_id = html.escape(str(data.get("device_id", "")))
            ip = html.escape(str(data.get("ip_address", "")))
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ موافقة وتفعيل المشرف", callback_data=f"v3_coadm:approve:{uid}"),
                InlineKeyboardButton(text="❌ رفض وحظر", callback_data=f"v3_coadm:reject:{uid}"),
            ]])
            await self._notify_all_admins(
                f"🚨 <b>طلب إنشاء حساب أدمن جديد (يتطلب موافقة أول أدمن)!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>الاسم التوضيحي:</b> {lbl}\n"
                f"🔑 <b>سيريال الهاتف:</b> <code>{dev_id}</code>\n"
                f"🌐 <b>IP:</b> <code>{ip}</code>\n\n"
                f"بصفتك أول أدمن (المؤسس)، هل توافق على منح هذا الجهاز صلاحية الأدمن؟ 👇",
                reply_markup=kb,
                permission="manage_admins",
            )
            return

        if event == "SENDER_APPROVAL_REQUEST":
            sid = data.get("sender_id")
            phone = html.escape(str(data.get("phone", "")))
            dname = html.escape(str(data.get("display_name", "")))
            scode = html.escape(str(data.get("serial_code", "")))
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ قبول وتفعيل المرسل", callback_data=f"v3_snd:approve:{sid}"),
                InlineKeyboardButton(text="❌ رفض المرسل", callback_data=f"v3_snd:reject:{sid}"),
            ]])
            await self._notify_all_admins(
                f"🚀 <b>طلب انضمام مرسل جديد لفريق الإرسال!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>الاسم:</b> {dname}\n"
                f"📱 <b>رقم الهاتف:</b> <code>{phone}</code>\n"
                f"🔐 <b>سيريال الجهاز:</b> <code>{scode}</code>\n\n"
                f"اضغط موافقة ليفتح تطبيق المرسلين على هاتفه في التو واللحظة 👇",
                reply_markup=kb,
                permission="orders_finance",
            )
            return

        if event == "TEACHER_APPROVAL_REQUEST":
            uid = data.get("user_id")
            fname = html.escape(str(data.get("full_name", "")))
            phone = html.escape(str(data.get("phone", "")))
            scode = html.escape(str(data.get("serial_code", "")))
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ تفعيل حساب المدرس", callback_data=f"v3_tch:approve:{uid}"),
                InlineKeyboardButton(text="❌ رفض", callback_data=f"v3_tch:reject:{uid}"),
            ]])
            await self._notify_all_admins(
                f"👨‍🏫 <b>طلب تفعيل حساب مدرس جديد بالمنصة!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>الاسم:</b> {fname}\n"
                f"📱 <b>رقم الهاتف:</b> <code>{phone}</code>\n"
                f"🔐 <b>سيريال الجهاز:</b> <code>{scode}</code>\n\n"
                f"بمجرد التفعيل سيفتح له المتجر تلقائياً على هاتفه 👇",
                reply_markup=kb,
                permission="orders_finance",
            )
            return

        if event == "NEW_PENDING_BUNDLE":
            bcode = str(data.get("bundle_code", ""))
            bname = html.escape(str(data.get("buyer_name", "")))
            bphone = html.escape(str(data.get("buyer_phone", "")))
            fwallet = html.escape(str(data.get("from_wallet", "")))
            twallet = html.escape(str(data.get("to_wallet", "")))
            amt = data.get("total_amount", 0)
            pcount = data.get("pdfs_count", 1)
            msum = html.escape(str(data.get("materials_summary", "")))
            rel_receipt = data.get("receipt_image_path")
            fs_receipt = None
            if rel_receipt:
                from bot_service.config import BASE_DIR
                cand = os.path.join(BASE_DIR, "Z-PACT-main", rel_receipt.lstrip("/"))
                if os.path.isfile(cand):
                    fs_receipt = cand

            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ موافقة وختم السلة", callback_data=f"v3_bnd:approve:{bcode}"),
                InlineKeyboardButton(text="❌ رفض السلة", callback_data=f"v3_bnd:reject:{bcode}"),
            ]])
            await self._notify_all_admins(
                f"🛒 <b>سلة شراء جديدة بانتظار الاعتماد ({pcount} مذكرات)!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔖 <b>كود السلة:</b> <code>{html.escape(bcode)}</code>\n"
                f"👤 <b>المدرس:</b> {bname} (<code>{bphone}</code>)\n"
                f"💸 <b>التحويل:</b> <code>{fwallet}</code> ⬅️ <code>{twallet}</code>\n"
                f"💰 <b>الإجمالي:</b> <code>{amt}</code> ج.م\n"
                f"📚 <b>المذكرات:</b> {msum}\n\n"
                f"<i>(ظاهرة الآن أيضاً في تطبيق المرسلين ولوحة الإدارة)</i>",
                reply_markup=kb,
                photo_fs_path=fs_receipt,
                permission="orders_finance",
            )
            return

        if event in ("SENDER_ORDER_CREATED", "SENDER_FULFILLED_PENDING"):
            sname = html.escape(str(data.get("sender_name", "")))
            sphone = html.escape(str(data.get("sender_phone", "")))
            pcount = data.get("pdfs_count", 1)
            gross = data.get("gross_amount", 0)
            comm = data.get("sender_commission", 0)
            action_lbl = "أنشأ طلباً فورياً" if event == "SENDER_ORDER_CREATED" else f"اعتمد السلة المعلقة ({html.escape(str(data.get('target_code', '')))})"
            await self._notify_all_admins(
                f"⚡ <b>إشعار نشاط فريق المرسلين</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <b>المرسل:</b> {sname} (<code>{sphone}</code>) {action_lbl}\n"
                f"📄 <b>عدد الـ PDFs:</b> <code>{pcount}</code> مذكرات\n"
                f"💵 <b>قيمة العملية:</b> <code>{gross}</code> ج.م | <b>عمولته:</b> <code>{comm}</code> ج.م",
                permission="senders_ota",
            )
            return

        if event == "MATERIAL_WAITLIST_READY":
            mid = int(data.get("material_id") or 0)
            mtitle = html.escape(str(data.get("material_title") or f"#{mid}"))
            wl_cnt = int(data.get("waiting_count") or 0)
            if mid > 0:
                await self._notify_all_admins(
                    f"🚀 <b>اكتمل شرطا صدور المذكرة وتفعيل قائمة الانتظار تلقائياً!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📖 <b>المذكرة:</b> {mtitle} (<code>#{mid}</code>)\n"
                    f"👥 <b>المدرسون المنتظرون:</b> <code>{wl_cnt}</code>\n\n"
                    f"⚙️ جاري الآن ختم وإرسال الملفات تباعاً...",
                    permission="batch_vip",
                )
                from bot_service.handlers.admin_studio import trigger_waitlist_release_once
                asyncio.create_task(trigger_waitlist_release_once(self.bot, mid))
            return

        if event == "MATERIAL_PDF_NEEDS_LAYOUT":
            mid = int(data.get("material_id") or 0)
            mtitle = html.escape(str(data.get("material_title") or f"#{mid}"))
            wl_cnt = int(data.get("waiting_count") or 0)
            pages = int(data.get("total_pages") or 0)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔤 ضبط وتأكيد أماكن الأختام الآن", callback_data=f"adm_mat_font:{mid}"),
            ]])
            await self._notify_all_admins(
                f"📄 <b>تم رفع ملف الـ PDF للمذكرة القادمة: {mtitle}</b> (<code>#{mid}</code> | {pages} ص)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⏳ يوجد <code>{wl_cnt}</code> مدرس في قائمة الانتظار.\n"
                f"🛡️ <b>شرط الحماية:</b> لن تبدأ الطباعة قبل تحديد وحفظ أماكن الأختام من الموقع (🎨 تنسيق الأختام) أو من استوديو البوت.\n\n"
                f"👇 فور حفظ الأختام سيبدأ البوت بإرسال جميع الملفات تلقائياً!",
                reply_markup=kb,
                permission="studio_fonts",
            )
            return

        if event == "MATERIAL_LAYOUT_UPDATED":
            mid = int(data.get("material_id") or 0)
            mtitle = html.escape(str(data.get("material_title") or f"#{mid}"))
            comp_cnt = int(data.get("completed_count") or 0)
            if mid > 0 and comp_cnt > 0:
                now_ts = asyncio.get_running_loop().time()
                last_ts = self._last_layout_alert_ts.get(mid, 0.0)
                if now_ts - last_ts < 4.0:
                    return
                self._last_layout_alert_ts[mid] = now_ts
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"✅ موافقة — إرسال للكل ({comp_cnt} مدرس)", callback_data=f"adm_rst_all:{mid}")],
                    [InlineKeyboardButton(text="☑️ اختيار مدرسين محددين فقط", callback_data=f"adm_rst_pick:{mid}:1")],
                    [InlineKeyboardButton(text="⏭️ لاحقاً (الاحتفاظ بالتنسيق فقط)", callback_data=f"adm_rst_dismiss:{mid}")],
                ])
                await self._notify_all_admins(
                    f"🎨 <b>تم تعديل أماكن/تنسيق أختام المذكرة: {mtitle}</b> (<code>#{mid}</code>)\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"👥 يوجد <code>{comp_cnt}</code> مدرس محفوظة بياناتهم مسبقاً لهذه المذكرة.\n"
                    f"هل تريد إعادة طباعة وإرسال الملفات المعدلة الآن؟",
                    reply_markup=kb,
                    permission="batch_vip",
                )
            return

        if not chat_id:
            return

        try:
            chat_id = int(chat_id)
        except ValueError:
            return

        # ---------------------------------------------------------
        # أ. تأكيد التحويل المالي وبدء الختم
        # ---------------------------------------------------------
        if event == "ORDER_RECONCILED":
            text = (
                f"🎉 <b>تم تأكيد استلام تحويلك المالي بنجاح!</b>\n\n"
                f"🔖 <b>كود الطلب:</b> <code>{html.escape(str(order_code))}</code>\n"
                f"💵 <b>المبلغ:</b> <code>{html.escape(str(data.get('amount', '')))}</code> ج.م\n"
                f"⚙️ <b>الحالة:</b> جاري الآن تشغيل محرك الختم الذكي وتجهيز نسختك المخصصة...\n"
                f"⏳ يستغرق تجهيز الملف من 5 إلى 30 ثانية فقط!"
            )
            try:
                await self.bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ [Notifier] خطأ إرسال إشعار الدفع للعميل {chat_id}: {e}")

        # ---------------------------------------------------------
        # ب. اكتمال تجهيز الـ PDF وتسليمه فوراً
        # ---------------------------------------------------------
        elif event == "ORDER_READY":
            await self.deliver_order_pdf(order_code, chat_id, data)

        # ---------------------------------------------------------
        # ج. موافقة يدوية من الأدمن
        # ---------------------------------------------------------
        elif event == "ORDER_ADMIN_APPROVED":
            text = (
                f"👑 <b>موافقة المشرف المباشرة!</b>\n\n"
                f"تم اعتماد طلبك <code>{html.escape(str(order_code))}</code> يدوياً من قبل إدارة المنصة، "
                f"وجاري توليد ملف الـ PDF بأعلى دقة متجهة الآن... 🚀"
            )
            try:
                await self.bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception:
                pass

        # ---------------------------------------------------------
        # د. إلغاء أو رفض الطلب
        # ---------------------------------------------------------
        elif event == "ORDER_CANCELLED":
            reason = data.get("reason", "لم يتم توضيح السبب")
            text = (
                f"❌ <b>تنبيه: تم إلغاء طلبك <code>{html.escape(str(order_code))}</code></b>\n\n"
                f"💬 <b>سبب الإلغاء:</b> {html.escape(str(reason))}\n"
                f"إذا كنت قد قمت بالتحويل بالفعل، يرجى الضغط على زر الدعم الفني للتواصل مع المشرف."
            )
            try:
                await self.bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception:
                pass

    # -------------------------------------------------------------
    # 3. دالة تسليم المستند النهائي للعميل
    # -------------------------------------------------------------
    async def deliver_order_pdf(self, order_code: str, chat_id: int, event_data: Optional[dict] = None):
        """تحميل الملف النهائي وبثه للمستخدم كـ Telegram Document"""
        if order_code in self._processed_orders:
            return  # منع التكرار

        logger.info(f"📦 [Notifier] جاري تسليم ملف الطلب {order_code} للعميل {chat_id}...")

        # 1. تحميل بايتات الملف من السيرفر
        pdf_bytes = await bridge_client.download_order_pdf(order_code)
        if not pdf_bytes:
            logger.error(f"❌ [Notifier] لم نتمكن من تنزيل ملف الطلب {order_code}")
            return

        # 2. تجهيز التسمية والوصف الاحترافي
        title = (event_data or {}).get("material_title") or "المذكرة التعليمية"
        buyer_name = (event_data or {}).get("buyer_name") or "المدرس المعتمد"
        
        caption = (
            f"✅ <b>تم تجهيز وتأمين مذكرتك بنجاح!</b> 🎓\n\n"
            f"📚 <b>المذكرة:</b> {html.escape(str(title))}\n"
            f"👤 <b>المدرس:</b> {html.escape(str(buyer_name))}\n"
            f"🔖 <b>كود الطلب:</b> <code>{html.escape(str(order_code))}</code>\n"
            f"🛡️ <b>حالة الحماية:</b> مختومة بأختام رقمية متجهة وعلامات تتبع أمنية مشفرة ضد التسريب.\n\n"
            f"✨ نتمنى لكم ولطلابكم دوام التفوق والنجاح!"
        )

        filename = f"ZPACT_{order_code}.pdf"
        input_file = BufferedInputFile(pdf_bytes, filename=filename)

        try:
            await self.bot.send_document(
                chat_id=chat_id,
                document=input_file,
                caption=caption,
                parse_mode="HTML"
            )
            self._processed_orders.add(order_code)
            logger.info(f"🎉 [Notifier] تم تسليم الملف {order_code} بنجاح إلى المحادثة {chat_id}!")
            await bridge_client.admin_order_action(order_code, action="deliver", admin_notes="Delivered via Telegram Bot")
            if str(order_code or "").startswith("BAT-"):
                await bridge_client.cleanup_order_pdf(order_code)

            # إشعار حسابات الإدارة بالتسليم
            admin_msg = (
                f"🚀 <b>تم تسليم طلب بنجاح!</b>\n"
                f"🔖 الطلب: <code>{html.escape(str(order_code))}</code>\n"
                f"📚 المذكرة: {html.escape(str(title))}\n"
                f"👤 المدرس: {html.escape(str(buyer_name))}\n"
                f"📱 المعرف: <code>{chat_id}</code>"
            )
            for admin_id in bot_config.get_admins_for_permission("orders_finance"):
                try:
                    await self.bot.send_message(admin_id, admin_msg, parse_mode="HTML")
                except Exception:
                    pass

        except Exception as e:
            err_msg = str(e).lower()
            if "chat not found" in err_msg or "blocked" in err_msg or "deactivated" in err_msg or "can't initiate" in err_msg:
                self._processed_orders.add(order_code)
                logger.warning(f"⚠️ [Notifier] تعذر الوصول للعميل ({chat_id}) للطلب {order_code}: {e} (تم تسجيله كمكتمل لتجنب التكرار)")
                await bridge_client.admin_order_action(order_code, action="deliver", admin_notes=f"Unreachable chat: {e}")
            else:
                logger.error(f"❌ [Notifier] فشل إرسال المستند للعميل {chat_id}: {e}")

    # -------------------------------------------------------------
    # 4. مراقب التسليم الاحتياطي (Fallback Polling)
    # -------------------------------------------------------------
    async def _fallback_delivery_poller(self):
        """فحص احتياطي دوري كل 12 ثانية للطلبات الجاهزة وقوائم الانتظار المكتملة"""
        tick = 0
        while self.is_running:
            await asyncio.sleep(12)
            tick += 1
            try:
                if tick % 2 == 1:
                    ready_wls = await bridge_client.get_ready_waitlists()
                    if ready_wls:
                        from bot_service.handlers.admin_studio import trigger_waitlist_release_once
                        for w in ready_wls:
                            mid = int(w.get("material_id") or 0)
                            if mid > 0:
                                asyncio.create_task(trigger_waitlist_release_once(self.bot, mid))

                stats = await bridge_client.get_admin_stats()
                if not stats:
                    continue

                for order in stats.get("recent_orders", []):
                    code = order.get("order_code")
                    status = order.get("status")
                    chat_id_str = order.get("telegram_chat_id")

                    if status == "ready_for_delivery" and code not in self._processed_orders:
                        if chat_id_str and chat_id_str.isdigit():
                            logger.info(f"🔍 [Notifier Fallback] تم رصد طلب جاهز لم يتم تسليمه: {code}")
                            await self.deliver_order_pdf(
                                order_code=code,
                                chat_id=int(chat_id_str),
                                event_data={
                                    "material_title": "مذكرتك التعليمية",
                                    "buyer_name": order.get("buyer_name_stamp")
                                }
                            )
            except Exception as e:
                logger.debug(f"[Notifier Fallback Error] {e}")

