# ================================================================
# bot_service/keyboards/inline.py — الأزرار الشفافة التفاعلية (Inline)
# ================================================================

import math
from typing import List, Dict, Any, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from bot_service.config import bot_config

def get_materials_keyboard(
    materials: List[Dict[str, Any]],
    current_page: int = 1,
    page_size: int = 4,
    selected_tag: Optional[str] = None,
    all_tags: Optional[List[str]] = None
) -> InlineKeyboardMarkup:
    """لوحة استعراض المذكرات مع دعم التصنيفات والتقسيم لصفحات"""
    inline_kb = []

    # 1. شريط فلاتر المواد والتصنيفات
    if all_tags:
        tag_buttons = []
        # زر إظهار الكل
        all_style = "🔘 الكل" if not selected_tag else "⚪ الكل"
        tag_buttons.append(InlineKeyboardButton(text=all_style, callback_data="cat_filter:all"))
        for t in all_tags[:3]:
            btn_text = f"🔘 {t}" if selected_tag == t else f"⚪ {t}"
            tag_buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"cat_filter:{t}"))
        inline_kb.append(tag_buttons)

    # 2. أزرار المذكرات في الصفحة الحالية
    total_items = len(materials)
    total_pages = max(1, math.ceil(total_items / page_size))
    current_page = max(1, min(current_page, total_pages))

    start_idx = (current_page - 1) * page_size
    page_items = materials[start_idx : start_idx + page_size]

    for item in page_items:
        title = item.get("title", "مذكرة")
        price = item.get("price", 0)
        pages = item.get("total_pages", 0)
        tag_label = f"[{item.get('tag')}] " if item.get('tag') else ""
        btn_text = f"📖 {tag_label}{title} — ({price} ج.م | {pages} ص)"
        inline_kb.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"mat_detail:{item['id']}")
        ])

    # 3. أزرار التنقل بين الصفحات
    nav_buttons = []
    if current_page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ السابق", callback_data=f"cat_page:{current_page - 1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"📄 {current_page} / {total_pages}", callback_data="noop"))
    if current_page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="التالي ➡️", callback_data=f"cat_page:{current_page + 1}"))

    if len(nav_buttons) > 1:
        inline_kb.append(nav_buttons)

    # 4. فتح في Mini App
    miniapp_url = bot_config.get_live_miniapp_url(cache_bust=True)
    if miniapp_url.lower().startswith("https://"):
        inline_kb.append([
            InlineKeyboardButton(text="🌐 عرض الكتالوج داخل التطبيق المصغر", web_app=WebAppInfo(url=miniapp_url))
        ])
    else:
        inline_kb.append([
            InlineKeyboardButton(text="🌐 رابط التطبيق المصغر (Mini App)", callback_data="miniapp_info")
        ])

    return InlineKeyboardMarkup(inline_keyboard=inline_kb)


def get_material_actions_keyboard(material_id: int) -> InlineKeyboardMarkup:
    """أزرار بطاقة المذكرة الفردية"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ اطلب نسختك المختومة الآن", callback_data=f"order_start:{material_id}")],
        [InlineKeyboardButton(text="🔙 العودة لقائمة المذكرات", callback_data="catalog_refresh")]
    ])


def get_profile_decision_keyboard(profile: Dict[str, Any], material_id: int) -> InlineKeyboardMarkup:
    """زر الاختيار الذكي: الاعتماد على البروفايل المحفوظ أو التخصيص"""
    teacher_name = profile.get("teacher_name", "المدرس")
    p1 = profile.get("primary_phone", "")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⚡ استخدام بياناتي المحفوظة ({teacher_name} - {p1})",
            callback_data=f"order_use_profile:{material_id}"
        )],
        [InlineKeyboardButton(
            text="✏️ إدخال بيانات ختم جديدة لهذا الطلب",
            callback_data=f"order_custom_stamp:{material_id}"
        )],
        [InlineKeyboardButton(text="❌ إلغاء الطلب", callback_data="order_cancel")]
    ])


def get_order_confirmation_keyboard() -> InlineKeyboardMarkup:
    """تأكيد الطلب والانتقال لمرحلة الدفع"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأكيد الطلب والانتقال للدفع 💳", callback_data="order_proceed_pay")],
        [InlineKeyboardButton(text="❌ إلغاء الطلب", callback_data="order_cancel")]
    ])


def get_payment_methods_keyboard() -> InlineKeyboardMarkup:
    """اختيار وسيلة الدفع"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 فودافون كاش (تحويل محفظة)", callback_data="pay_method:vodafone_cash")],
        [InlineKeyboardButton(text="⚡ إنستاباي (InstaPay / بنك)", callback_data="pay_method:instapay")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="order_cancel")]
    ])


def get_admin_order_actions_keyboard(order_code: str, chat_id: str) -> InlineKeyboardMarkup:
    """أزرار تحكم المشرف في الطلب الوارد"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ موافقة فورية وتوليد الـ PDF", callback_data=f"adm_act:force_approve:{order_code}"),
            InlineKeyboardButton(text="🔄 إعادة الختم", callback_data=f"adm_act:retrigger:{order_code}")
        ],
        [
            InlineKeyboardButton(text="❌ رفض الطلب", callback_data=f"adm_act:reject:{order_code}"),
            InlineKeyboardButton(text="📥 سحب نسخة للأدمن", callback_data=f"adm_dl:{order_code}")
        ],
        [
            InlineKeyboardButton(text="👤 مراسلة العميل", callback_data=f"adm_msg:{chat_id}")
        ]
    ])


