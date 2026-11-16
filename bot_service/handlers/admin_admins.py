# ================================================================
# bot_service/handlers/admin_admins.py — إدارة المشرفين وتحديد الصلاحيات
# ================================================================

import html
import logging
import re
from typing import Dict, Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot_service.admin_permissions import (
    ADMIN_PERMISSIONS,
    PERM_KEYS,
    PERM_LABELS,
    check_admin_perm,
    full_permissions_dict,
    normalize_permissions,
    preset_permissions_dict,
)
from bot_service.config import bot_config
from bot_service.keyboards.reply import get_main_keyboard
from bot_service.states.order_flow import AdminManageStates

logger = logging.getLogger("bot_service.handlers.admin_admins")
router = Router(name="admin_admins_router")


def _format_perms_lines(perms: Dict[str, bool]) -> str:
    lines = []
    for key, label, desc in ADMIN_PERMISSIONS:
        mark = "✅" if perms.get(key) else "❌"
        lines.append(f"{mark} <b>{html.escape(label)}</b>\n    <i>{html.escape(desc)}</i>")
    return "\n".join(lines)


def _count_enabled(perms: Dict[str, bool]) -> int:
    return sum(1 for k in PERM_KEYS if perms.get(k))


def _render_admins_overview() -> Tuple[str, InlineKeyboardMarkup]:
    owners = sorted(bot_config._registry().owner_ids)
    managed = bot_config.list_managed_admins()
    legacy_ids = sorted(
        uid
        for uid in bot_config.ADMIN_TELEGRAM_IDS
        if not bot_config.is_owner_admin(uid)
        and bot_config.get_admin_record(uid) is None
    )

    lines = [
        "👥 <b>إدارة المشرفين وتحديد الصلاحيات</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"👑 <b>المالك الرئيسي (كامل الصلاحيات الدائمة):</b>",
    ]
    for oid in owners:
        lines.append(f"  • <code>{oid}</code> 🛡️")

    lines.append("")
    if managed:
        lines.append(f"🛡️ <b>المشرفون المضافون ({len(managed)}):</b>")
        for rec in managed:
            cnt = _count_enabled(rec["permissions"])
            badge = "👑 كل الصلاحيات (زي تحكماتك)" if cnt == len(PERM_KEYS) else f"🎛️ {cnt}/{len(PERM_KEYS)} صلاحيات"
            lines.append(
                f"  • <b>{html.escape(rec['label'])}</b> (<code>{rec['telegram_id']}</code>) — {badge}"
            )
    else:
        lines.append("ℹ️ <i>لا يوجد مشرفون مضافون حالياً غير المالك الرئيسي.</i>")

    if legacy_ids:
        lines.append("")
        lines.append("⚙️ <i>معرفات بيئة أخرى:</i> " + "، ".join(f"<code>{x}</code>" for x in legacy_ids))

    lines.append("\n💡 <i>اضغط ➕ لإضافة مشرف جديد وتحديد إن كان يأخذ تحكماتك بالكامل أو صلاحيات مخفضة، أو اضغط على أي مشرف لتعديل صلاحياته فوراً.</i>")

    rows = [[InlineKeyboardButton(text="➕ إضافة مشرف جديد", callback_data="adm_adm:add")]]
    for rec in managed[:30]:
        cnt = _count_enabled(rec["permissions"])
        icon = "👑" if cnt == len(PERM_KEYS) else "🛡️"
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {rec['label'][:24]} ({rec['telegram_id']}) [{cnt}/{len(PERM_KEYS)}]",
                callback_data=f"adm_adm:view:{rec['telegram_id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="🔙 العودة للوحة التحكم", callback_data="adm_view:stats")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _render_perm_editor(
    mode: str,
    target_uid: int,
    label: str,
    perms: Dict[str, bool],
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    بناء بطاقة تخصيص الصلاحيات سواء عند إضافة مشرف جديد (mode='new')
    أو عند تعديل مشرف حالي (mode='edit').
    """
    cnt = _count_enabled(perms)
    is_full = cnt == len(PERM_KEYS)
    title_hdr = "➕ <b>تحديد صلاحيات المشرف الجديد</b>" if mode == "new" else "✏️ <b>تعديل صلاحيات المشرف</b>"
    status_badge = "👑 نفس تحكماتك بالضبط (كامل الصلاحيات)" if is_full else f"🎛️ صلاحيات مخصصة ({cnt} من {len(PERM_KEYS)})"

    text = (
        f"{title_hdr}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>المشرف:</b> {html.escape(label)}\n"
        f"🆔 <b>المعرف (ID):</b> <code>{target_uid}</code>\n"
        f"🔐 <b>المستوى الحالي:</b> {status_badge}\n\n"
        f"{_format_perms_lines(perms)}\n\n"
        "👇 <i>اضغط على أي صلاحية لتفعيلها (✅) أو إيقافها (❌)، أو اختر باقة جاهزة مباشرة:</i>"
    )

    prefix = "adm_adm_new" if mode == "new" else f"adm_adm_edit:{target_uid}"
    rows = []

    if mode == "new":
        rows.append([InlineKeyboardButton(
            text="👑 نفس تحكماتي بالضبط (حفظ وتفعيل الكل)",
            callback_data="adm_adm_new:save_full",
        )])
        rows.append([
            InlineKeyboardButton(text="🛡️ الكل عدا المشرفين", callback_data="adm_adm_new:preset_no_mgr"),
            InlineKeyboardButton(text="⚡ عمليات يومية", callback_data="adm_adm_new:preset_ops"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="👑 منحه كل الصلاحيات", callback_data=f"adm_adm_edit:{target_uid}:preset_full"),
            InlineKeyboardButton(text="🛡️ الكل عدا المشرفين", callback_data=f"adm_adm_edit:{target_uid}:preset_no_mgr"),
        ])
        rows.append([
            InlineKeyboardButton(text="⚡ تقليل لعمليات يومية", callback_data=f"adm_adm_edit:{target_uid}:preset_ops"),
        ])

    for key, short_lbl, _ in ADMIN_PERMISSIONS:
        mark = "✅" if perms.get(key) else "❌"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {short_lbl}",
            callback_data=f"{prefix}:tog:{key}",
        )])

    if mode == "new":
        rows.append([
            InlineKeyboardButton(text="☑️ تفعيل الكل", callback_data="adm_adm_new:all_on"),
            InlineKeyboardButton(text="☐ تصفير الكل", callback_data="adm_adm_new:all_off"),
        ])
        rows.append([
            InlineKeyboardButton(text="💾 حفظ وتفعيل المشرف", callback_data="adm_adm_new:save"),
            InlineKeyboardButton(text="❌ إلغاء", callback_data="adm_adm:list"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="🗑️ إزالة المشرف وسحب صلاحياته", callback_data=f"adm_adm:del_ask:{target_uid}"),
        ])
        rows.append([
            InlineKeyboardButton(text="🔙 العودة لقائمة المشرفين", callback_data="adm_adm:list"),
        ])

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _extract_target_from_message(message: Message) -> Tuple[Optional[int], str, Optional[str]]:
    """
    استخراج معرف تيليجرام واسم الشخص سواء عبر كتابة الرقم أو تحويل رسالة أو مشاركة جهة اتصال.
    ترجع: (user_id, label, error_message)
    """
    # 1. عبر تحويل رسالة (Forward) في aiogram 3.x أو legacy
    fwd_origin = getattr(message, "forward_origin", None)
    if fwd_origin is not None:
        sender_user = getattr(fwd_origin, "sender_user", None)
        if sender_user is not None:
            return int(sender_user.id), (sender_user.full_name or f"مشرف {sender_user.id}"), None
        sender_user_name = getattr(fwd_origin, "sender_user_name", None)
        if sender_user_name or type(fwd_origin).__name__ == "MessageOriginHiddenUser":
            return (
                None,
                "",
                "🔒 حساب هذا الشخص يفعّل إخفاء الهوية عند تحويل الرسائل.\n"
                "💡 اطلب منه إرسال الأمر <code>/id</code> داخل البوت ثم انسخ الرقم وأرسله لي هنا.",
            )

    fwd_from = getattr(message, "forward_from", None)
    if fwd_from is not None:
        return int(fwd_from.id), (fwd_from.full_name or f"مشرف {fwd_from.id}"), None

    # 2. عبر مشاركة جهة اتصال (Contact)
    contact = getattr(message, "contact", None)
    if contact is not None:
        if contact.user_id:
            cname = " ".join(x for x in (contact.first_name, contact.last_name) if x).strip() or f"مشرف {contact.user_id}"
            return int(contact.user_id), cname, None
        return (
            None,
            "",
            "⚠️ جهة الاتصال هذه غير مرتبطة بحساب تيليجرام ظاهر.\n"
            "💡 أرسل معرف تيليجرام الرقمي (ID) أو اطلب من الشخص إرسال <code>/id</code> داخل البوت.",
        )

    # 3. عبر النص: "123456789" أو "123456789 أحمد" أو "أحمد | 123456789"
    raw = (message.text or "").strip()
    m = re.search(r"(?<!\d)(\d{5,15})(?!\d)", raw)
    if not m:
        return (
            None,
            "",
            "⚠️ لم أجد رقم معرّف تيليجرام (ID) صحيح في رسالتك.\n\n"
            "أرسل المعرف بهذا الشكل مثلاً:\n"
            "• <code>123456789</code>\n"
            "• أو مع الاسم: <code>123456789 أحمد مساعدي</code>\n"
            "• أو قم بتحويل (Forward) أي رسالة منه إلى هنا.",
        )

    tid = int(m.group(1))
    remainder = (raw[:m.start()] + " " + raw[m.end():]).strip(" |:-،,\t\n")
    label = " ".join(remainder.split())[:64] if remainder else f"مشرف {tid}"
    return tid, label, None


async def _notify_target_admin_change(
    bot: Bot,
    target_uid: int,
    *,
    added: bool,
    perms: Optional[Dict[str, bool]] = None,
) -> bool:
    """إشعار الشخص المستهدف عند منحه أو تحديث أو سحب صلاحياته بشكل لطيف وآمن."""
    try:
        if added and perms is not None:
            granted = [f"• {PERM_LABELS[k]}" for k in PERM_KEYS if perms.get(k)]
            summary = "\n".join(granted) if granted else "• (لا توجد صلاحيات مفعلة)"
            await bot.send_message(
                chat_id=target_uid,
                text=(
                    "🛡️ <b>تم منحك صلاحية المشرف في بوت Z-PACT!</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>الصلاحيات المفعلة لك:</b>\n"
                    f"{summary}\n\n"
                    "👇 اضغط على زر <b>🛡️ لوحة تحكم الإدارة</b> بالأسفل أو أرسل <code>/admin</code> للبدء."
                ),
                reply_markup=get_main_keyboard(is_admin=True),
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=target_uid,
                text="ℹ️ تم سحب صلاحيات المشرف من حسابك بواسطة المالك الرئيسي.",
                reply_markup=get_main_keyboard(is_admin=False),
                parse_mode="HTML",
            )
        return True
    except Exception as e:
        logger.debug(f"[AdminMgmt] Could not notify target user {target_uid}: {e}")
        return False


# ================================================================
# نقاط الدخول: عرض قائمة المشرفين
# ================================================================

@router.message(Command("admins"))
@router.callback_query(F.data.in_({"adm_view:admins", "adm_adm:list"}))
async def handle_admins_list(event: Message | CallbackQuery, state: FSMContext):
    if not await check_admin_perm(event, bot_config, "manage_admins", state=state):
        return
    await state.clear()
    text, kb = _render_admins_overview()
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await event.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


# ================================================================
# إضافة مشرف جديد
# ================================================================

@router.callback_query(F.data == "adm_adm:add")
async def handle_add_admin_start(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "manage_admins", state=state):
        return
    await state.set_state(AdminManageStates.waiting_new_admin_target)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء والعودة", callback_data="adm_adm:list")],
    ])
    await callback.message.answer(
        "➕ <b>إضافة مشرف جديد للبوت</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "أرسل الآن بيانات الشخص بأي طريقة تناسبك:\n"
        "1️⃣ اكتب <b>الآيدي (ID)</b> مباشرة أو مع الاسم:\n"
        "<code>123456789</code>  أو  <code>123456789 محمد أحمد</code>\n"
        "2️⃣ أو قم بـ <b>تحويل (Forward)</b> أي رسالة منه هنا.\n"
        "3️⃣ أو شارك <b>جهة اتصاله (Contact)</b>.\n\n"
        "💡 <i>لمعرفة الآيدي بسهولة، يقدر الشخص يرسل أمر <code>/id</code> داخل البوت ويبعتلك الرقم.</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminManageStates.waiting_new_admin_target)
async def handle_add_admin_target_input(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "manage_admins", state=state):
        return

    if message.text and message.text.strip().lower() in ("إلغاء", "الغاء", "cancel", "/cancel"):
        await state.clear()
        text, kb = _render_admins_overview()
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    target_uid, label, err = _extract_target_from_message(message)
    if err or target_uid is None:
        await message.answer(err or "⚠️ تعذر قراءة معرف المستخدم.", parse_mode="HTML")
        return

    if bot_config.is_owner_admin(target_uid):
        await message.answer("👑 هذا الحساب هو <b>المالك الرئيسي</b> بالفعل ويمتلك كامل الصلاحيات الدائمة.", parse_mode="HTML")
        return

    if target_uid == message.from_user.id:
        await message.answer("ℹ️ هذا حسابك الحالي! أرسل معرف (ID) المشرف الجديد الذي تريد إضافته.", parse_mode="HTML")
        return

    existing = bot_config.get_admin_record(target_uid)
    initial_perms = existing["permissions"] if existing else full_permissions_dict()
    effective_label = existing["label"] if (existing and label.startswith("مشرف ")) else label

    await state.set_state(AdminManageStates.customizing_new_admin_perms)
    await state.update_data(
        new_admin_id=target_uid,
        new_admin_label=effective_label,
        new_admin_perms=initial_perms,
    )
    text, kb = _render_perm_editor("new", target_uid, effective_label, initial_perms)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _commit_new_admin(callback: CallbackQuery, state: FSMContext, perms: Dict[str, bool]) -> None:
    data = await state.get_data()
    target_uid = data.get("new_admin_id")
    label = data.get("new_admin_label") or f"مشرف {target_uid}"
    if not target_uid:
        await state.clear()
        await callback.answer("⚠️ انتهت جلسة الإضافة، أعد المحاولة.", show_alert=True)
        return

    norm = normalize_permissions(perms)
    if _count_enabled(norm) == 0:
        await callback.answer("⚠️ اختر صلاحية واحدة على الأقل قبل الحفظ.", show_alert=True)
        return

    rec = bot_config.set_admin_with_permissions(
        int(target_uid),
        norm,
        label=label,
        added_by=callback.from_user.id,
    )
    await state.clear()

    notified = await _notify_target_admin_change(
        callback.bot,
        int(target_uid),
        added=True,
        perms=rec["permissions"],
    )
    cnt = _count_enabled(rec["permissions"])
    role_desc = "👑 كامل الصلاحيات (مثل تحكماتك بالضبط)" if cnt == len(PERM_KEYS) else f"🎛️ صلاحيات مخصصة ({cnt}/{len(PERM_KEYS)})"
    dm_note = "✅ تم إشعار المشرف في الخاص وتفعيل لوحة الإدارة لديه." if notified else "ℹ️ لم تبدأ المحادثة مع البوت من حسابه بعد؛ بمجرد ضغطه /start ستظهر له لوحة التحكم بالصلاحيات المحددة."

    list_text, list_kb = _render_admins_overview()
    try:
        await callback.message.edit_text(
            f"🎉 <b>تم حفظ وتفعيل المشرف بنجاح!</b>\n"
            f"👤 <b>الاسم:</b> {html.escape(rec['label'])} (<code>{rec['telegram_id']}</code>)\n"
            f"🔐 <b>الصلاحيات:</b> {role_desc}\n"
            f"{dm_note}\n\n"
            f"{list_text}",
            reply_markup=list_kb,
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(list_text, reply_markup=list_kb, parse_mode="HTML")
    await callback.answer("تمت إضافة المشرف ✅")


@router.callback_query(F.data.startswith("adm_adm_new:"), AdminManageStates.customizing_new_admin_perms)
async def handle_new_admin_perm_action(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "manage_admins", state=state):
        return

    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    target_uid = data.get("new_admin_id")
    label = data.get("new_admin_label") or f"مشرف {target_uid}"
    perms = normalize_permissions(data.get("new_admin_perms"))

    if action == "save_full":
        await _commit_new_admin(callback, state, full_permissions_dict())
        return
    if action == "save":
        await _commit_new_admin(callback, state, perms)
        return

    if action == "preset_no_mgr":
        perms = preset_permissions_dict("no_admin_mgr")
    elif action == "preset_ops":
        perms = preset_permissions_dict("operations")
    elif action == "all_on":
        perms = full_permissions_dict()
    elif action == "all_off":
        perms = preset_permissions_dict("empty")
    elif action.startswith("tog:"):
        pkey = action.split(":", 1)[1]
        if pkey in PERM_KEYS:
            perms[pkey] = not perms.get(pkey, False)

    await state.update_data(new_admin_perms=perms)
    text, kb = _render_perm_editor("new", int(target_uid), label, perms)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


# ================================================================
# عرض / تعديل / سحب صلاحيات مشرف حالي
# ================================================================

@router.callback_query(F.data.startswith("adm_adm:view:"))
async def handle_view_existing_admin(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "manage_admins"):
        return
    uid = int(callback.data.split(":")[2])
    if bot_config.is_owner_admin(uid):
        await callback.answer("المالك الرئيسي محمي ولا يمكن تعديل صلاحياته.", show_alert=True)
        return
    rec = bot_config.get_admin_record(uid)
    if not rec:
        await callback.answer("⚠️ هذا المشرف غير موجود في القائمة.", show_alert=True)
        return

    text, kb = _render_perm_editor("edit", uid, rec["label"], rec["permissions"])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_adm_edit:"))
async def handle_edit_existing_admin_perm(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "manage_admins"):
        return

    parts = callback.data.split(":", 3)
    if len(parts) < 3:
        await callback.answer()
        return
    uid = int(parts[1])
    op = parts[2]
    arg = parts[3] if len(parts) > 3 else ""

    if bot_config.is_owner_admin(uid):
        await callback.answer("المالك الرئيسي محمي ولا يمكن تعديل صلاحياته.", show_alert=True)
        return
    rec = bot_config.get_admin_record(uid)
    if not rec:
        await callback.answer("⚠️ المشرف غير موجود.", show_alert=True)
        return

    perms = normalize_permissions(rec["permissions"])
    if op == "preset_full":
        perms = full_permissions_dict()
    elif op == "preset_no_mgr":
        perms = preset_permissions_dict("no_admin_mgr")
    elif op == "preset_ops":
        perms = preset_permissions_dict("operations")
    elif op == "tog" and arg in PERM_KEYS:
        if uid == callback.from_user.id and arg == "manage_admins" and perms.get("manage_admins"):
            await callback.answer("⚠️ لا يمكنك إيقاف صلاحية إدارة المشرفين عن نفسك!", show_alert=True)
            return
        candidate = dict(perms)
        candidate[arg] = not candidate.get(arg, False)
        if _count_enabled(candidate) == 0:
            await callback.answer("⚠️ يجب بقاء صلاحية واحدة على الأقل أو اختر إزالة المشرف.", show_alert=True)
            return
        perms = candidate

    updated = bot_config.set_admin_with_permissions(uid, perms, label=rec["label"])
    text, kb = _render_perm_editor("edit", uid, updated["label"], updated["permissions"])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("تم تحديث الصلاحيات ✅")


@router.callback_query(F.data.startswith("adm_adm:del_ask:"))
async def handle_remove_admin_confirm(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "manage_admins"):
        return
    uid = int(callback.data.split(":")[2])
    if bot_config.is_owner_admin(uid):
        await callback.answer("المالك الرئيسي محمي.", show_alert=True)
        return
    if uid == callback.from_user.id:
        await callback.answer("⚠️ لا يمكنك حذف حسابك الحالي من هنا.", show_alert=True)
        return
    rec = bot_config.get_admin_record(uid)
    lbl = rec["label"] if rec else str(uid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ نعم، إزالة وسحب كافة الصلاحيات", callback_data=f"adm_adm:del_yes:{uid}")],
        [InlineKeyboardButton(text="↩️ تراجع", callback_data=f"adm_adm:view:{uid}")],
    ])
    await callback.message.edit_text(
        f"🗑️ <b>تأكيد إزالة المشرف</b>\n\n"
        f"هل أنت متأكد من إزالة <b>{html.escape(lbl)}</b> (<code>{uid}</code>) وسحب جميع صلاحيات الإدارة منه؟",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_adm:del_yes:"))
async def handle_remove_admin_execute(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "manage_admins"):
        return
    uid = int(callback.data.split(":")[2])
    if bot_config.is_owner_admin(uid) or uid == callback.from_user.id:
        await callback.answer("غير مسموح.", show_alert=True)
        return

    removed = bot_config.remove_admin(uid)
    if removed:
        await _notify_target_admin_change(callback.bot, uid, added=False)
    list_text, list_kb = _render_admins_overview()
    prefix = f"🗑️ <b>تم سحب صلاحيات المشرف <code>{uid}</code> وإزالته بنجاح.</b>\n\n" if removed else ""
    try:
        await callback.message.edit_text(f"{prefix}{list_text}", reply_markup=list_kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(f"{prefix}{list_text}", reply_markup=list_kb, parse_mode="HTML")
    await callback.answer("تمت الإزالة ✅" if removed else "غير موجود")
