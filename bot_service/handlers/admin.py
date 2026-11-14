# ================================================================
# bot_service/handlers/admin.py — غرفة التحكم وإدارة النظام للمشرفين
# ================================================================

import html
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    BufferedInputFile,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.fsm.context import FSMContext

from bot_service.config import bot_config
from bot_service.services.bridge_client import bridge_client
from bot_service.services.batch_parser import filter_usable_materials
from bot_service.keyboards.inline import (
    get_admin_main_keyboard,
    get_admin_order_actions_keyboard,
    get_admin_material_item_keyboard
)
from bot_service.states.order_flow import (
    AdminVIPStates, SupportStates, AdminSettingsStates,
    AdminMaterialManageStates, AdminBroadcastStates
)

logger = logging.getLogger("bot_service.handlers.admin")
router = Router(name="admin_router")


def is_admin_user(user_id: int) -> bool:
    return bot_config.is_admin(user_id)


# ================================================================
# 1. لوحة التحكم الرئيسية (Admin Dashboard)
# ================================================================

@router.message(F.text == "🛡️ لوحة تحكم الإدارة")
@router.message(Command("admin"))
async def handle_admin_dashboard(message: Message):
    if not is_admin_user(message.from_user.id):
        await message.answer("⛔ هذا القسم مخصص لإدارة المنصة فقط.")
        return

    stats = await bridge_client.get_admin_stats()
    if not stats:
        await message.answer("⚠️ تعذر جلب الإحصائيات من السيرفر، تأكد من تشغيل المنصة الأساسية.")
        return

    total_orders = stats.get("total_orders", 0)
    pending_orders = stats.get("pending_orders", 0)
    completed_orders = stats.get("completed_orders", 0)
    total_materials = stats.get("total_materials", 0)
    total_teachers = stats.get("total_teachers", 0)
    total_sms = stats.get("total_sms_logs", 0)
    unreconciled_sms = stats.get("unreconciled_sms_logs", 0)

    live_miniapp = bot_config.get_live_miniapp_url(cache_bust=False)
    dashboard_text = (
        f"👑 **لوحة قيادة إدارة منصة Z-PACT** 🛡️\n\n"
        f"📊 **مؤشرات الأداء اللحظية:**\n"
        f"  • إجمالي الطلبات: `{total_orders}` طلب\n"
        f"  • طلبات بانتظار التأكيد: `🟡 {pending_orders}`\n"
        f"  • طلبات مكتملة ومسلمة: `🟢 {completed_orders}`\n\n"
        f"👨‍🏫 **المعلمون والمحتوى:**\n"
        f"  • عدد المدرسين المسجلين: `{total_teachers}` مدرس\n"
        f"  • المذكرات المنشورة بالمتجر: `{total_materials}` مذكرة\n\n"
        f"📱 **محرك الـ SMS Webhook:**\n"
        f"  • إجمالي رسائل التحويل: `{total_sms}`\n"
        f"  • تحويلات غير مطابقة: `⚠️ {unreconciled_sms}`\n\n"
        f"💳 **بيانات الاستقبال والروابط:**\n"
        f"  • رقم المحفظة: `{bot_config.PAYMENT_WALLET_NUMBER}`\n"
        f"  • عنوان إنستاباي: `{bot_config.INSTAPAY_ADDRESS}`\n"
        f"  • رابط الميني آب: `{live_miniapp}`\n"
        f"  *(للأوامر: `/set_wallet` | `/set_instapay` | `/set_miniapp` | `/get_apk`)*\n\n"
        f"اختر القسم المطلوب من الأزرار التفاعلية أدناه 👇"
    )

    await message.answer(
        text=dashboard_text,
        reply_markup=get_admin_main_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "adm_view:stats")
