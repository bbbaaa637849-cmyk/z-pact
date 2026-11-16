# ================================================================
# bot_service/handlers/start.py — الترحيب والتهيئة وقائمة البداية
# ================================================================

import html
import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, MenuButtonWebApp, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext

from bot_service.config import bot_config
import stamp_settings
from bot_service.services.bridge_client import bridge_client
from bot_service.keyboards.reply import get_main_keyboard, get_cancel_keyboard, get_skip_keyboard
from bot_service.keyboards.inline import get_admin_main_keyboard
from bot_service.states.order_flow import TeacherProfileStates, OtpVerificationStates

logger = logging.getLogger("bot_service.handlers.start")
router = Router(name="start_router")


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext):
    """الترحيب وعرض لوحة التحكم الذكية وحالة الملف التعريفي ومطالبة رموز OTP"""
    await state.clear()
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "أستاذنا الفاضل"
    is_admin = bot_config.is_admin(user_id)

    # 0. فحص هل الرابط هو رابط استلام كود OTP لحظي (1-Tap Deep-Link: /start otp_<token>)
    start_parts = (message.text or "").split(maxsplit=1)
    if len(start_parts) > 1 and start_parts[1].startswith("otp_"):
        challenge_token = start_parts[1].replace("otp_", "").strip()
        claim_res = await bridge_client.claim_telegram_otp(
            challenge_token=challenge_token,
            telegram_user_id=user_id,
            telegram_username=message.from_user.username
        )
        if claim_res:
            st = claim_res.get("status")
            if st == "ok":
                otp_code = claim_res.get("otp_code", "")
                role_label = "المدرسين" if claim_res.get("target_role") == "teacher" else "فريق المرسلين"
                await message.answer(
                    f"🔐 <b>رمز التحقق السريع (OTP) لمنظومة Z-PACT:</b>\n\n"
                    f"<code>{otp_code}</code>\n\n"
                    f"📱 <b>الحساب الموثق:</b> {role_label} (<code>{claim_res.get('phone')}</code>)\n"
                    f"⏱️ <i>الكود صالح لمدة 10 دقائق للاستخدام لمرة واحدة.</i>\n\n"
                    f"اضغط على الرقم لنسخه فوراً والصقه في شاشة الدخول بالتطبيق! ✨",
                    reply_markup=ReplyKeyboardRemove(),
                    parse_mode="HTML"
                )
                return
            elif st == "need_contact_verification":
                role_label = "المدرسين" if claim_res.get("target_role") == "teacher" else "فريق المرسلين"
                masked = claim_res.get("masked_phone", "")
                kb = ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="📱 مشاركة رقم هاتفي لتأكيد هويتي واستلام الكود", request_contact=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
                await message.answer(
                    f"🛡️ <b>تأكيد الهوية لمنظومة Z-PACT ({role_label}):</b>\n\n"
                    f"مطلوب إصدار كود تحقق للرقم: <code>{masked}</code>\n\n"
                    f"⚠️ <b>حماية أمنية مشددة:</b> لمنع انتحال الشخصية، يجب التأكد من أن حساب التليجرام هذا مسجل بنفس رقم الهاتف المطلوب في التطبيق.\n\n"
                    f"👇 اضغط على الزر الكبير بالأسفل <b>(مشاركة رقم هاتفي)</b> للتحقق فوراً واستلام الكود:",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                await state.set_state(OtpVerificationStates.waiting_contact)
                await state.update_data(
                    otp_challenge_token=challenge_token,
                    expected_phone=claim_res.get("required_phone")
                )
                return
            elif st == "phone_mismatch":
                await message.answer(
                    f"⛔ <b>خطأ في تطابق رقم الهاتف:</b>\n\n"
                    f"رقم حساب التليجرام الحالي لا يتطابق مع رقم الهاتف المطلوب توثيقه في التطبيق.\n"
                    f"يرجى فتح الرابط من حساب التليجرام المسجل به نفس الرقم.",
                    reply_markup=ReplyKeyboardRemove(),
                    parse_mode="HTML"
                )
                return
            else:
                await message.answer(
                    claim_res.get("message") or "⚠️ رمز التحقق منتهي الصلاحية أو تم استخدامه بالفعل.",
                    reply_markup=ReplyKeyboardRemove(),
                    parse_mode="HTML"
                )
                return

    # 1. مزامنة الجلسة مع المنصة الأساسية
    await bridge_client.sync_session(
        telegram_chat_id=message.chat.id,
        telegram_user_id=user_id,
        current_state="START_MENU",
        username=message.from_user.username
    )

    # 2. فحص هل المدرس يمتلك ملفاً تعريفياً محفوظاً مسبقاً
    profile = await bridge_client.get_teacher_profile(user_id)

    profile_status_text = ""
    if profile:
        t_name = html.escape(str(profile.get('teacher_name') or 'غير محدد'))
        t_phone = html.escape(str(profile.get('primary_phone') or 'غير محدد'))
        t_series = html.escape(str(profile.get('series_title') or 'غير محدد'))
        profile_status_text = (
            f"👤 <b>الملف المحفوظ:</b> {t_name}\n"
            f"📱 <b>رقم التواصل:</b> <code>{t_phone}</code>\n"
            f"📚 <b>السلسلة:</b> {t_series}\n"
            f"✨ بياناتك جاهزة للختم الفوري بنقرة واحدة!"
        )
    else:
        profile_status_text = (
            "⚠️ <b>تنبيه:</b> لم تقم بتسجيل بيانات الختم الخاصة بك بعد.\n"
            "اضغط على زر <code>👤 بيانات الختم الخاصة بي</code> لتسجيل اسمك ورقمك مرة واحدة."
        )

    safe_user_name = html.escape(str(user_name))
    welcome_text = (
        f"مرحباً بك يا {safe_user_name} في <b>منصة Z-PACT الذكية للمذكرات والمستندات</b> 🎓✨\n\n"
        f"نظام متكامل لطلب وتحميل مذكراتك التعليمية مختومة برقمك واسمك بدقة متجهة فائقة، "
        f"مع حماية رقمية متطورة ضد التسريب وإعادة المشاركة.\n\n"
        f"────────────────────\n"
        f"{profile_status_text}\n"
        f"────────────────────\n"
        f"اختر ما تريد من القائمة بالأسفل للبدء فوراً 👇"
    )

    await message.answer(
        text=welcome_text,
        reply_markup=get_main_keyboard(is_admin=is_admin),
        parse_mode="HTML"
    )

    # تحديث زر القائمة الدائم للمحادثة الحالية وإظهار زر فتح سريع بآخر رابط حي
    live_url = bot_config.get_live_miniapp_url(cache_bust=True)
    if live_url.lower().startswith("https://"):
        try:
            await message.bot.set_chat_menu_button(
                chat_id=message.chat.id,
                menu_button=MenuButtonWebApp(
                    text="📱 المتجر والتطبيق الذكي",
                    web_app=WebAppInfo(url=live_url),
                ),
            )
        except Exception:
            pass
        await message.answer(
            "⚡ <b>يمكنك أيضاً فتح المتجر ومحاكي الختم التفاعلي بنقرة واحدة من هنا:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="🚀 فتح المتجر والتطبيق الذكي (Mini App)", web_app=WebAppInfo(url=live_url))
                ]]
            ),
            parse_mode="HTML",
        )


