# ================================================================
# bot_service/handlers/payment.py — معالج الدفع وتتبع الإيصالات والمطابقة
# ================================================================

import os
import html
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext

from bot_service.config import bot_config
from bot_service.services.bridge_client import bridge_client
from bot_service.states.order_flow import PaymentStates
from bot_service.keyboards.reply import get_cancel_keyboard, get_main_keyboard, get_skip_keyboard
from bot_service.keyboards.inline import get_admin_order_actions_keyboard

logger = logging.getLogger("bot_service.handlers.payment")
router = Router(name="payment_router")


# ================================================================
# 1. اختيار وسيلة الدفع وعرض بيانات التحويل
# ================================================================

@router.callback_query(F.data.startswith("pay_method:"))
async def handle_payment_method_selected(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split(":")[1]
    data = await state.get_data()
    total_amount = data.get("total_amount", 0)
    title = data.get("material_title", "المذكرة")

    await state.update_data(payment_method=method)

    if method == "vodafone_cash":
        instruction = (
            f"📱 **طريقة الدفع: فودافون كاش (Vodafone Cash)**\n\n"
            f"💵 **المبلغ المطلوب تحويله:** `{total_amount}` ج.م\n"
            f"📞 **أرقام المحافظ المعتمدة للتحويل (اضغط لنسخ الرقم):**\n"
            f"  1️⃣ `{bot_config.PAYMENT_WALLET_NUMBER}`\n"
            f"  2️⃣ `{bot_config.PAYMENT_WALLET_NUMBER_2}`\n\n"
            f"⚠️ **الخطوات المطلوبة:**\n"
            f"1. قم بالتحويل من محفظتك إلى أي من الرقمين أعلاه.\n"
            f"2. التقط سكرين شوت (Screenshot) لرسالة أو إيصال نجاح التحويل.\n\n"
            f"👉 **أدخل الآن رقم الهاتف الذي قمت بالتحويل منه:**"
        )
    else:
        instruction = (
            f"⚡ **طريقة الدفع: إنستاباي (InstaPay / كارت ميزة)**\n\n"
            f"💵 **المبلغ المطلوب تحويله:** `{total_amount}` ج.م\n"
            f"🏦 **عنوان إنستاباي (رقم الهاتف):** `{bot_config.INSTAPAY_ADDRESS}`\n"
            f"*(اضغط على الرقم لنسخه فوراً في تطبيق InstaPay)*\n\n"
            f"⚠️ **الخطوات المطلوبة:**\n"
            f"1. قم بالتحويل عبر تطبيق إنستاباي للمبلغ المحدد إلى الرقم `{bot_config.INSTAPAY_ADDRESS}`.\n"
            f"2. التقط سكرين شوت (Screenshot) لإيصال نجاح المعاملة.\n\n"
            f"👉 **أدخل الآن رقم هاتفك أو اسم حسابك في إنستاباي:**"
        )

    is_admin = bot_config.is_admin(callback.from_user.id)
    if is_admin:
        instruction += "\n\n👑 *للأدمن:* يمكنك الضغط على `تخطي ⏩` للمتابعة بدون رقم هاتف."
    await state.set_state(PaymentStates.waiting_claimed_phone)
    await callback.message.answer(
        text=instruction,
        reply_markup=get_skip_keyboard() if is_admin else get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ================================================================
# 2. استقبال رقم الهاتف المحول منه
# ================================================================

@router.message(PaymentStates.waiting_claimed_phone)
async def process_claimed_phone(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if is_admin and raw.lower() in ("تخطي", "تخطي ⏩", "-", "—", "بدون", "بدون رقم", "لا يوجد", "skip", ""):
        phone_input = ""
    elif not is_admin and len(raw) < 4:
        await message.answer("⚠️ يرجى إدخال رقم هاتف أو اسم حساب صحيح.")
        return
    else:
        phone_input = raw

    await state.update_data(claimed_sender_phone=phone_input)
    await state.set_state(PaymentStates.waiting_receipt_photo)

    skip_hint = "\n*(أو أرسل كلمة `تخطي` لإنشاء الطلب بدون صورة إيصال للأدمن)*" if is_admin else ""
    await message.answer(
        "📸 **الخطوة الأخيرة:** يرجى إرسال **صورة الإيصال (Screenshot)** لعملية التحويل الآن:\n"
        f"*(أرسل الصورة كصورة عادية داخل الشات)*{skip_hint}",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )


# ================================================================
# 3. استقبال صورة الإيصال وإرسال الطلب والمطابقة اللحظية
# ================================================================

@router.message(PaymentStates.waiting_receipt_photo, F.photo | F.text)
async def process_receipt_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    chat_id = message.chat.id
    is_admin = bot_config.is_admin(user_id)

    receipt_file_id = None
    local_receipt_path = None

    if message.photo:
        # 1. جلب صورة الإيصال بأعلى جودة
        photo = message.photo[-1]
        receipt_file_id = photo.file_id

        # 2. تحميل وحفظ الصورة محلياً
        import secrets
        receipt_filename = f"receipt_{user_id}_{secrets.token_hex(4)}.jpg"
        local_receipt_path = os.path.join(bot_config.RECEIPTS_DIR, receipt_filename)

        try:
            file_info = await message.bot.get_file(receipt_file_id)
            await message.bot.download_file(file_info.file_path, destination=local_receipt_path)
            logger.info(f"📸 [Payment] تم حفظ سكرين شوت الإيصال في: {local_receipt_path}")
        except Exception as e:
            logger.error(f"⚠️ [Payment] خطأ أثناء حفظ الإيصال محلياً: {e}")
            local_receipt_path = None
    elif not is_admin:
        await message.answer("📸 يرجى إرسال صورة إيصال التحويل (Screenshot) الآن.")
        return

    # 3. إعداد بيانات الطلب الكاملة
    order_payload = {
        "material_id": data.get("material_id"),
        "telegram_chat_id": str(chat_id),
        "telegram_user_id": str(user_id),
        "is_admin": is_admin,
        "teacher_profile_id": data.get("teacher_profile_id"),
        "buyer_name_stamp": data.get("buyer_name_stamp"),
        "buyer_phone_stamp": data.get("buyer_phone_stamp"),
        "buyer_phone_stamp_2": data.get("buyer_phone_stamp_2"),
        "series_name": data.get("series_name") or data.get("doc_name_stamp"),
        "doc_name_stamp": data.get("doc_name_stamp") or data.get("series_name"),
        "watermark_text": data.get("watermark_text"),
        "watermark_type": data.get("watermark_type", "none"),
        "total_amount": float(data.get("total_amount", 0)),
        "payment_method": data.get("payment_method", "vodafone_cash"),
        "claimed_sender_phone": data.get("claimed_sender_phone"),
        "receipt_image_path": local_receipt_path,
        "receipt_file_id": receipt_file_id
    }

    # 4. إرسال الطلب إلى المنصة الأساسية عبر الجسر الداخلي
    await message.answer("⏳ جاري مراجعة ومطابقة التحويل لحظياً مع المنظومة المالية...")
    created_order = await bridge_client.create_order(order_payload)
    await state.clear()

    if not created_order:
        await message.answer(
            "⚠️ حدث خطأ أثناء تسجيل الطلب، يرجى التواصل مع الدعم الفني.",
            reply_markup=get_main_keyboard(is_admin=is_admin)
        )
        return

    order_code = created_order.get("order_code")
    order_status = created_order.get("status")

    # ============================================================
    # الحالة أ: إذا تمت المطابقة التلقائية فوراً (SMS arrived already)
    # ============================================================
    if order_status in ["payment_confirmed", "processing_stamp", "ready_for_delivery"]:
        celebration_text = (
            f"🎉 <b>مبروك! تم التحقق التلقائي الفوري من تحويلك!</b> ⚡\n\n"
            f"🔖 <b>كود الطلب:</b> <code>{html.escape(str(order_code))}</code>\n"
            f"📚 <b>المذكرة:</b> {html.escape(str(data.get('material_title', '')))}\n"
            f"💵 <b>المبلغ:</b> <code>{html.escape(str(data.get('total_amount', '')))}</code> ج.م\n"
            f"⚙️ <b>الحالة الحالية:</b> جاري الآن تشغيل محرك الختم والضغط وتجهيز ملفك...\n\n"
            f"سيرسل لك البوت ملف الـ PDF هنا في الشات فور اكتماله خلال ثوانٍ! 🚀"
        )
        await message.answer(
            celebration_text,
            reply_markup=get_main_keyboard(is_admin=is_admin),
            parse_mode="HTML"
        )
        return

    # ============================================================
    # الحالة ب: في انتظار وصول رسالة الـ SMS من شبكة المحافظ
    # ============================================================
    checklist_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 فحص وتحديث حالة الطلب الآن", callback_data=f"check_order_status:{order_code}")],
        [InlineKeyboardButton(text="💬 التواصل مع الدعم الفني", callback_data="support:talk_human")]
    ])

    pending_text = (
        f"✅ <b>تم استلام وتسجيل إيصال التحويل بنجاح!</b> 🎓\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>قائمة التحقق الآلية (Checklist):</b>\n"
        f"  [✔️] بيانات الختم: <b>مسجلة ومجهزة بالكامل</b>\n"
        f"  [✔️] صورة الإيصال: <b>تم استلامها وحفظها محلياً</b>\n"
        f"  [🔄] مطابقة رسالة الـ SMS: <b>جاري الاستماع اللحظي للشبكة...</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔖 <b>كود الطلب:</b> <code>{html.escape(str(order_code))}</code>\n"
        f"📚 <b>المذكرة:</b> {html.escape(str(data.get('material_title', '')))}\n"
        f"💵 <b>المبلغ:</b> <code>{html.escape(str(data.get('total_amount', '')))}</code> ج.م\n"
        f"📱 <b>المحول منه:</b> <code>{html.escape(str(data.get('claimed_sender_phone', '')))}</code>\n\n"
        f"⚡ <b>ماذا يحدث الآن؟</b>\n"
        f"فور وصول إشعار البنك/المحفظة، سيتم إطلاق محرك الختم والضغط المتجه وتصلك المذكرة هنا تلقائياً خلال ثوانٍ!"
    )
    await message.answer(
        pending_text,
        reply_markup=checklist_kb,
        parse_mode="HTML"
    )

    # 5. إرسال بطاقة تنبيه تفاعلية مع صورة الإيصال لجميع حسابات الإدارة
    admin_caption = (
        f"🔔 <b>طلب جديد بانتظار التأكيد المالي!</b>\n\n"
        f"🔖 <b>الطلب:</b> <code>{html.escape(str(order_code))}</code>\n"
        f"📚 <b>المذكرة:</b> {html.escape(str(data.get('material_title', '')))}\n"
        f"💵 <b>المبلغ:</b> <code>{html.escape(str(data.get('total_amount', '')))}</code> ج.م\n"
        f"👤 <b>المدرس:</b> {html.escape(str(data.get('buyer_name_stamp', '')))}\n"
        f"📱 <b>رقم الختم:</b> <code>{html.escape(str(data.get('buyer_phone_stamp', '')))}</code>\n"
        f"📞 <b>رقم التحويل:</b> <code>{html.escape(str(data.get('claimed_sender_phone', '')))}</code>\n"
        f"💳 <b>وسيلة الدفع:</b> {html.escape(str(data.get('payment_method', '')))}\n"
        f"🆔 <b>معرف المحادثة:</b> <code>{html.escape(str(chat_id))}</code>"
    )
    admin_kb = get_admin_order_actions_keyboard(order_code, str(chat_id))

    for admin_id in bot_config.ADMIN_TELEGRAM_IDS:
        try:
            if receipt_file_id:
                await message.bot.send_photo(
                    chat_id=admin_id,
                    photo=receipt_file_id,
                    caption=admin_caption,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            else:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=admin_caption,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
        except Exception as err:
            logger.warning(f"⚠️ فشل إرسال تنبيه الطلب للمشرف {admin_id}: {err}")


# ================================================================
# فحص حالة الطلب اللحظي
# ================================================================

@router.callback_query(F.data.startswith("check_order_status:"))
async def handle_check_order_status(callback: CallbackQuery):
    order_code = callback.data.split(":")[1]
    order = await bridge_client.get_order_status(order_code)
    if not order:
        await callback.answer("⚠️ تعذر جلب حالة الطلب حالياً.", show_alert=True)
        return

    status = (order.get("status") or "").lower()
    status_map = {
        "pending_payment": "⏳ بانتظار وصول رسالة السداد من شبكة المحافظ (SMS)",
        "payment_confirmed": "🟢 تم استلام وتأكيد السداد بنجاح!",
        "processing_stamp": "⚙️ جاري الآن تشغيل محرك الختم والضغط الفائق...",
        "ready_for_delivery": "📦 اكتمل الختم والملف جاهز للتسليم!",
        "delivered": "✅ تم تسليم الملف بالفعل للمحادثة.",
        "cancelled": "❌ تم إلغاء الطلب من قبل الإدارة."
    }
    desc = status_map.get(status, status)
    await callback.answer(f"حالة الطلب {order_code}:\n{desc}", show_alert=True)


# ================================================================
# أزرار القائمة السفلية: شحن الرصيد ومتابعة الطلبات
# ================================================================

@router.message(F.text == "💳 شحن الرصيد والدفع")
async def handle_payment_menu(message: Message):
    text = (
        f"💳 <b>طرق الدفع والشحن المعتمدة في منصة Z-PACT</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"1️⃣ <b>فودافون كاش / المحافظ الإلكترونية:</b>\n"
        f"  • رقم المحفظة الأول: <code>{html.escape(bot_config.PAYMENT_WALLET_NUMBER)}</code>\n"
        f"  • رقم المحفظة الثاني: <code>{html.escape(bot_config.PAYMENT_WALLET_NUMBER_2)}</code>\n"
        f"  <i>(اضغط على أي رقم لنسخه فوراً)</i>\n\n"
        f"2️⃣ <b>إنستاباي (InstaPay):</b>\n"
        f"  • عنوان إنستاباي (رقم الهاتف): <code>{html.escape(bot_config.INSTAPAY_ADDRESS)}</code>\n\n"
        f"⚡ <b>ميزة التحقق اللحظي الآلي:</b>\n"
        f"عند اختيار أي مذكرة من الكتالوج وإرسال صورة التحويل، يتعرف النظام تلقائياً على رسالة البنك ويبدأ ختم المذكرة فوراً دون أي انتظار!"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "📦 متابعة طلباتي")
async def handle_my_orders_menu(message: Message):
    chat_id = message.chat.id
    user_orders = await bridge_client.get_user_orders(chat_id)

    if not user_orders:
        await message.answer(
            "📭 <b>ليس لديك أي طلبات سابقة مسجلة على هذا الحساب.</b>\n\n"
            "تصفح الكتالوج واطلب مذكرتك الأولى لتختم بأعلى دقة متجهة!",
            parse_mode="HTML"
        )
        return

    await message.answer(
        f"📦 <b>سجل طلباتك السابقة ({len(user_orders)}):</b>\n"
        f"يمكنك إعادة تحميل أي مذكرة مختومة مسبقاً بنقرة واحدة دون دفع إضافي 👇",
        parse_mode="HTML"
    )

    for o in user_orders[:6]:
        code = o.get("order_code")
        amt = o.get("total_amount")
        status = (o.get("status") or "").lower()
        title = (o.get("material") or {}).get("title") or "المذكرة التعليمية"
        buyer = o.get("buyer_name_stamp") or ""

        status_text = "🟡 بانتظار التأكيد"
        if status in ["ready_for_delivery", "delivered"]:
            status_text = "✅ مكتمل ومختوم"
        elif status in ["payment_confirmed", "processing_stamp"]:
            status_text = "⚙️ جاري المعالجة"
        elif status == "cancelled":
            status_text = "❌ ملغي"

        card_text = (
            f"🔖 <b>كود:</b> <code>{code}</code> | 💵 <b>{amt} ج.م</b>\n"
            f"📚 <b>المذكرة:</b> {html.escape(title)}\n"
            f"👤 <b>الاسم:</b> {html.escape(buyer)}\n"
            f"📊 <b>الحالة:</b> {status_text}"
        )

        buttons = []
        if status in ["ready_for_delivery", "delivered"]:
            buttons.append([InlineKeyboardButton(text="📥 إعادة تحميل المذكرة (PDF)", callback_data=f"redownload_order:{code}")])
        else:
            buttons.append([InlineKeyboardButton(text="🔄 تحديث حالة الطلب", callback_data=f"check_order_status:{code}")])

        await message.answer(card_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("redownload_order:"))
async def handle_redownload_order(callback: CallbackQuery):
    order_code = callback.data.split(":")[1]
    await callback.answer("⏳ جاري سحب وتحميل نسختك المختومة...")

    pdf_bytes = await bridge_client.download_order_pdf(order_code)
    if not pdf_bytes:
        await callback.message.answer("⚠️ تعذر استرجاع ملف المذكرة المختوم من السيرفر.")
        return

    input_file = BufferedInputFile(pdf_bytes, filename=f"ZPACT_{order_code}.pdf")
    await callback.message.answer_document(
        document=input_file,
        caption=f"✅ <b>إعادة تحميل نسختك المختومة للطلب <code>{order_code}</code></b>\n✨ نتمنى لكم دوام التوفيق والنجاح!",
        parse_mode="HTML"
    )