async def handle_admin_stats_callback(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    stats = await bridge_client.get_admin_stats()
    if not stats:
        await callback.answer("خطأ في الاتصال بالسيرفر", show_alert=True)
        return

    total_orders = stats.get("total_orders", 0)
    pending_orders = stats.get("pending_orders", 0)
    completed_orders = stats.get("completed_orders", 0)
    total_teachers = stats.get("total_teachers", 0)
    total_sms = stats.get("total_sms_logs", 0)
    unreconciled_sms = stats.get("unreconciled_sms_logs", 0)

    text = (
        f"📊 **تحديث الإحصائيات اللحظي:**\n\n"
        f"• إجمالي المبيعات والطلبات: `{total_orders}`\n"
        f"• طلبات قيد المراجعة: `{pending_orders}`\n"
        f"• تم تسليمها بنجاح: `{completed_orders}`\n"
        f"• المدرسون النشطون: `{total_teachers}`\n"
        f"• رسائل SMS المعالجة: `{total_sms}` (غير مطابقة: `{unreconciled_sms}`)\n\n"
        f"⏱️ تم التحديث الآن بنجاح."
    )

    try:
        await callback.message.edit_text(text, reply_markup=get_admin_main_keyboard(), parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer("تم التحديث ✅")


@router.callback_query(F.data == "adm_close")
async def handle_admin_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# ================================================================
# 2. مراجعة الطلبات المعلقة وتأكيد الإيصالات
# ================================================================

@router.callback_query(F.data == "adm_view:pending")
async def handle_admin_pending_orders(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    stats = await bridge_client.get_admin_stats()
    orders = stats.get("recent_orders", []) if stats else []
    pending_orders = [o for o in orders if o.get("status") == "pending_payment"]

    if not pending_orders:
        await callback.message.answer("🎉 **رائع! لا توجد أي طلبات معلقة حالياً.** جميع العمليات مكتملة.")
        await callback.answer()
        return

    await callback.message.answer(f"⏳ **يوجد ({len(pending_orders)}) طلبات بانتظار المراجعة:**")

    for o in pending_orders[:5]:
        code = o.get("order_code")
        amt = o.get("total_amount")
        name = o.get("buyer_name_stamp")
        p1 = o.get("buyer_phone_stamp")
        claimed = o.get("claimed_sender_phone") or "غير محدد"
        receipt_file_id = o.get("receipt_file_id")
        chat_id = o.get("telegram_chat_id")

        caption = (
            f"🔖 <b>طلب معلق:</b> <code>{html.escape(str(code))}</code>\n"
            f"💵 <b>المبلغ:</b> <code>{html.escape(str(amt))}</code> ج.م\n"
            f"👤 <b>المدرس:</b> {html.escape(str(name or ''))}\n"
            f"📱 <b>رقم الختم:</b> <code>{html.escape(str(p1 or ''))}</code>\n"
            f"📞 <b>الرقم المحول منه:</b> <code>{html.escape(str(claimed))}</code>\n"
            f"🆔 <b>معرف المحادثة:</b> <code>{html.escape(str(chat_id or ''))}</code>"
        )
        kb = get_admin_order_actions_keyboard(code, chat_id)

        if receipt_file_id:
            try:
                await callback.message.answer_photo(
                    photo=receipt_file_id,
                    caption=caption,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                continue
            except Exception:
                pass

        await callback.message.answer(text=caption, reply_markup=kb, parse_mode="HTML")

    await callback.answer()


# ================================================================
# 3. معالجة نقرات أزرار الإدارة (موافقة / إعادة ختم / رفض)
# ================================================================

@router.callback_query(F.data.startswith("adm_act:"))
async def handle_admin_action_click(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    parts = callback.data.split(":")
    action = parts[1]
    order_code = parts[2]

    res = await bridge_client.admin_order_action(order_code, action=action)
    if not res:
        await callback.answer("⚠️ فشل تنفيذ الإجراء على السيرفر", show_alert=True)
        return

    if action == "force_approve":
        msg = f"✅ **تمت الموافقة الفورية على الطلب `{order_code}`!**\nجاري تشغيل الختم وإرسال الـ PDF للعميل فوراً."
        await callback.message.reply(msg, parse_mode="Markdown")
        await callback.answer("تمت الموافقة وجاري الختم ✅")

    elif action == "retrigger":
        msg = f"🔄 **تمت إعادة إطلاق مهمة الختم للطلب `{order_code}`!**"
        await callback.message.reply(msg, parse_mode="Markdown")
        await callback.answer("جاري إعادة الختم 🔄")

    elif action == "reject":
        msg = f"❌ **تم رفض الطلب `{order_code}` وإبلاغ العميل.**"
        await callback.message.reply(msg, parse_mode="Markdown")
        await callback.answer("تم الرفض ❌")


@router.callback_query(F.data.startswith("adm_msg:"))
async def handle_admin_direct_message(callback: CallbackQuery, state: FSMContext):
    """بدء محادثة مباشرة مع العميل"""
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    target_chat_id = callback.data.split(":")[1]
    await state.set_state(SupportStates.admin_replying)
    await state.update_data(target_chat_id=target_chat_id)

    await callback.message.answer(
        f"✍️ **أدخل الآن نص الرسالة التي ترغب بإرسالها للعميل (`{target_chat_id}`):**\n"
        f"*(يمكنك إرسال نص أو رسالة صوتية أو صورة)*"
    )
    await callback.answer()


@router.message(SupportStates.admin_replying)
async def process_admin_reply_to_user(message: Message, state: FSMContext, bot: Bot):
    """إرسال رد المشرف مباشرة إلى محادثة العميل"""
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    target_chat_id = data.get("target_chat_id")
    await state.clear()

    if not target_chat_id:
        await message.answer("⚠️ لم يتم تحديد معرف العميل المستهدف.")
        return

    admin_header = (
        "💬 <b>رسالة واردة من إدارة المنصة والدعم الفني:</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    try:
        if message.text:
            await bot.send_message(
                chat_id=target_chat_id,
                text=f"{admin_header}{message.text}\n━━━━━━━━━━━━━━━━━━\n<i>يمكنك الرد في أي وقت عبر زر الدعم الفني.</i>",
                parse_mode="HTML"
            )
        elif message.photo:
            caption = message.caption or ""
            await bot.send_photo(
                chat_id=target_chat_id,
                photo=message.photo[-1].file_id,
                caption=f"{admin_header}{caption}",
                parse_mode="HTML"
            )
        elif message.voice:
            await bot.send_voice(
                chat_id=target_chat_id,
                voice=message.voice.file_id,
                caption=f"{admin_header}🎤 <i>رسالة صوتية من الإدارة</i>",
                parse_mode="HTML"
            )
        else:
            await message.answer("⚠️ نوع المرفق غير مدعوم للإرسال للعميل.")
            return

        await message.answer(f"✅ <b>تم تسليم الرسالة بنجاح إلى العميل ({target_chat_id}).</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send admin reply to {target_chat_id}: {e}")
        await message.answer(f"❌ تعذر إرسال الرسالة للعميل: {e}")



# ================================================================
# 4. سجل رسائل الـ SMS اللحظي (Live SMS Webhook Feed)
# ================================================================

@router.callback_query(F.data == "adm_view:sms_feed")
async def handle_admin_sms_feed(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    stats = await bridge_client.get_admin_stats()
    unmatched_sms = stats.get("recent_unmatched_sms", []) if stats else []

    if not unmatched_sms:
        await callback.message.answer("✅ **ممتاز! جميع رسائل الـ SMS الواردة تم مطابقتها واستهلاكها بالكامل.**")
        await callback.answer()
        return

    text = f"📩 **رسائل الـ SMS المعلقة غير المطابقة ({len(unmatched_sms)}):**\n\n"
    for s in unmatched_sms[:6]:
        sid = s.get("id")
        provider = s.get("source_provider")
        amt = s.get("extracted_amount")
        sender = s.get("extracted_sender") or "مجهول"
        ref = s.get("reference_number") or "بدون مرجع"

        text += (
            f"🆔 **معرف الرسالة:** `{sid}`\n"
            f"🏦 **الخدمة:** {provider}\n"
            f"💵 **المبلغ:** `{amt}` ج.م\n"
            f"📱 **من:** `{sender}`\n"
            f"🔖 **المرجع:** `{ref}`\n"
            f"────────────────────\n"
        )

    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


# ================================================================
# 5. ختم فوري للمشرفين (Direct Admin VIP Generator)
# ================================================================

@router.callback_query(F.data == "adm_view:vip_stamp")
async def handle_admin_vip_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    materials = await bridge_client.get_materials(include_unpublished=True)
    if not materials:
        await callback.message.answer("لا توجد مذكرات بالكتالوج.")
        await callback.answer()
        return

    buttons = []
    usable_mats = filter_usable_materials(materials)
    text = "⚡ <b>أداة الختم الفوري للمشرفين (VIP Direct Generator)</b>\n\nاضغط على المذكرة أو أرسل رقمها (ID):\n\n"
    for m in usable_mats[:40]:
        text += f"• <code>{m['id']}</code> — {html.escape(m.get('title', ''))} ({m.get('total_pages', 0)} ص)\n"
        buttons.append([InlineKeyboardButton(
            text=f"📖 {m.get('title', '')} (#{m['id']})",
            callback_data=f"vip_pick_mat:{m['id']}"
        )])

    buttons.append([InlineKeyboardButton(text="❌ إلغاء", callback_data="vip_cancel")])
    await state.set_state(AdminVIPStates.waiting_material_id)
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "vip_cancel")
async def handle_vip_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ تم إلغاء عملية الختم الفوري.")
    await callback.answer()


@router.callback_query(F.data.startswith("vip_pick_mat:"))
async def handle_vip_pick_mat(callback: CallbackQuery, state: FSMContext):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return
    mat_id = int(callback.data.split(":")[1])
    await state.update_data(vip_material_id=mat_id)
    await state.set_state(AdminVIPStates.waiting_teacher_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي الاسم (اختياري للأدمن)", callback_data="vip_skip:teacher_name")]])
    await callback.message.answer(f"📖 تم اختيار المذكرة <code>#{mat_id}</code>.\n\n✍️ <b>أدخل اسم المدرس المراد طباعته (اختياري للأدمن):</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminVIPStates.waiting_material_id)
async def process_vip_mat_id(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.isdigit():
        mat_id = int(raw)
    else:
        from bot_service.services.batch_parser import match_material_by_ref
        materials = await bridge_client.get_materials(include_unpublished=True)
        mat, _ = match_material_by_ref(raw, materials)
        if not mat:
            await message.answer("⚠️ لم يتم العثور على مذكرة مطابقة؛ أدخل رقم المعرف أو جزءاً من اسم المذكرة أو اضغط على زرها بالأعلى.")
            return
        mat_id = int(mat["id"])

    await state.update_data(vip_material_id=mat_id)
    await state.set_state(AdminVIPStates.waiting_teacher_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي الاسم (اختياري للأدمن)", callback_data="vip_skip:teacher_name")]])
    await message.answer("✍️ <b>أدخل اسم المدرس المراد طباعته (اختياري للأدمن):</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "vip_skip:teacher_name", AdminVIPStates.waiting_teacher_name)
async def skip_vip_teacher_name(callback: CallbackQuery, state: FSMContext):
    await state.update_data(vip_teacher_name="")
    await state.set_state(AdminVIPStates.waiting_phone)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي الهاتف (اختياري للأدمن)", callback_data="vip_skip:phone")]])
    await callback.message.answer("📱 <b>أدخل رقم هاتف المدرس الأساسي (اختياري للأدمن):</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminVIPStates.waiting_teacher_name)
async def process_vip_teacher_name(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    name = "" if raw in ("تخطي", "تخطي ⏩", "-") else raw
    await state.update_data(vip_teacher_name=name)
    await state.set_state(AdminVIPStates.waiting_phone)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي الهاتف (اختياري للأدمن)", callback_data="vip_skip:phone")]])
    await message.answer("📱 <b>أدخل رقم هاتف المدرس الأساسي (اختياري للأدمن):</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "vip_skip:phone", AdminVIPStates.waiting_phone)
async def skip_vip_phone(callback: CallbackQuery, state: FSMContext):
    await state.update_data(vip_phone="")
    await state.set_state(AdminVIPStates.waiting_phone_2)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي الهاتف الإضافي", callback_data="vip_skip:phone_2")]])
    await callback.message.answer("📱 <b>أدخل رقم هاتف إضافي (اختياري):</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminVIPStates.waiting_phone)
async def process_vip_phone(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    phone = "" if raw in ("تخطي", "تخطي ⏩", "-") else raw
    await state.update_data(vip_phone=phone)
    await state.set_state(AdminVIPStates.waiting_phone_2)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي الهاتف الإضافي", callback_data="vip_skip:phone_2")]])
    await message.answer("📱 <b>أدخل رقم هاتف إضافي (اختياري):</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "vip_skip:phone_2", AdminVIPStates.waiting_phone_2)
async def skip_vip_phone_2(callback: CallbackQuery, state: FSMContext):
    await state.update_data(vip_phone_2=None)
    await state.set_state(AdminVIPStates.waiting_series)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي اسم السلسلة", callback_data="vip_skip:series")]])
    await callback.message.answer("📚 <b>أدخل اسم السلسلة (اختياري):</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminVIPStates.waiting_phone_2)
async def process_vip_phone_2(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    phone_2 = None if raw in ("تخطي", "تخطي ⏩", "-", "بدون", "") else raw
    await state.update_data(vip_phone_2=phone_2)
    await state.set_state(AdminVIPStates.waiting_series)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ تخطي اسم السلسلة", callback_data="vip_skip:series")]])
    await message.answer("📚 <b>أدخل اسم السلسلة (اختياري):</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "vip_skip:series", AdminVIPStates.waiting_series)
async def skip_vip_series(callback: CallbackQuery, state: FSMContext):
    await state.update_data(vip_series=None)
    await state.set_state(AdminVIPStates.waiting_watermark)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ بدون علامة مائية", callback_data="vip_skip:watermark")]])
    await callback.message.answer("🛡️ <b>أدخل نص العلامة المائية (اختياري):</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminVIPStates.waiting_series)
async def process_vip_series(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    series = None if raw in ("تخطي", "تخطي ⏩", "-", "بدون", "") else raw
    await state.update_data(vip_series=series)
    await state.set_state(AdminVIPStates.waiting_watermark)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ بدون علامة مائية", callback_data="vip_skip:watermark")]])
    await message.answer("🛡️ <b>أدخل نص العلامة المائية (اختياري):</b>", reply_markup=kb, parse_mode="HTML")


async def _execute_vip_direct_stamp(target_msg: Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    await state.clear()

    mat_id = data.get("vip_material_id")
    name = (data.get("vip_teacher_name") or "").strip()
    phone = (data.get("vip_phone") or "").strip()
    phone_2 = data.get("vip_phone_2")
    series = data.get("vip_series")
    wm_text = data.get("vip_watermark")

    status_msg = await target_msg.answer("⏳ جاري الختم المتجه الخفيف وتجهيز النسخة لك...")
    res = await bridge_client.direct_stamp_sync({
        "material_id": mat_id,
        "buyer_name_stamp": name,
        "buyer_phone_stamp": phone,
        "buyer_phone_stamp_2": phone_2,
        "series_name": series,
        "doc_name_stamp": series,
        "watermark_text": wm_text,
        "watermark_type": "text" if wm_text else "none",
        "telegram_chat_id": str(target_msg.chat.id),
        "telegram_user_id": str(user_id),
        "is_admin": True,
    })
    if not res.get("ok"):
        await status_msg.edit_text(f"⚠️ فشل الختم: {html.escape(str(res.get('error', '')))}", parse_mode="HTML")
        return

    info = res["data"]
    order_code = info.get("order_code")
    pdf_bytes = await bridge_client.download_order_pdf(order_code)
    if not pdf_bytes:
        await status_msg.edit_text("⚠️ تم الختم ولكن تعذر تنزيل الملف من السيرفر.")
        return

    disp_name = name or (series or "") or phone or "بدون اسم"
    disp_phone = phone or "بدون رقم"
    safe_tname = "".join(ch if ch.isalnum() or ch in (" ", "_", "-") else "_" for ch in str(name or series or "vip"))[:32].strip() or "vip"
    fname = f"ZPACT_{safe_tname}_{order_code}.pdf"
    await target_msg.answer_document(
        document=BufferedInputFile(pdf_bytes, filename=fname),
        caption=(
            f"✅ <b>نسخة VIP جاهزة!</b>\n"
            f"📖 {html.escape(str(info.get('material_title', '')))}\n"
            f"👤 {html.escape(disp_name)} | 📱 <code>{html.escape(disp_phone)}</code>\n"
            f"🔖 <code>{html.escape(str(order_code))}</code>"
        ),
        parse_mode="HTML",
    )
    try:
        await status_msg.delete()
    except Exception:
        pass


@router.callback_query(F.data == "vip_skip:watermark", AdminVIPStates.waiting_watermark)
async def skip_vip_watermark(callback: CallbackQuery, state: FSMContext):
    await state.update_data(vip_watermark=None)
    await callback.answer("جاري التجهيز...")
    await _execute_vip_direct_stamp(callback.message, state, callback.from_user.id)


@router.message(AdminVIPStates.waiting_watermark)
async def process_vip_watermark(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    wm = None if raw in ("تخطي", "تخطي ⏩", "-", "بدون", "") else raw
    await state.update_data(vip_watermark=wm)
    await _execute_vip_direct_stamp(message, state, message.from_user.id)


# ================================================================
# 6. تعديل أرقام وعناوين الاستقبال المالي ديناميكياً
# ================================================================

@router.message(Command("set_wallet"))
async def cmd_set_wallet(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        return

    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip():
        new_wallet = args[1].strip()
        bot_config.update_wallet(new_wallet)
        await message.answer(
            f"✅ <b>تم تحديث رقم المحفظة بنجاح!</b>\n\n"
            f"📱 الرقم الجديد: <code>{html.escape(bot_config.PAYMENT_WALLET_NUMBER)}</code>\n"
            f"🌐 العرض الدولي: <code>{html.escape(bot_config.PAYMENT_WALLET_DISPLAY)}</code>\n\n"
            f"تم حفظ القيمة في الإعدادات وملف التكوين وستظهر فوراً لجميع المستخدمين.",
            parse_mode="HTML"
        )
    else:
        await state.set_state(AdminSettingsStates.waiting_new_wallet)
        await message.answer(
            f"📱 <b>تحديث رقم محفظة فودافون كاش:</b>\n\n"
            f"الرقم الحالي: <code>{html.escape(bot_config.PAYMENT_WALLET_NUMBER)}</code>\n\n"
            f"👉 <b>أرسل الآن الرقم الجديد للمحفظة:</b>",
            parse_mode="HTML"
        )


@router.message(AdminSettingsStates.waiting_new_wallet)
async def process_new_wallet_input(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return

    new_wallet = message.text.strip()
    digits = "".join(filter(str.isdigit, new_wallet))
    if len(digits) < 10:
        await message.answer("⚠️ يرجى إدخال رقم هاتف صحيح (11 رقم مثلاً 010xxxxxxxx).")
        return

    bot_config.update_wallet(new_wallet)
    await state.clear()
    await message.answer(
        f"✅ <b>تم حفظ وتفعيل رقم المحفظة الجديد!</b>\n\n"
        f"📱 الرقم: <code>{html.escape(bot_config.PAYMENT_WALLET_NUMBER)}</code>\n"
        f"🌐 العرض: <code>{html.escape(bot_config.PAYMENT_WALLET_DISPLAY)}</code>",
        parse_mode="HTML"
    )


@router.message(Command("set_instapay"))
async def cmd_set_instapay(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        return

    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip():
        new_address = args[1].strip()
        bot_config.update_instapay(new_address)
        await message.answer(
            f"✅ <b>تم تحديث عنوان إنستاباي بنجاح!</b>\n\n"
            f"🏦 العنوان الجديد: <code>{html.escape(bot_config.INSTAPAY_ADDRESS)}</code>\n\n"
            f"تم حفظ القيمة وستظهر فوراً لجميع العملاء.",
            parse_mode="HTML"
        )
    else:
        await state.set_state(AdminSettingsStates.waiting_new_instapay)
        await message.answer(
            f"🏦 <b>تحديث عنوان إنستاباي (IPA):</b>\n\n"
            f"العنوان الحالي: <code>{html.escape(bot_config.INSTAPAY_ADDRESS)}</code>\n\n"
            f"👉 <b>أرسل الآن العنوان الجديد (مثال: username@instapay أو رقم الحساب):</b>",
            parse_mode="HTML"
        )


@router.message(AdminSettingsStates.waiting_new_instapay)
async def process_new_instapay_input(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return

    new_address = message.text.strip()
    if len(new_address) < 3:
        await message.answer("⚠️ يرجى إدخال عنوان صحيح.")
        return

    bot_config.update_instapay(new_address)
    await state.clear()
    await message.answer(
        f"✅ <b>تم حفظ وتفعيل عنوان إنستاباي الجديد!</b>\n\n"
        f"🏦 العنوان: <code>{html.escape(bot_config.INSTAPAY_ADDRESS)}</code>",
        parse_mode="HTML"
    )


@router.message(Command("set_miniapp"))
async def cmd_set_miniapp(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        return

    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip():
        new_url = args[1].strip()
        bot_config.update_miniapp_url(new_url)
        is_https = bot_config.MINIAPP_URL.lower().startswith("https://")
        reply_markup = None
        if is_https:
            busted = bot_config.get_live_miniapp_url(cache_bust=True)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 تجربة فتح التطبيق المصغر", web_app=WebAppInfo(url=busted))
            ]])
            try:
                from aiogram.types import MenuButtonWebApp
                mb = MenuButtonWebApp(text="📱 المتجر والتطبيق الذكي", web_app=WebAppInfo(url=busted))
                await message.bot.set_chat_menu_button(menu_button=mb)
                await message.bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=mb)
            except Exception:
                pass
        await message.answer(
            f"✅ <b>تم تحديث رابط التطبيق المصغر (Mini App) بنجاح!</b>\n\n"
            f"🌐 الرابط الجديد: <code>{html.escape(bot_config.MINIAPP_URL)}</code>\n\n"
            f"{'🔒 الرابط مشفر بـ HTTPS وسيعمل كزر ميني آب منبثق لكافة المستخدمين! ✨' if is_https else '⚠️ تنبيه: الرابط غير مشفر (HTTP)؛ تليجرام يتطلب HTTPS لظهور أيقونة الميني آب.'}",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        await state.set_state(AdminSettingsStates.waiting_new_miniapp)
        await message.answer(
            f"🌐 <b>تحديث رابط التطبيق المصغر (Telegram Mini App):</b>\n\n"
            f"الرابط الحالي: <code>{html.escape(bot_config.MINIAPP_URL)}</code>\n\n"
            f"👉 <b>أرسل الآن الرابط الجديد المباشر (مثل https://xxxx.trycloudflare.com/miniapp/):</b>",
            parse_mode="HTML"
        )


@router.message(AdminSettingsStates.waiting_new_miniapp)
async def process_new_miniapp_input(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return

    new_url = message.text.strip()
    if not new_url.startswith("http://") and not new_url.startswith("https://"):
        new_url = "https://" + new_url

    bot_config.update_miniapp_url(new_url)
    await state.clear()
    is_https = bot_config.MINIAPP_URL.lower().startswith("https://")
    reply_markup = None
    if is_https:
        busted = bot_config.get_live_miniapp_url(cache_bust=True)
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 تجربة فتح التطبيق المصغر", web_app=WebAppInfo(url=busted))
        ]])
        try:
            from aiogram.types import MenuButtonWebApp
            mb = MenuButtonWebApp(text="📱 المتجر والتطبيق الذكي", web_app=WebAppInfo(url=busted))
            await message.bot.set_chat_menu_button(menu_button=mb)
            await message.bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=mb)
        except Exception:
            pass

    await message.answer(
        f"✅ <b>تم حفظ وتفعيل رابط التطبيق المصغر بنجاح!</b>\n\n"
        f"🌐 الرابط: <code>{html.escape(bot_config.MINIAPP_URL)}</code>\n\n"
        f"{'🔒 الرابط مشفر بـ HTTPS وأصبح زر الميني آب مفعل فوراً لجميع الأعضاء!' if is_https else '⚠️ تنبيه: يفضل استخدام رابط HTTPS.'}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


# ================================================================
# 7. سحب نسخة الأدمن من المستند المختوم
# ================================================================

@router.callback_query(F.data.startswith("adm_dl:"))
async def handle_admin_order_download(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    order_code = callback.data.split(":")[1]
    await callback.answer("⏳ جاري سحب نسخة الأدمن...")

    pdf_bytes = await bridge_client.download_order_pdf(order_code)
    if not pdf_bytes:
        await callback.message.reply("⚠️ تعذر استرجاع ملف المذكرة المختوم من السيرفر.")
        return

    input_file = BufferedInputFile(pdf_bytes, filename=f"ADMIN_COPY_{order_code}.pdf")
    await callback.message.reply_document(
        document=input_file,
        caption=f"👑 <b>نسخة الإدارة للمذكرة المختومة للطلب:</b> <code>{order_code}</code>",
        parse_mode="HTML"
    )


# ================================================================
# 8. إدارة المذكرات والأسعار ورفع ملفات جديدة للأدمن
# ================================================================

@router.callback_query(F.data == "adm_view:materials")
async def handle_admin_view_materials(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    materials = await bridge_client.get_materials(include_unpublished=True)
    if not materials:
        await callback.message.answer("📭 لا توجد مذكرات مسجلة في المتجر حالياً.")
        await callback.answer()
        return

    usable_mats = filter_usable_materials(materials)
    text = f"📚 <b>قائمة المذكرات والأسعار الحالية ({len(usable_mats)} مذكرة جاهزة):</b>\nاضغط على أي مذكرة للتحكم بالسعر والخطوط والإطار:\n"
    buttons = [[InlineKeyboardButton(text="📗 تنزيل ملف Excel بالأسماء والخامات", callback_data="adm_view:materials_excel")]]
    for m in usable_mats[:45]:
        pub_icon = "🟢" if m.get("is_published", True) else "🔴"
        fr_icon = " 🖼️" if m.get("has_frame") else ""
        title = m.get("title", "")
        price = m.get("price", 0)
        btn_text = f"{pub_icon}{fr_icon} {title} (#{m['id']}) — ({price} ج.م)"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"adm_mat_sel:{m['id']}")])

    buttons.append([InlineKeyboardButton(text="➕ رفع مذكرة جديدة بالتفاصيل", callback_data="adm_mat_upload")])
    buttons.append([InlineKeyboardButton(text="🔙 العودة للوحة التحكم", callback_data="adm_view:stats")])

    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_mat_sel:"))
async def handle_admin_material_detail(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    mat_id = int(callback.data.split(":")[1])
    materials = await bridge_client.get_materials(include_unpublished=True)
    mat = next((m for m in materials if m["id"] == mat_id), None)
    if not mat:
        await callback.answer("المذكرة غير موجودة", show_alert=True)
        return

    is_pub = mat.get("is_published", True)
    has_frame = bool(mat.get("has_frame"))
    status_str = "🟢 منشورة وتظهر في المتجر" if is_pub else "🔴 مخفية عن الطلاب والمدرسين"
    frame_str = "🖼️ إطار صفحات مدمج (شفاف)" if has_frame else "بدون إطار مخصص"

    text = (
        f"📖 <b>تفاصيل المذكرة:</b> {html.escape(mat.get('title', ''))} (<code>#{mat_id}</code>)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>السعر الحالي:</b> <code>{mat.get('price', 0)}</code> ج.م\n"
        f"📄 <b>عدد الصفحات:</b> {mat.get('total_pages', 0)} صفحة\n"
        f"🏷️ <b>التصنيف:</b> {html.escape(mat.get('tag') or 'عام')}\n"
        f"🖼️ <b>الإطار:</b> {frame_str}\n"
        f"📊 <b>حالة العرض:</b> {status_str}\n\n"
        f"اختر الإجراء المطلوب من الأزرار أدناه:"
    )
    kb = get_admin_material_item_keyboard(mat_id, is_pub, has_frame=has_frame)
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_mat_toggle:"))
async def handle_admin_material_toggle(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    mat_id = int(callback.data.split(":")[1])
    res = await bridge_client.toggle_material_publish(mat_id)
    if res:
        status_str = res.get("status", "")
        await callback.answer(f"تم تغيير حالة المذكرة إلى: {status_str} ✅", show_alert=True)
        await handle_admin_material_detail(callback)
    else:
        await callback.answer("⚠️ فشل تغيير الحالة على السيرفر", show_alert=True)


@router.callback_query(F.data.startswith("adm_mat_price:"))
async def handle_admin_material_price_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    mat_id = int(callback.data.split(":")[1])
    await state.set_state(AdminMaterialManageStates.waiting_new_price)
    await state.update_data(target_material_id=mat_id)

    await callback.message.answer(
        f"💵 <b>تعديل سعر المذكرة (معرف {mat_id}):</b>\n\n"
        f"👉 <b>أرسل الآن السعر الجديد بالأرقام فقط (مثال: 120):</b>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminMaterialManageStates.waiting_new_price)
async def process_admin_material_new_price(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return

    text = message.text.strip()
    try:
        new_price = float(text)
    except ValueError:
        await message.answer("⚠️ يرجى إدخال رقم صحيح (مثال: 90 أو 150.5).")
        return

    data = await state.get_data()
    mat_id = data.get("target_material_id")
    await state.clear()

    res = await bridge_client.update_material(mat_id, {"price": new_price})
    if res:
        await message.answer(f"✅ <b>تم تحديث سعر المذكرة بنجاح إلى {new_price} ج.م!</b>", parse_mode="HTML")
    else:
        await message.answer("⚠️ حدث خطأ أثناء تحديث السعر.")


@router.callback_query(F.data.startswith("adm_mat_del:"))
async def handle_admin_material_delete(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    mat_id = int(callback.data.split(":")[1])
    ok = await bridge_client.delete_material(mat_id)
    if ok:
        await callback.answer(f"تم حذف المذكرة {mat_id} بنجاح 🗑️", show_alert=True)
        await handle_admin_view_materials(callback)
    else:
        await callback.answer("⚠️ تعذر حذف المذكرة من السيرفر.", show_alert=True)


# (تم نقل معالج رفع المذكرات الموسع مع الغلاف والإطار والتصنيف إلى bot_service/handlers/admin_studio.py)


# ================================================================
# 9. نظام الإذاعة العامة لجميع المدرسين (Broadcast Engine)
# ================================================================

@router.callback_query(F.data == "adm_view:broadcast")
async def handle_admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    recipients = await bridge_client.get_broadcast_recipients()
    total_count = len(recipients)

    await state.set_state(AdminBroadcastStates.waiting_broadcast_message)
    await callback.message.answer(
        f"📢 <b>نظام الإذاعة العامة لجميع المدرسين</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 عدد المدرسين المسجلين حالياً: <b>{total_count} مدرس</b>\n\n"
        f"👉 <b>أرسل الآن نص الرسالة أو الصورة التي ترغب بإرسالها للجميع:</b>\n"
        f"<i>(يمكنك إرسال رسالة نصية عادية أو صورة مع نص توضيحي)</i>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminBroadcastStates.waiting_broadcast_message)
async def process_admin_broadcast_message(message: Message, state: FSMContext, bot: Bot):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return

    await state.clear()
    recipients = await bridge_client.get_broadcast_recipients()
    if not recipients:
        await message.answer("⚠️ لا يوجد أي مدرسين مسجلين للإرسال إليهم.")
        return

    await message.answer(f"⏳ جاري بدء بث الإذاعة إلى ({len(recipients)}) حساب...")

    success_count = 0
    fail_count = 0

    header = "📢 <b>إعلان هام من إدارة منصة Z-PACT:</b>\n━━━━━━━━━━━━━━━━━━\n"

    for chat_id in recipients:
        try:
            if message.text:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"{header}{html.escape(message.text)}",
                    parse_mode="HTML"
                )
            elif message.photo:
                photo_id = message.photo[-1].file_id
                cap = f"{header}{html.escape(message.caption or '')}"
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_id,
                    caption=cap,
                    parse_mode="HTML"
                )
            success_count += 1
            import asyncio
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1

    await message.answer(
        f"🎉 <b>اكتملت عملية الإذاعة بنجاح!</b>\n\n"
        f"  • تم التسليم بنجاح: <code>{success_count}</code> مدرس\n"
        f"  • تعذر التسليم (حسابات محظورة/غير موجودة): <code>{fail_count}</code>",
        parse_mode="HTML"
    )


# ================================================================
# 10. إرسال نسختي تطبيق الأندرويد (نسخة الأدمن والرادار + نسخة المدرسين الآمنة)
# ================================================================

async def _send_apk_variant(event: Message | CallbackQuery, variant: str):
    user_id = event.from_user.id
    if not is_admin_user(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("غير مصرح", show_alert=True)
        return

    import os
    from bot_service.config import BASE_DIR

    if variant == "teacher":
        apk_filename = "Z-PACT-Teacher-Store.apk"
        apk_candidates = [
            os.path.join(BASE_DIR, "Z-PACT-Teacher-Store.apk"),
            os.path.join(BASE_DIR, "android_app", "Z-PACT-Teacher-Store.apk"),
            os.path.join(BASE_DIR, "android_app", "app", "build", "outputs", "apk", "teacher", "debug", "app-teacher-debug.apk"),
        ]
        caption = (
            "🎓 <b>نسخة تطبيق المنصة للمدرسين v3.1 — (Z-PACT Teacher Store)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ <b>خفيفة وآمنة تماماً للمدرسين (صفر أذونات رسائل SMS — نظيفة 100%):</b>\n"
            "• تسجيل ودخول آمن وموثق بالهاتف وكلمة السر ورمز التحقق (OTP) وقفل الأجهزة.\n"
            "• سلة شراء عدة مذكرات معاً وإرفاق التحويل أو الدفع الآلي الفوري دون أي ريفرش عشوائي."
        )
    elif variant == "sender":
        apk_filename = "Z-PACT-Sender-Team.apk"
        apk_candidates = [
            os.path.join(BASE_DIR, "Z-PACT-Sender-Team.apk"),
            os.path.join(BASE_DIR, "android_app", "Z-PACT-Sender-Team.apk"),
            os.path.join(BASE_DIR, "android_app", "app", "build", "outputs", "apk", "sender", "debug", "app-sender-debug.apk"),
        ]
        caption = (
            "🚀 <b>نسخة فريق المرسلين الفاخرة v3.1 — (Z-PACT Sender Team)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ <b>مخصصة لفريق الإرسال (صفر أذونات رسائل SMS — نظيفة 100%):</b>\n"
            "• تسجيل بالهاتف وكلمة السر ورمز الـ OTP مع قفل السيريال على هاتف المرسل.\n"
            "• إنشاء وختم طلبات المدرسين للواتساب وتحميلها بضغطة زر وحساب الأرباح والعمولات تلقائياً!"
        )
    else:
        apk_filename = "Z-PACT-Admin-Radar.apk"
        apk_candidates = [
            os.path.join(BASE_DIR, "Z-PACT-Admin-Radar.apk"),
            os.path.join(BASE_DIR, "Z-PACT-Enterprise.apk"),
            os.path.join(BASE_DIR, "android_app", "Z-PACT-Admin-Radar.apk"),
            os.path.join(BASE_DIR, "android_app", "app", "build", "outputs", "apk", "admin", "debug", "app-admin-debug.apk"),
        ]
        caption = (
            "👑 <b>نسخة الأدمن السيادية ورادار الـ SMS الفائق v3.1 — (Z-PACT Admin Radar)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚡ <b>حصرية لهاتف الإدارة والمشرفين:</b>\n"
            "• بوابة القيادة بكلمة سر فقط، حوكمة أول أدمن المؤسس، وموافقة فورية على المشرفين الجدد.\n"
            "• رادار كشف وتدقيق التحويلات 24/7 + إرسال رموز التحقق OTP آلياً للمدرسين والمرسلين عبر شريحة الأدمن!"
        )

    found_apk = next((p for p in apk_candidates if os.path.isfile(p)), None)
    target_msg = event.message if isinstance(event, CallbackQuery) else event

    if isinstance(event, CallbackQuery):
        await event.answer(f"⏳ جاري رفع {apk_filename} لهاتفك...")

    if not found_apk:
        await target_msg.answer(
            f"⚠️ <b>ملف {html.escape(apk_filename)} غير مبني بعد على السيرفر.</b>\n"
            "قم بتشغيل ملف <code>بناء_تطبيق_الاندرويد.bat</code> أولاً ثم أعد المحاولة.",
            parse_mode="HTML",
        )
        return

    size_mb = os.path.getsize(found_apk) / (1024 * 1024)
    status_msg = await target_msg.answer(
        f"📤 جاري رفع <b>{html.escape(apk_filename)}</b> ({size_mb:.2f} MB) إلى محادثتك...",
        parse_mode="HTML",
    )

    try:
        doc = FSInputFile(found_apk, filename=apk_filename)
        await target_msg.answer_document(document=doc, caption=caption, parse_mode="HTML")
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"⚠️ تعذر إرسال ملف الـ APK: {html.escape(str(e))}", parse_mode="HTML")


@router.message(Command("get_apk"))
@router.message(Command("get_all_apks"))
async def handle_send_all_three_apks(message: Message):
    await _send_apk_variant(message, "admin")
    await _send_apk_variant(message, "teacher")
    await _send_apk_variant(message, "sender")


@router.message(Command("get_admin_apk"))
@router.callback_query(F.data.in_({"adm_view:get_apk", "adm_view:get_admin_apk"}))
async def handle_send_admin_apk(event: Message | CallbackQuery):
    await _send_apk_variant(event, "admin")


@router.message(Command("get_teacher_apk"))
@router.callback_query(F.data == "adm_view:get_teacher_apk")
async def handle_send_teacher_apk(event: Message | CallbackQuery):
    await _send_apk_variant(event, "teacher")


@router.message(Command("get_sender_apk"))
@router.callback_query(F.data == "adm_view:get_sender_apk")
async def handle_send_sender_apk(event: Message | CallbackQuery):
    await _send_apk_variant(event, "sender")


# ================================================================
# 11. معالجات الإدارة السريعة للإصدار الثالث (المرسلون / المدرسون / السلال / OTA)
# ================================================================

@router.callback_query(F.data == "adm_view:v3_senders")
async def handle_admin_v3_senders_report(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    ov = await bridge_client.get_v3_admin_overview()
    if not ov:
        await callback.answer("تعذر الاتصال بالسيرفر", show_alert=True)
        return

    s = ov.get("summary", {})
    senders = ov.get("senders", [])
    lines = [
        "🚀 <b>تقرير فريق المرسلين والعمولات والعهدة المالية</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"👥 المرسلون المعتمدون: <code>{s.get('active_senders', 0)}</code> من <code>{s.get('total_senders', 0)}</code>",
        f"⏳ طلبات تفعيل معلقة: مرسلون (<code>{s.get('pending_senders', 0)}</code>) | مدرسون (<code>{s.get('pending_teachers', 0)}</code>)",
        f"🛒 سلال معلقة للمراجعة: <code>{s.get('pending_bundles', 0)}</code>",
        f"📄 إجمالي المذكرات المختومة بالمرسلين: <code>{s.get('total_sender_pdfs', 0)}</code> ملف",
        f"💎 إجمالي عمولات المرسلين: <code>{s.get('total_commissions_paid', 0)}</code> ج.م",
        f"💰 صافي العهدة المستحقة للإدارة: <code>{s.get('total_net_due_to_admin', 0)}</code> ج.م",
        "",
    ]
    for snd in senders[:8]:
        st_ico = "🟢" if snd.get("status") == "approved" else ("🟡" if snd.get("status") == "pending" else "🔴")
        lines.append(
            f"{st_ico} <b>{html.escape(str(snd.get('display_name') or snd.get('phone')))}</b> (<code>{html.escape(str(snd.get('phone')))}</code>)\n"
            f"   ├ ملفات: <code>{snd.get('total_pdfs_count', 0)}</code> | عمولة: <code>{snd.get('total_commission_earned', 0)}</code> ج\n"
            f"   └ صافي للإدارة: <code>{snd.get('net_due_to_admin', 0)}</code> ج | سيريال: <code>{html.escape(str(snd.get('serial_code', '')))}</code>"
        )

    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "adm_view:push_ota")
async def handle_admin_push_ota(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return

    res = await bridge_client.publish_ota_update(
        flavor="all",
        release_notes="🚀 تحديث جديد لمنظومة Z-PACT! اضغط تحديث الآن لتثبيت أحدث إصدار تلقائياً.",
    )
    if res:
        await callback.message.answer(
            "✅ <b>تم بث إشارة التحديث الهوائي التلقائي (OTA) لجميع التطبيقات الثلاثة!</b>\n"
            "ستظهر نافذة <i>تحديث الآن</i> تلقائياً لدى جميع المدرسين والمرسلين والأدمن.",
            parse_mode="HTML",
        )
        await callback.answer("تم بث التحديث 🚀")
    else:
        await callback.answer("تعذر بث التحديث", show_alert=True)


@router.callback_query(F.data.startswith("v3_snd:"))
async def handle_v3_sender_inline_action(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return
    _, action, sid_str = callback.data.split(":")
    res = await bridge_client.manage_sender(int(sid_str), action=action)
    if not res:
        await callback.answer("فشل تنفيذ الإجراء", show_alert=True)
        return
    msg = "✅ تم اعتماد وتفعيل المرسل بنجاح! فتح التطبيق على هاتفه الآن." if action == "approve" else "❌ تم رفض طلب المرسل."
    await callback.message.reply(msg)
    await callback.answer("تم التنفيذ ✅")


@router.callback_query(F.data.startswith("v3_tch:"))
async def handle_v3_teacher_inline_action(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return
    _, action, uid_str = callback.data.split(":")
    res = await bridge_client.manage_teacher(int(uid_str), action=action)
    if not res:
        await callback.answer("فشل تنفيذ الإجراء", show_alert=True)
        return
    msg = "✅ تم تفعيل حساب المدرس فوراً! فتح المتجر على هاتفه تلقائياً." if action == "approve" else "❌ تم رفض حساب المدرس."
    await callback.message.reply(msg)
    await callback.answer("تم التنفيذ ✅")


@router.callback_query(F.data.startswith("v3_bnd:"))
async def handle_v3_bundle_inline_action(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return
    _, action, bcode = callback.data.split(":", 2)
    res = await bridge_client.manage_bundle(bcode, action=action)
    if not res:
        await callback.answer("فشل تنفيذ الإجراء", show_alert=True)
        return
    msg = f"✅ تمت الموافقة على السلة `{bcode}` وجاري ختم وإرسال كل المذكرات فوراً!" if action == "approve" else f"❌ تم رفض السلة `{bcode}`."
    await callback.message.reply(msg, parse_mode="Markdown")
    await callback.answer("تم التنفيذ ✅")


@router.callback_query(F.data.startswith("v3_coadm:"))
async def handle_v3_coadm_inline_action(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("غير مصرح", show_alert=True)
        return
    _, action, uid_str = callback.data.split(":")
    res = await bridge_client.manage_co_admin(int(uid_str), action=action)
    if not res:
        await callback.answer("فشل تنفيذ الإجراء", show_alert=True)
        return
    msg = (
        "✅ <b>تمت الموافقة من أول أدمن على تفعيل حساب المشرف بنجاح!</b>\nسيفتح تطبيق الإدارة على هاتفه فوراً."
        if action == "approve"
        else "❌ <b>تم رفض حساب المشرف الإضافي وحظره.</b>"
    )
    await callback.message.reply(msg, parse_mode="HTML")
    await callback.answer("تم التنفيذ ✅")


