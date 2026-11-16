# ================================================================
# bot_service/handlers/order.py — معالج تخصيص الأختام وتأكيد الطلب
# ================================================================

import html
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from bot_service.services.bridge_client import bridge_client
from bot_service.keyboards.inline import (
    get_profile_decision_keyboard,
    get_order_confirmation_keyboard,
    get_payment_methods_keyboard
)
from bot_service.keyboards.reply import get_cancel_keyboard, get_skip_keyboard, get_main_keyboard
from bot_service.states.order_flow import OrderWizardStates
from bot_service.config import bot_config
import stamp_settings

logger = logging.getLogger("bot_service.handlers.order")
router = Router(name="order_router")


async def render_order_summary(message_or_query, data: dict, edit: bool = False):
    """عرض بطاقة مراجعة وتأكيد بيانات الطلب والختم"""
    title = data.get("material_title", "مذكرة دراسية")
    price = data.get("total_amount", 0)
    name = (data.get("buyer_name_stamp") or "").strip() or "غير محدد"
    p1 = (data.get("buyer_phone_stamp") or "").strip() or "غير محدد"
    p2 = (data.get("buyer_phone_stamp_2") or "").strip() or "لا يوجد (رقم واحد فقط)"
    series = data.get("series_name") or data.get("doc_name_stamp") or "بدون (الافتراضي)"
    wm_type = data.get("watermark_type") or "none"
    if wm_type == "text":
        wm = data.get("watermark_text") or "علامة مائية نصية"
    elif wm_type in ("logo", "teacher"):
        wm = "لوجو / شعار"
    else:
        wm = "بدون علامة مائية (نسخة نقية)"

    summary_text = (
        f"📋 <b>مراجعة وتأكيد طلبك قبل الانتقال للدفع:</b> 🎓\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>المذكرة المطلوبة:</b> {html.escape(str(title))}\n"
        f"💵 <b>القيمة المطلوبة:</b> <code>{html.escape(str(price))}</code> ج.م\n\n"
        f"🏷️ <b>البيانات التي سيتم طباعتها على الـ PDF:</b>\n"
        f"  • <b>اسم المدرس:</b> {html.escape(str(name))}\n"
        f"  • <b>الموبايل الأساسي:</b> <code>{html.escape(str(p1))}</code>\n"
        f"  • <b>الموبايل الإضافي:</b> <code>{html.escape(str(p2))}</code>\n"
        f"  • <b>اسم السلسلة:</b> {html.escape(str(series))}\n"
        f"  • <b>العلامة المائية:</b> {html.escape(str(wm))}\n\n"
        f"🛡️ سيتم دمج علامات أمنية غير مرئية بكود طلبك لحمايتك من التسريب.\n"
        f"اضغط على <b>تأكيد الطلب والانتقال للدفع</b> للمتابعة 👇"
    )

    kb = get_order_confirmation_keyboard()

    if edit and isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.edit_text(summary_text, reply_markup=kb, parse_mode="HTML")
    elif isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.answer(summary_text, reply_markup=kb, parse_mode="HTML")
    else:
        await message_or_query.answer(summary_text, reply_markup=kb, parse_mode="HTML")


# ================================================================
# 1. بدء الطلب وفحص الملف التعريفي
# ================================================================