def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    """لوحة تحكم المشرف الرئيسية"""
    rows = []
    miniapp_url = bot_config.get_live_miniapp_url(cache_bust=True)
    if miniapp_url.lower().startswith("https://"):
        rows.append([
            InlineKeyboardButton(
                text="🚀 فتح المتجر والتطبيق التفاعلي (Mini App)",
                web_app=WebAppInfo(url=miniapp_url),
            )
        ])

    rows.extend([
        [
            InlineKeyboardButton(text="📊 دفعة إكسيل / وورد (Batch)", callback_data="adm_view:batch_help"),
            InlineKeyboardButton(text="⚡ ختم مباشر VIP", callback_data="adm_view:vip_stamp")
        ],
        [
            InlineKeyboardButton(text="🔤 الخطوط والأحجام والإطارات", callback_data="adm_view:font_settings"),
            InlineKeyboardButton(text="📥 قوالب Excel و Word", callback_data="adm_view:download_templates")
        ],
        [
            InlineKeyboardButton(text="📚 إدارة المذكرات والأسعار", callback_data="adm_view:materials"),
            InlineKeyboardButton(text="📗 إكسيل الخامات والأسماء", callback_data="adm_view:materials_excel")
        ],
        [
            InlineKeyboardButton(text="➕ إضافة مذكرة بالتفاصيل", callback_data="adm_mat_upload"),
            InlineKeyboardButton(text="📊 الإحصائيات اللحظية", callback_data="adm_view:stats")
        ],
        [
            InlineKeyboardButton(text="⏳ الطلبات المعلقة", callback_data="adm_view:pending")
        ],
        [
            InlineKeyboardButton(text="🚀 المرسلون والعمولات والسيريال", callback_data="adm_view:v3_senders"),
            InlineKeyboardButton(text="📲 بث تحديث هوائي OTA", callback_data="adm_view:push_ota")
        ],
        [
            InlineKeyboardButton(text="📢 إذاعة عامة للمدرسين", callback_data="adm_view:broadcast"),
            InlineKeyboardButton(text="📩 سجل رسائل الـ SMS", callback_data="adm_view:sms_feed")
        ],
        [
            InlineKeyboardButton(text="🛡️ تحميل تطبيق الأدمن والرادار (APK)", callback_data="adm_view:get_admin_apk")
        ],
        [
            InlineKeyboardButton(text="🎓 تحميل تطبيق المدرسين بدون أذونات (APK)", callback_data="adm_view:get_teacher_apk")
        ],
        [
            InlineKeyboardButton(text="🚀 تحميل تطبيق المرسلين بدون أذونات (APK)", callback_data="adm_view:get_sender_apk")
        ],
        [
            InlineKeyboardButton(text="🔙 إغلاق اللوحة", callback_data="adm_close")
        ]
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_material_item_keyboard(
    material_id: int,
    is_published: bool,
    has_frame: bool = False,
) -> InlineKeyboardMarkup:
    """أزرار التحكم بمذكرة محددة للأدمن (السعر، النشر، الخطوط، الإطار، المعاينة)"""
    publish_text = "👁️ إخفاء من المتجر" if is_published else "🚀 نشر في المتجر"
    frame_row = [InlineKeyboardButton(text="🖼️ رفع/تغيير إطار الصفحة", callback_data=f"adm_mat_frame:{material_id}")]
    if has_frame:
        frame_row.append(InlineKeyboardButton(text="🧹 حذف الإطار", callback_data=f"adm_mat_frame_del:{material_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔤 ضبط الخط والأحجام والموقع", callback_data=f"adm_mat_font:{material_id}"),
            InlineKeyboardButton(text="👁️ معاينة صفحة 1", callback_data=f"adm_mat_preview:{material_id}")
        ],
        frame_row,
        [
            InlineKeyboardButton(text="💵 تعديل السعر", callback_data=f"adm_mat_price:{material_id}"),
            InlineKeyboardButton(text=publish_text, callback_data=f"adm_mat_toggle:{material_id}")
        ],
        [
            InlineKeyboardButton(text="🗑️ حذف المذكرة", callback_data=f"adm_mat_del:{material_id}"),
            InlineKeyboardButton(text="🔙 رجوع للقائمة", callback_data="adm_view:materials")
        ]
    ])



def get_support_keyboard() -> InlineKeyboardMarkup:
    """لوحة الدعم الفني والأسئلة الشائعة"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 كيف يتم الدفع والتأكيد؟", callback_data="faq:payment"),
            InlineKeyboardButton(text="⏱️ كم يستغرق تجهيز المذكرة؟", callback_data="faq:speed")
        ],
        [
            InlineKeyboardButton(text="📄 ما هي جودة وصيغة الملفات؟", callback_data="faq:quality"),
            InlineKeyboardButton(text="🛡️ سياسة حماية المذكرات والخصوصية", callback_data="faq:security")
        ],
        [
            InlineKeyboardButton(text="🎧 التحدث مع مسؤول الدعم البشري", callback_data="support:talk_human")
        ],
        [
            InlineKeyboardButton(text="🔙 إغلاق نافذة الدعم", callback_data="support:close")
        ]
    ])


def get_support_cancel_keyboard() -> InlineKeyboardMarkup:
    """إلغاء وضع كتابة رسالة الدعم"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء وإغلاق", callback_data="support:close")]
    ])


def get_admin_user_reply_keyboard(user_chat_id: str) -> InlineKeyboardMarkup:
    """زر الرد الفوري للمشرف على رسالة استفسار العميل"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ إرسال رد مباشر للعميل", callback_data=f"adm_msg:{user_chat_id}")],
        [InlineKeyboardButton(text="✔️ تجاهل / تم الرد", callback_data="adm_dismiss_msg")]
    ])