@router.message(OtpVerificationStates.waiting_contact, F.contact)
async def process_otp_contact_verification(message: Message, state: FSMContext):
    """التحقق المشدد من تطابق رقم الهاتف الموثق في تليجرام مع رقم حساب المنصة"""
    contact = message.contact
    if not contact:
        await message.answer("⚠️ يرجى الضغط على زر مشاركة رقم الهاتف بالأسفل.")
        return

    # فحص أمني: التأكد من أن جهة الاتصال تخص نفس المستخدم
    if contact.user_id != message.from_user.id:
        await message.answer(
            "⛔ <b>خطأ أمني:</b> يجب مشاركة رقم هاتفك الحقيقي المسجل به هذا الحساب، وليس بطاقة جهة اتصال أخرى!",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        await state.clear()
        return

    data = await state.get_data()
    challenge_token = data.get("otp_challenge_token")
    expected_phone = data.get("expected_phone")
    user_phone = contact.phone_number or ""

    claim_res = await bridge_client.claim_telegram_otp(
        challenge_token=challenge_token,
        telegram_user_id=message.from_user.id,
        telegram_username=message.from_user.username,
        telegram_phone=user_phone
    )
    await state.clear()

    if not claim_res:
        await message.answer(
            "⚠️ حدث خطأ أو انتهت صلاحية كود التحقق. يرجى إعادة طلب الكود من التطبيق.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        return

    st = claim_res.get("status")
    if st == "ok":
        otp_code = claim_res.get("otp_code", "")
        role_label = "المدرسين" if claim_res.get("target_role") == "teacher" else "فريق المرسلين"
        await message.answer(
            f"✅ <b>تم التحقق وتوثيق تطابق رقم هاتفك بنجاح!</b>\n\n"
            f"🔐 <b>رمز التحقق السريع (OTP):</b>\n\n"
            f"<code>{otp_code}</code>\n\n"
            f"📱 <b>الحساب الموثق:</b> {role_label} (<code>{claim_res.get('phone')}</code>)\n"
            f"⏱️ <i>صالح لمدة 10 دقائق للاستخدام لمرة واحدة.</i>\n\n"
            f"اضغط على الرقم لنسخه فوراً والصقه في شاشة التسجيل بالتطبيق! ✨",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
    elif st == "phone_mismatch":
        await message.answer(
            f"⛔ <b>خطأ أمني: عدم تطابق رقم الهاتف!</b>\n\n"
            f"📱 رقم هذا الحساب على تليجرام: <code>{user_phone}</code>\n"
            f"📱 الرقم المطلوب في التطبيق: <code>{expected_phone}</code>\n\n"
            f"❌ لا يمكن تسليم كود التفعيل إلا لحساب تليجرام يحمل نفس رقم الهاتف المسجل تماماً.\n"
            f"يرجى فتح الرابط من جهازك المسجل به رقم الهاتف المطلوب.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            claim_res.get("message") or "⚠️ رمز التحقق غير صالح أو انتهت صلاحيته.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )



@router.message(Command("id", "myid"))
async def handle_show_telegram_id(message: Message):
    """عرض معرف تيليجرام الرقمي للمستخدم ليسهل نسخه وإضافته كمشرف."""
    uid = message.from_user.id
    await message.answer(
        f"🆔 <b>معرف حسابك في تيليجرام (ID):</b> <code>{uid}</code>\n"
        f"<i>اضغط على الرقم لنسخه وإرساله للإدارة.</i>",
        parse_mode="HTML",
    )


@router.message(Command("admin_auth"))
async def handle_admin_auth(message: Message):
    """أمر المصادقة السريع للأدمن برمز المرور السري"""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ الصيغة: <code>/admin_auth &lt;الرمز_السري&gt;</code>", parse_mode="HTML")
        return

    secret = args[1].strip()
    if secret == bot_config.ADMIN_PASSCODE:
        bot_config.add_admin(message.from_user.id)
        await message.answer(
            "👑 <b>تمت ترقية حسابك إلى مسؤول النظام (Admin) بنجاح!</b>\n"
            "يمكنك الآن استخدام أمر <code>/admin</code> أو زر لوحة التحكم.",
            reply_markup=get_main_keyboard(is_admin=True),
            parse_mode="HTML"
        )
    else:
        await message.answer("⛔ رمز المرور السري غير صحيح!")


@router.message(F.text == "❌ إلغاء والعودة للقائمة الرئيسية")
async def handle_cancel_to_main(message: Message, state: FSMContext):
    """إلغاء أي عملية حالية والعودة للقائمة الرئيسية"""
    await state.clear()
    is_admin = bot_config.is_admin(message.from_user.id)
    await message.answer(
        "تم إلغاء العملية والعودة للقائمة الرئيسية بنجاح.",
        reply_markup=get_main_keyboard(is_admin=is_admin)
    )


# ================================================================
# عرض وتعديل بيانات الختم الخاصة بالمدرس
# ================================================================

@router.message(F.text == "👤 بيانات الختم الخاصة بي")
async def show_teacher_profile(message: Message):
    user_id = message.from_user.id
    profile = await bridge_client.get_teacher_profile(user_id)

    if profile:
        text = (
            f"👤 <b>بيانات الختم المعتمدة الخاصة بك:</b>\n\n"
            f"👨‍🏫 <b>اسم المدرس بالختم:</b> {html.escape(str(profile.get('teacher_name') or 'غير محدد'))}\n"
            f"📱 <b>رقم الموبايل الأساسي:</b> <code>{html.escape(str(profile.get('primary_phone') or 'غير محدد'))}</code>\n"
            f"📞 <b>رقم الموبايل الإضافي:</b> {html.escape(str(profile.get('secondary_phone') or 'غير مسجل'))}\n"
            f"🔬 <b>المادة والتخصص:</b> {html.escape(str(profile.get('subject_specialty') or 'عام'))}\n"
            f"📚 <b>اسم السلسلة:</b> {html.escape(str(profile.get('series_title') or 'تلقائي'))}\n"
            f"💧 <b>العلامة المائية:</b> {html.escape(str(profile.get('watermark_text') or 'الافتراضي'))}\n\n"
            f"لتحديث هذه البيانات، أرسل الأمر: <code>/edit_profile</code>"
        )
    else:
        text = (
            "⚠️ <b>ليس لديك ملف تعريفي مسجل بعد!</b>\n\n"
            "تسجيل بياناتك يتيح للنظام ختم مذكراتك تلقائياً باسمك وأرقامك بنقرة زر واحدة.\n"
            "للبدء في تسجيل بياناتك الآن، أرسل الأمر: <code>/edit_profile</code>"
        )

    await message.answer(text, parse_mode="HTML")


@router.message(Command("edit_profile"))
async def start_profile_edit(message: Message, state: FSMContext):
    """بدء معالج تعديل بيانات الختم"""
    await state.set_state(TeacherProfileStates.waiting_name)
    await message.answer(
        "✍️ <b>الخطوة [1/5]:</b> ما هو اسمك الأكاديمي الذي ترغب بظهوره على أختام المذكرة؟\n"
        "<i>(مثال: أ/ محمد عبد الرحمن — أو اضغط 'تخطي ⏩' للاكتفاء ببيان آخر)</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(TeacherProfileStates.waiting_name)
async def process_profile_name(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if raw in ("تخطي ⏩", "تخطي", "-", "بدون"):
        name = ""
    else:
        name = raw
        if not is_admin and len(name) < stamp_settings.MIN_STAMP_VALUE_LENGTH_WHEN_PRESENT:
            await message.answer("⚠️ يرجى إدخال اسم صحيح أو الضغط على 'تخطي ⏩'.")
            return

    await state.update_data(teacher_name=name)
    await state.set_state(TeacherProfileStates.waiting_phone_1)
    await message.answer(
        "📱 <b>الخطوة [2/5]:</b> أدخل رقم الموبايل الأساسي للتواصل:\n"
        "<i>(مثال: 01012345678 — أو اضغط 'تخطي ⏩')</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(TeacherProfileStates.waiting_phone_1)
async def process_profile_phone_1(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if raw in ("تخطي ⏩", "تخطي", "-", "بدون"):
        phone = ""
    else:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not is_admin and len(digits) < 6:
            await message.answer("⚠️ يرجى إدخال رقم هاتف صحيح أو الضغط على 'تخطي ⏩'.")
            return
        phone = raw

    await state.update_data(primary_phone=phone)
    await state.set_state(TeacherProfileStates.waiting_phone_2)
    await message.answer(
        "📞 <b>الخطوة [3/5]:</b> هل لديك رقم هاتف ثانٍ ترغب بظهوره بالتبادل على الصفحات الزوجية؟\n"
        "<i>(يمكنك الضغط على 'تخطي ⏩' إذا كنت تستخدم رقماً واحداً فقط)</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(TeacherProfileStates.waiting_phone_2)
async def process_profile_phone_2(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    phone_2 = None if raw in ("تخطي ⏩", "تخطي", "-", "بدون", "") else raw

    await state.update_data(secondary_phone=phone_2)
    await state.set_state(TeacherProfileStates.waiting_series)
    await message.answer(
        "📚 <b>الخطوة [4/5]:</b> ما هو اسم السلسلة أو الشعار التعليمي الخاص بك؟\n"
        "<i>(مثال: سلسلة البرهان ، سلسلة التفوق ، أو اضغط 'تخطي ⏩')</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(TeacherProfileStates.waiting_series)
async def process_profile_series(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    series = None if raw in ("تخطي ⏩", "تخطي", "-", "بدون", "") else raw

    await state.update_data(series_title=series)
    await state.set_state(TeacherProfileStates.waiting_watermark)
    await message.answer(
        "💧 <b>الخطوة [5/5]:</b> هل ترغب بكتابة نص مخصص للعلامة المائية المائلة في خلفية الصفحات؟\n"
        "<i>(مثال: خاص لطلاب الأستاذ أحمد أو لا يجوز تصوير المذكرة — أو اضغط 'تخطي ⏩')</i>",
        reply_markup=get_skip_keyboard(),
        parse_mode="HTML"
    )


@router.message(TeacherProfileStates.waiting_watermark)
async def process_profile_finish(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    watermark = None if raw in ("تخطي ⏩", "تخطي", "-", "بدون", "") else raw

    is_admin = bot_config.is_admin(message.from_user.id)
    data = await state.get_data()
    ok_stamp, err_msg = stamp_settings.validate_stamp_fields(
        teacher_name=data.get("teacher_name"),
        primary_phone=data.get("primary_phone"),
        secondary_phone=data.get("secondary_phone"),
        series_title=data.get("series_title"),
        watermark_text=watermark,
        is_admin=is_admin,
    )
    if not ok_stamp:
        await state.set_state(TeacherProfileStates.waiting_name)
        await message.answer(
            f"⚠️ {html.escape(str(err_msg or stamp_settings.MISSING_STAMP_DATA_MESSAGE_AR))}\n\n"
            f"✍️ <b>الخطوة [1/5]:</b> أدخل اسمك الأكاديمي (أو اضغط 'تخطي ⏩' لإدخال الهاتف أو اسم السلسلة):",
            reply_markup=get_skip_keyboard(),
            parse_mode="HTML",
        )
        return

    data["watermark_text"] = watermark
    data["telegram_user_id"] = str(message.from_user.id)
    data["telegram_username"] = message.from_user.username
    data["is_admin"] = is_admin

    # حفظ في قاعدة البيانات عبر الجسر الداخلي
    saved = await bridge_client.upsert_teacher_profile(data)
    await state.clear()

    if saved:
        await message.answer(
            f"🎉 <b>تم حفظ وتأكيد بيانات الختم الخاصة بك بنجاح!</b>\n\n"
            f"👨‍🏫 <b>الاسم:</b> {html.escape(str(saved.get('teacher_name') or 'غير محدد'))}\n"
            f"📱 <b>الهاتف:</b> <code>{html.escape(str(saved.get('primary_phone') or 'غير محدد'))}</code>\n"
            f"📚 <b>السلسلة:</b> {html.escape(str(saved.get('series_title') or 'تلقائي'))}\n\n"
            f"عند طلب أي مذكرة، سيتم ختمها بهذه البيانات تلقائياً بنقرة واحدة! 🚀",
            reply_markup=get_main_keyboard(is_admin=is_admin),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "⚠️ حدث خطأ أثناء حفظ البيانات، يرجى المحاولة لاحقاً.",
            reply_markup=get_main_keyboard(is_admin=is_admin)
        )


# ================================================================
# متابعة الطلبات السابقة وشحن الرصيد
# ================================================================

@router.message(F.text == "📦 متابعة طلباتي")
async def show_user_orders(message: Message):
    """عرض قائمة بطلبات المستخدم السابقة وحالاتها"""
    user_id = message.from_user.id
    orders = await bridge_client.get_user_orders(user_id)

    if not orders:
        await message.answer(
            "📭 <b>ليس لديك أي طلبات سابقة حتى الآن.</b>\n\n"
            "يمكنك استعراض المذكرات المتاحة والطلب فوراً عبر الضغط على: <code>📚 تصفح المذكرات والطلب</code>.",
            parse_mode="HTML"
        )
        return

    status_labels = {
        "pending_payment": "⏳ بانتظار تحويل الرسوم ورفع الإيصال",
        "payment_confirmed": "✅ تم تأكيد الدفع وجاري بدء الختم",
        "stamping_in_progress": "⚙️ جاري ختم المذكرة وضغط ملف الـ PDF",
        "ready_for_delivery": "🚀 جاهز للتسليم (جاري الإرسال عبر البوت)",
        "delivered": "🎉 تم تسليم الملف بنجاح",
        "failed": "⚠️ تعذر المعالجة (تواصل مع المشرف)",
        "cancelled": "❌ تم إلغاء الطلب"
    }

    text = f"📦 <b>سجل طلباتك الأحدث ({len(orders)}):</b>\n\n"
    for o in orders[:5]:
        code = html.escape(str(o.get("order_code") or ""))
        mat_title = html.escape(str(o.get("material", {}).get("title") if o.get("material") else "مذكرة تعليمية"))
        amt = html.escape(str(o.get("total_amount", 0)))
        status_key = o.get("status", "pending_payment")
        status_text = html.escape(str(status_labels.get(status_key, status_key)))
        created_at = html.escape(str((o.get("created_at") or "")[:10]))

        text += (
            f"🔖 <b>الطلب:</b> <code>{code}</code> — {mat_title}\n"
            f"📅 <b>التاريخ:</b> {created_at} | 💵 <b>القيمة:</b> {amt} ج.م\n"
            f"📊 <b>الحالة:</b> {status_text}\n"
            f"────────────────────\n"
        )

    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "💳 شحن الرصيد والدفع")
async def show_payment_info(message: Message):
    """عرض معلومات وسائل الدفع المعتمدة والتحويل"""
    wallet1 = html.escape(str(bot_config.PAYMENT_WALLET_NUMBER))
    wallet2 = html.escape(str(getattr(bot_config, "PAYMENT_WALLET_NUMBER_2", "01032881402")))
    instapay = html.escape(str(bot_config.INSTAPAY_ADDRESS))

    text = (
        "💳 <b>وسائل الدفع والشحن المعتمدة في منصة Z-PACT:</b>\n\n"
        "يمكنك سداد رسوم المذكرات والطلبات عبر القنوات التالية مباشرة:\n\n"
        f"📱 <b>محافظ فودافون كاش المعتمدة (اضغط للنسخ):</b>\n"
        f"👉 <code>{wallet1}</code>\n"
        f"👉 <code>{wallet2}</code>\n\n"
        f"⚡ <b>تطبيق إنستاباي (رقم الهاتف):</b>\n"
        f"👉 <code>{instapay}</code> <i>(اضغط للنسخ)</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>كيف يعمل النظام؟</b>\n"
        "عند طلبك لأي مذكرة من الكتالوج، يمنحك البوت كود طلب فريد ورابط مباشر لتأكيد السداد فورياً، "
        "مع فحص آلي لحظي لرسائل التحويل البنكية لختم ملفك في ثوانٍ معدودة!"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "🌐 فتح التطبيق التفاعلي (Mini App)")
@router.callback_query(F.data == "miniapp_info")
async def handle_miniapp_request(event: Message | CallbackQuery):
    """توجيه المستخدم لرابط التطبيق المصغر وتحديث زر القائمة دائماً بأحدث رابط حي"""
    clean_url = bot_config.get_live_miniapp_url(cache_bust=False)
    live_url = bot_config.get_live_miniapp_url(cache_bust=True)
    is_https = live_url.lower().startswith("https://")
    target_chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
    bot = event.bot
    
    reply_markup = None
    if is_https:
        try:
            await bot.set_chat_menu_button(
                chat_id=target_chat_id,
                menu_button=MenuButtonWebApp(
                    text="📱 المتجر والتطبيق الذكي",
                    web_app=WebAppInfo(url=live_url),
                ),
            )
        except Exception:
            pass
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 اضغط هنا لفتح المتجر والتطبيق الذكي الآن", web_app=WebAppInfo(url=live_url))
        ]])

    text = (
        "🌐 <b>التطبيق التفاعلي للمنصة (Telegram Mini App &amp; PWA)</b>\n\n"
        f"🔗 <b>رابط المعاينة المباشر النشط الآن:</b>\n"
        f"<code>{html.escape(str(clean_url))}</code>\n\n"
        "✨ <i>الميزات المتوفرة داخل التطبيق:</i>\n"
        "• 🎨 محاكي الختم الحي لورقة A4 ومعاينة العلامة المائية لحظياً.\n"
        "• 📚 استعراض وتصفية كتالوج المذكرات والطلب المباشر.\n"
        "• 📦 متابعة فورية لحالات طلباتك وتحميل الـ PDF.\n\n"
    )
    if is_https:
        text += "💡 <i>اضغط على الزر أدناه لفتح التطبيق كنافذة مدمجة داخل تيليجرام فوراً!</i>"
    else:
        text += (
            "💡 <i>ملاحظة:</i> يفرض تطبيق تليجرام روابط مشفرة (HTTPS) لفتح النوافذ المنبثقة التفاعلية داخل المحادثة مباشرة. "
            "قم بتشغيل النفق المشفر (Cloudflare Tunnel) ليظهر زر الفتح الفوري."
        )

    if isinstance(event, CallbackQuery):
        await event.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=reply_markup, parse_mode="HTML")


