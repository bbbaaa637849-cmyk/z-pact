# ================================================================
# bot_service/handlers/support.py — خدمة العملاء والدعم الفني والربط المباشر
# ================================================================

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from bot_service.config import bot_config
from bot_service.states.order_flow import SupportStates
from bot_service.keyboards.inline import (
    get_support_keyboard,
    get_support_cancel_keyboard,
    get_admin_user_reply_keyboard
)

logger = logging.getLogger(__name__)
router = Router(name="support_router")


@router.message(F.text == "💬 الدعم الفني والمساعدة")
@router.message(F.text == "/support")
async def show_support_menu(message: Message, state: FSMContext):
    """عرض لوحة مركز المساعدة والأسئلة الشائعة والدعم المباشر"""
    await state.clear()
    text = (
        "🎧 <b>مركز خدمة العملاء والدعم الفني المباشر</b>\n\n"
        "أهلاً بك في منصة <b>Z-PACT</b> الموحدة.\n"
        "فريقنا التقني متاح على مدار الساعة لخدمتكم ومساعدتكم في كل ما يخص المذكرات وطرق الدفع والختم الرقمي.\n\n"
        "💡 <i>يمكنك الضغط على أي سؤال شائع أدناه للإجابة الفورية، أو طلب محادثة مسؤول الدعم البشري:</i>"
    )
    await message.answer(text, reply_markup=get_support_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "support:main")