@router.callback_query(F.data.startswith("order_start:"))
async def handle_order_start(callback: CallbackQuery, state: FSMContext):
    mat_id = int(callback.data.split(":")[1])
    materials = await bridge_client.get_materials()
    mat = next((m for m in materials if m["id"] == mat_id), None)

    if not mat:
        await callback.answer("المذكرة غير موجودة", show_alert=True)
        return

    user_id = callback.from_user.id
    profile = await bridge_client.get_teacher_profile(user_id)

    # حفظ بيانات المادة في الـ FSM
    await state.update_data(
        material_id=mat_id,
        material_title=mat.get("title"),
        total_amount=float(mat.get("price", 0))
    )

    if profile and (bot_config.is_admin(user_id) or stamp_settings.has_any_available_stamp_data(profile)):
        # المدرس لديه ملف مسجل مسبقاً -> خيار بنقرة واحدة
        p_text = (
            f"⚡ <b>لديك ملف تعريفي مسجل مسبقاً!</b>\n\n"
            f"👤 <b>المدرس:</b> {html.escape(str(profile.get('teacher_name') or 'بدون اسم'))}\n"
            f"📱 <b>الموبايل:</b> <code>{html.escape(str(profile.get('primary_phone') or 'بدون رقم'))}</code>\n"
            f"📚 <b>السلسلة:</b> {html.escape(str(profile.get('series_title') or 'تلقائي'))}\n\n"
            f"هل ترغب باستخدام بياناتك المحفوظة فوراً لختم <b>{html.escape(str(mat.get('title') or ''))}</b>؟"
        )
        await callback.message.edit_text(
            text=p_text,
            reply_markup=get_profile_decision_keyboard(profile, mat_id),
            parse_mode="HTML"
        )
    else:
        # ليس لديه ملف -> بدء المعالج التفاعلي
        await state.set_state(OrderWizardStates.custom_name)
        await callback.message.answer(
            f"🎓 جاري تجهيز طلب مذكرة: <b>{html.escape(str(mat.get('title')))}</b>\n\n"
            f"🧭 <b>خطوات تخصيص الختم:</b> [■□□□□] 20% — <b>الخطوة [1/5]</b>\n"
            f"✍️ ما هو اسم المدرس أو اللقب المراد طباعته على الأختام؟\n"
            f"<i>(مثال: أ/ محمد عبد الرحمن — أو اضغط 'تخطي ⏩' للاكتفاء ببيان آخر)</i>",
            reply_markup=get_skip_keyboard(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("order_use_profile:"))
async def handle_use_profile(callback: CallbackQuery, state: FSMContext):
    """استخدام بيانات البروفايل المحفوظة وتجهيز بطاقة التأكيد"""
    user_id = callback.from_user.id
    profile = await bridge_client.get_teacher_profile(user_id)
    if not profile:
        await callback.answer("تعذر استرجاع الملف", show_alert=True)
        return

    data = await state.get_data()
    data.update({
        "buyer_name_stamp": profile.get("teacher_name"),
        "buyer_phone_stamp": profile.get("primary_phone"),
        "buyer_phone_stamp_2": profile.get("secondary_phone"),
        "doc_name_stamp": profile.get("series_title"),
        "watermark_text": profile.get("watermark_text"),
        "watermark_type": profile.get("watermark_type", "text"),
        "watermark_logo_path": profile.get("logo_image_path"),
        "teacher_profile_id": profile.get("id"),
        "claimed_sender_phone": profile.get("primary_phone")
    })
    await state.set_data(data)
    await render_order_summary(callback, data, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("order_custom_stamp:"))
async def handle_custom_stamp(callback: CallbackQuery, state: FSMContext):
    """بدء تخصيص بيانات ختم جديدة لطلب واحد"""
    data = await state.get_data()
    title = data.get("material_title", "المذكرة")

    await state.set_state(OrderWizardStates.custom_name)
    await callback.message.answer(
        f"✏️ <b>تخصيص أختام طلب:</b> {html.escape(str(title))}\n\n"
        f"🧭 <b>خطوات تخصيص الختم:</b> [■□□□□] 20% — <b>الخطوة [1/5]</b>\n"
        f"✍️ أدخل اسم المدرس المراد كتابته على المذكرة:\n"
        f"<i>(مثال: أ/ إبراهيم عادل — أو اضغط 'تخطي ⏩' للاكتفاء ببيان آخر)</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


# ================================================================
# 2. خطوات معالج إدخال بيانات الختم المخصصة
# ================================================================

def _is_skip_input(raw: str) -> bool:
    return (raw or "").strip() in ("تخطي ⏩", "تخطي", "-", "بدون")


@router.message(OrderWizardStates.custom_name)
async def process_order_name(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if _is_skip_input(raw):
        name = ""
    else:
        name = raw
        if not is_admin and len(name) < stamp_settings.MIN_STAMP_VALUE_LENGTH_WHEN_PRESENT:
            await message.answer("⚠️ يرجى إدخال اسم صحيح أو الضغط على 'تخطي ⏩'.")
            return

    await state.update_data(buyer_name_stamp=name)
    await state.set_state(OrderWizardStates.custom_phone_1)
    await message.answer(
        "🧭 <b>خطوات تخصيص الختم:</b> [■■□□□] 40% — <b>الخطوة [2/5]</b>\n"
        "📱 أدخل رقم الهاتف الأساسي للتواصل:\n"
        "<i>(أدخل الرقم أو اضغط على 'تخطي ⏩' إذا كنت تكتفي بالاسم أو اسم المذكرة)</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(OrderWizardStates.custom_phone_1)
async def process_order_phone_1(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if _is_skip_input(raw):
        phone = ""
    else:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not is_admin and len(digits) < 6:
            await message.answer("⚠️ يرجى إدخال رقم هاتف صحيح أو الضغط على 'تخطي ⏩'.")
            return
        phone = raw

    await state.update_data(buyer_phone_stamp=phone, claimed_sender_phone=phone)
    await state.set_state(OrderWizardStates.custom_phone_2)
    await message.answer(
        "🧭 <b>خطوات تخصيص الختم:</b> [■■■□] 75% — <b>الخطوة [3/4]</b>\n"
        "📞 هل لديك رقم هاتف إضافي ترغب بظهوره بالتبادل؟\n"
        "<i>(أدخل الرقم أو اضغط على 'تخطي ⏩')</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(OrderWizardStates.custom_phone_2)
async def process_order_phone_2(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    phone_2 = None if _is_skip_input(raw) or not raw else raw

    await state.update_data(buyer_phone_stamp_2=phone_2)
    await state.set_state(OrderWizardStates.custom_series)
    await message.answer(
        "🧭 <b>خطوات تخصيص الختم:</b> [■■■□] 75% — <b>الخطوة [4/5]</b>\n"
        "📚 <b>ما هو اسم السلسلة المراد طباعتها بالختم؟</b> (اختياري)\n"
        "<i>(مثال: سلسلة الأوائل في الفيزياء ، أو اضغط على 'تخطي ⏩')</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(OrderWizardStates.custom_series)
async def process_order_series(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    series = None if _is_skip_input(raw) or not raw else raw

    await state.update_data(series_name=series, doc_name_stamp=series)
    await state.set_state(OrderWizardStates.custom_watermark)
    await message.answer(
        "🧭 <b>خطوات تخصيص الختم:</b> [■■■■] 100% — <b>الخطوة [5/5]</b>\n"
        "💧 <b>هل ترغب في إضافة علامة مائية بمنتصف الصفحات؟</b> (اختياري)\n"
        "<i>(اكتب نص العلامة المائية مثل: 'خاص بطلاب الأستاذ' ، أو اضغط على 'تخطي ⏩' لنسخة نقية بدون علامة مائية)</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(OrderWizardStates.custom_watermark)
async def process_order_watermark(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    wm_text = None
    wm_type = "none"
    if raw and not _is_skip_input(raw):
        wm_text = raw
        wm_type = "text"

    await state.update_data(watermark_text=wm_text, watermark_type=wm_type)
    data = await state.get_data()

    is_admin = bot_config.is_admin(message.from_user.id)
    ok_stamp, err_msg = stamp_settings.validate_stamp_fields(
        buyer_name_stamp=data.get("buyer_name_stamp"),
        buyer_phone_stamp=data.get("buyer_phone_stamp"),
        buyer_phone_stamp_2=data.get("buyer_phone_stamp_2"),
        series_name=data.get("series_name"),
        doc_name_stamp=data.get("doc_name_stamp"),
        watermark_text=wm_text,
        is_admin=is_admin,
    )
    if not ok_stamp:
        await state.set_state(OrderWizardStates.custom_name)
        await message.answer(
            f"⚠️ {err_msg or stamp_settings.MISSING_STAMP_DATA_MESSAGE_AR}\n\n"
            f"✍️ أدخل اسم المدرس المراد كتابته (أو اضغط 'تخطي ⏩' لإدخال رقم هاتف أو اسم مذكرة):",
            reply_markup=get_skip_keyboard(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        "✅ <b>تم استكمال وتخصيص بيانات الختم بنجاح!</b>\nراجع التفاصيل في البطاقة التالية:",
        reply_markup=get_main_keyboard(is_admin=is_admin),
        parse_mode="HTML"
    )
    await render_order_summary(message, data, edit=False)


# ================================================================
# 3. إلغاء الطلب
# ================================================================

@router.callback_query(F.data == "order_cancel")
async def handle_order_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ تم إلغاء الطلب بنجاح. يمكنك دائماً العودة للقائمة من الأزرار بالأسفل.")
    await callback.answer()


# ================================================================
# 4. الانتقال لاختيار وسيلة الدفع
# ================================================================

@router.callback_query(F.data == "order_proceed_pay")
async def handle_proceed_payment(callback: CallbackQuery):
    """عرض وسائل الدفع المعتمدة"""
    pay_text = (
        f"💳 <b>اختر وسيلة الدفع المناسبة لك:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ <b>فودافون كاش:</b> تحويل فوري من أي محفظة إلكترونية في مصر.\n"
        f"2️⃣ <b>إنستاباي (InstaPay):</b> تحويل لحظي من أي بنك أو كارت ميزة.\n\n"
        f"⚡ محركنا الآلي يراقب التحويل في الوقت الحقيقي ويقوم بتأكيده وتوليد المذكرة فوراً!"
    )
    await callback.message.edit_text(
        text=pay_text,
        reply_markup=get_payment_methods_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

