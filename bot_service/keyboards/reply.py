# ================================================================
# bot_service/keyboards/reply.py — لوحات المفاتيح السفلية الرئيسية
# ================================================================

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from bot_service.config import bot_config

def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """لوحة المفاتيح الثابتة للقائمة الرئيسية"""
    # زر نصي ديناميكي يرسل دائماً أحدث رابط حي بضغطة واحدة دون أن يتعطل أو يخزن رابطاً قديماً عند إعادة التشغيل
    miniapp_btn = KeyboardButton(text="🌐 فتح التطبيق التفاعلي (Mini App)")

    buttons = [
        [
            KeyboardButton(text="📚 تصفح المذكرات والطلب"),
            KeyboardButton(text="🛒 شراء عدة مذكرات معاً")
        ],
        [
            KeyboardButton(text="👤 بيانات الختم الخاصة بي"),
            KeyboardButton(text="📦 متابعة طلباتي")
        ],
        [
            KeyboardButton(text="💳 شحن الرصيد والدفع"),
            miniapp_btn
        ],
        [
            KeyboardButton(text="💬 الدعم الفني والمساعدة")
        ]
    ]

    if is_admin:
        buttons.append([KeyboardButton(text="🛡️ لوحة تحكم الإدارة")])

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True
    )


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """زر الإلغاء السريع أثناء إدخال البيانات"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ إلغاء والعودة للقائمة الرئيسية")]],
        resize_keyboard=True
    )


def get_skip_keyboard() -> ReplyKeyboardMarkup:
    """أزرار التخطي أو الإلغاء للحقول الاختيارية"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="تخطي ⏩")],
            [KeyboardButton(text="❌ إلغاء والعودة للقائمة الرئيسية")]
        ],
        resize_keyboard=True
    )