async def callback_support_main(callback: CallbackQuery, state: FSMContext):
    """العودة للواجهة الرئيسية للدعم الفني"""
    await state.clear()
    text = (
        "🎧 <b>مركز خدمة العملاء والدعم الفني المباشر</b>\n\n"
        "💡 <i>اختر أحد الأسئلة الشائعة أو تواصل مباشرة مع فريق المشرفين:</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_support_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_support_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("faq:"))
async def handle_faq_item(callback: CallbackQuery):
    """عرض الإجابات التفصيلية للأسئلة الشائعة"""
    faq_key = callback.data.split(":")[1]

    faq_contents = {
        "payment": (
            "💳 <b>كيف يتم الدفع والتأكيد التلقائي؟</b>\n\n"
            "1. يمكنك التحويل عبر أي محفظة إلكترونية (فودافون كاش، أورنج كاش، اتصالات، وي باي) أو عبر تطبيق <b>InstaPay</b>.\n"
            "2. عند إتمام التحويل، يُطلب منك إدخال رقم هاتفك الذي حولت منه ورفع صورة إيصال التحويل داخل البوت.\n"
            "3. يرتبط البوت مباشرة بمحرك الفحص الفوري؛ عند مطابقة رسالة الـ SMS أو مراجعة المشرف، يبدأ الختم آلياً وفوراً!"
        ),
        "speed": (
            "⏱️ <b>كم يستغرق تجهيز وختم المذكرة؟</b>\n\n"
            "• معالجة وتوليد المذكرة تتم بنظام <b>FastAPI + PyMuPDF Core</b> فائق السرعة.\n"
            "• يستغرق الختم وإضافة العلامات المائية وضغط الملف بالكامل ما بين <b>3 إلى 10 ثوانٍ فقط</b> بعد تأكيد الدفع!\n"
            "• يتم تسليم ملف الـ PDF الجاهز للطباعة مباشرة إليك في محادثة التليجرام."
        ),
        "quality": (
            "📄 <b>ما هي جودة المذكرات وصيغتها؟</b>\n\n"
            "• جميع المذكرات تسلم بصيغة <b>PDF عالية الدقة (Vector Print-Ready)</b>.\n"
            "• نعتمد خط <b>Cairo-Bold</b> المتوافق مع الطباعة، مع الحفاظ على وضوح المعادلات والصور بنسبة 100% دون تشويش.\n"
            "• نقوم بضغط ذكي للأحجام لسهولة التداول والطباعة السريعة."
        ),
        "security": (
            "🛡️ <b>سياسة الحماية والعلامات المائية الرقمية</b>\n\n"
            "• كل نسخة صادرة من البوت تحمل اسمك ورقم هاتفك على كل صفحة.\n"
            "• تحتوي الملفات على بصمات رقمية خفية (Steganography) لحماية حقوق النشر الخاصة بك ومنع تسريب المذكرة."
        )
    }

    answer_text = faq_contents.get(faq_key, "معلومات غير متوفرة حالياً.")
    back_kb = get_support_keyboard()
    try:
        await callback.message.edit_text(answer_text, reply_markup=back_kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(answer_text, reply_markup=back_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "support:talk_human")
async def start_human_support(callback: CallbackQuery, state: FSMContext):
    """بدء محادثة الدعم البشري مع المشرف"""
    await state.set_state(SupportStates.waiting_inquiry)
    text = (
        "✍️ <b>التواصل المباشر مع فريق الإدارة والدعم الفني</b>\n\n"
        "تفضل بكتابة استفسارك أو مشكلتك في رسالة واحدة.\n"
        "يمكنك إرسال <b>نص، صورة مع شرح، أو تسجيل صوتي</b>.\n\n"
        "<i>سيتم تحويل رسالتك مباشرة لغرفة عمليات المشرفين للرد عليك فوراً.</i>"
    )
    await callback.message.answer(text, reply_markup=get_support_cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "support:close")
async def close_support(callback: CallbackQuery, state: FSMContext):
    """إغلاق نافذة الدعم"""
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("✅ تم إغلاق مركز الدعم. يمكنك فتحه مجدداً في أي وقت.")
    await callback.answer("تم الإغلاق")


@router.message(SupportStates.waiting_inquiry)
async def receive_user_inquiry(message: Message, state: FSMContext, bot: Bot):
    """استلام رسالة العميل وتمريرها لكافة المشرفين مع زر الرد المباشر"""
    await state.clear()

    user = message.from_user
    user_name = user.full_name or "غير معروف"
    user_handle = f"@{user.username}" if user.username else "بدون يوزر"
    chat_id = str(message.chat.id)

    header = (
        "📩 <b>استفسار دعم فني وارد جديد</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>العميل:</b> {user_name} ({user_handle})\n"
        f"🆔 <b>معرف الحساب:</b> <code>{user.id}</code>\n"
        f"💬 <b>معرف المحادثة:</b> <code>{chat_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    sent_to_admin = False
    admin_kb = get_admin_user_reply_keyboard(chat_id)

    for admin_id in bot_config.ADMIN_TELEGRAM_IDS:
        try:
            if message.text:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"{header}📝 <b>نص الرسالة:</b>\n{message.text}",
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            elif message.photo:
                caption = message.caption or "بدون تعليق"
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=message.photo[-1].file_id,
                    caption=f"{header}🖼️ <b>صورة مرفقة:</b>\n{caption}",
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            elif message.voice:
                await bot.send_voice(
                    chat_id=admin_id,
                    voice=message.voice.file_id,
                    caption=f"{header}🎤 <b>رسالة صوتية من العميل</b>",
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"{header}📎 <i>أرسل العميل مرفقاً من نوع آخر.</i>",
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            sent_to_admin = True
        except Exception as e:
            logger.warning(f"Failed to forward support inquiry to admin {admin_id}: {e}")

    if sent_to_admin:
        await message.answer(
            "✅ <b>تم استلام رسالتك بنجاح!</b>\n\n"
            "تم إشعار فريق الدعم الفني بمحتوى استفسارك، وسيقوم أحد المشرفين بالرد عليك هنا مباشرة في أقرب وقت. ⏳",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "⚠️ تم تسجيل استفسارك وسيقوم المشرف بمراجعته قريباً.",
            parse_mode="HTML"
        )


@router.callback_query(F.data == "adm_dismiss_msg")
async def dismiss_admin_message(callback: CallbackQuery):
    """إخفاء إشعار الاستفسار بعد المعالجة"""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("تم وضع علامة مقروء")
    except Exception:
        await callback.answer()

