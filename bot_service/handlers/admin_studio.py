# ================================================================
# bot_service/handlers/admin_studio.py
# استوديو الأدمن من الموبايل: دفعات Excel/Word، رفع المذكرات، الخطوط والإطارات، والمعاينة الفورية
# ================================================================

import asyncio
import gc
import html
import logging
import os
import secrets
from typing import Any, Dict, List, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    WebAppInfo,
)

from bot_service.admin_permissions import check_admin_perm
from bot_service.config import BASE_DIR, bot_config
from bot_service.services.batch_parser import (
    build_excel_template_bytes,
    build_materials_catalog_excel_bytes,
    build_word_template_bytes,
    filter_usable_materials,
    match_material_by_ref,
    parse_text_teacher_entries,
    parse_uploaded_batch_file,
)
from bot_service.services.bridge_client import bridge_client
from bot_service.states.order_flow import (
    AdminBatchStampStates,
    AdminFontSettingsStates,
    AdminMaterialManageStates,
)

logger = logging.getLogger("bot_service.handlers.admin_studio")
router = Router(name="admin_studio_router")

# قفل متسلسل لمنع تشغيل دفعتين في وقت واحد وحماية المعالج المحلي
_BATCH_LOCK = asyncio.Lock()

TMP_UPLOAD_DIR = os.path.join(bot_config.STORAGE_DIR, "bot_tmp")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)


def _is_admin(user_id: int) -> bool:
    return bot_config.is_admin(user_id)


def _safe_filename_part(val: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in (" ", "_", "-") else "_" for ch in str(val or ""))
    cleaned = "_".join(cleaned.split())
    return cleaned[:32] or "teacher"


async def _download_telegram_bytes(bot: Bot, file_id: str) -> bytes:
    finfo = await bot.get_file(file_id)
    stream = await bot.download_file(finfo.file_path)
    return stream.read()


def _cleanup_temp_path(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


# ================================================================
# 1. تحميل قوالب Excel و Word الجاهزة
# ================================================================

@router.message(Command("templates"))
@router.callback_query(F.data == "adm_view:download_templates")
async def handle_send_batch_templates(event: Message | CallbackQuery):
    if not await check_admin_perm(event, bot_config, "batch_vip"):
        return

    target_msg = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer("⏳ جاري تجهيز القوالب بالأسماء الحالية...")

    materials = await bridge_client.get_materials(include_unpublished=True)
    xlsx_bytes = build_excel_template_bytes(materials)
    docx_bytes = build_word_template_bytes(materials)

    await target_msg.answer_document(
        document=BufferedInputFile(xlsx_bytes, filename="Z-PACT_Teachers_Template.xlsx"),
        caption=(
            "📊 <b>نموذج Excel المعتمد لإرسال بيانات المدرسين</b>\n"
            "• <b>الأعمدة الأساسية (اختيارية بالكامل للأدمن، وللغير يكفي أي بيان متاح):</b> <code>اسم المدرس</code> | <code>رقم الهاتف</code> | <code>المذكرة</code>\n"
            "• <b>الأعمدة الإضافية:</b> <code>اسم السلسلة</code> | <code>العلامة المائية</code> | <code>هاتف 2</code> | <code>الخط</code> | <code>حجم الخط</code>\n\n"
            "💡 بعد ملء البيانات أرسل الملف هنا مباشرة وسيتم ختم كل المذكرات بالتتابع!"
        ),
        parse_mode="HTML",
    )
    await target_msg.answer_document(
        document=BufferedInputFile(docx_bytes, filename="Z-PACT_Teachers_Template.docx"),
        caption=(
            "📝 <b>نموذج Word (.docx) لإرسال بيانات المدرسين</b>\n"
            "يدعم الإدخال داخل الجدول أو بنظام الأسطر (<code>الاسم:</code> / <code>الهاتف:</code> / <code>المذكرة:</code>)."
        ),
        parse_mode="HTML",
    )


_CATALOG_EXCEL_TEXT_TRIGGERS = {
    "الخامات",
    "خامات",
    "ملف الخامات",
    "اكسيل الخامات",
    "إكسيل الخامات",
    "قائمة الخامات",
    "قائمة المذكرات",
    "أسماء المذكرات",
    "اسماء المذكرات",
    "اكسل الخامات",
}


@router.message(Command("raw"))
@router.message(Command("materials_excel"))
@router.message(Command("خامات"))
@router.callback_query(F.data == "adm_view:materials_excel")
@router.message(StateFilter(None), lambda m: bot_config.has_permission(m.from_user.id, "batch_vip") and (m.text or "").strip() in _CATALOG_EXCEL_TEXT_TRIGGERS)
async def handle_send_materials_catalog_excel(event: Message | CallbackQuery):
    """إرسال ملف Excel منظم بالخامات والمذكرات الحالية وأسمائها المضبوطة للأدمن عند طلبه."""
    if not await check_admin_perm(event, bot_config, "batch_vip"):
        return

    target_msg = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer("⏳ جاري تجهيز ملف Excel بالخامات والأسماء...")

    materials = await bridge_client.get_materials(include_unpublished=True)
    usable = filter_usable_materials(materials)
    xlsx_bytes = build_materials_catalog_excel_bytes(usable)

    await target_msg.answer_document(
        document=BufferedInputFile(xlsx_bytes, filename="Z-PACT_Materials_Catalog.xlsx"),
        caption=(
            f"📗 <b>قائمة المذكرات والخامات المضبوطة ({len(usable)} مذكرة)</b>\n"
            "• <b>الورقة الأولى (المذكرات والخامات):</b> رقم كل مذكرة (ID)، اسمها المضبوط في قاعدة البيانات، المادة، عدد الصفحات، والحالة.\n"
            "• <b>الورقة الثانية (دفعات جاهزة بهذه الخامات):</b> أعمدة جاهزة لإدخال بيانات المدرسين بجانب كل مذكرة وإعادة إرسال الملف للبوت.\n\n"
            "💡 تقدر تطلب الملف في أي وقت بكتابة <code>الخامات</code> أو <code>/raw</code> أو من زر <b>إكسيل الخامات والأسماء</b>."
        ),
        parse_mode="HTML",
    )


# ================================================================
# 2. نظام الدفعات الذكي (Excel / Word / CSV / نصوص سريعة)
# ================================================================

@router.message(Command("batch"))
@router.callback_query(F.data == "adm_view:batch_help")
async def handle_batch_help(event: Message | CallbackQuery):
    if not await check_admin_perm(event, bot_config, "batch_vip"):
        return

    target_msg = event.message if isinstance(event, CallbackQuery) else event
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ إرسال قائمة مدرسين كنص الآن", callback_data="adm_batch:paste_text")],
        [InlineKeyboardButton(text="📥 تنزيل قوالب Excel و Word", callback_data="adm_view:download_templates")],
        [InlineKeyboardButton(text="🔙 رجوع للوحة الأدمن", callback_data="adm_view:stats")],
    ])
    await target_msg.answer(
        "📊 <b>نظام ختم الدفعات المتسلسل (Excel / Word / نص سريع)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "تقدر من الموبايل تبعت مباشرةً هنا في الشات:\n"
        "1️⃣ <b>ملف Excel (.xlsx أو .csv)</b> فيه بيانات المدرسين واحد ورا التاني.\n"
        "2️⃣ <b>ملف Word (.docx)</b> جدول أو فقرات منظمة.\n"
        "3️⃣ <b>أو نص سريع</b> بالشكل:\n"
        "<code>د. أحمد علي | 01012345678 | اسم المذكرة | سلسلة التفوق</code>\n\n"
        "⚡ <b>حماية المعالج المحلي (Low-CPU Queue):</b>\n"
        "البوت يعالج الملفات <b>واحداً تلو الآخر بالتتابع</b> ويرسل كل PDF فور انتهائه بدون استهلاك زائد للمعالج أو الرامات!",
        reply_markup=kb,
        parse_mode="HTML",
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "adm_batch:paste_text")
async def handle_batch_paste_start(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "batch_vip"):
        return
    await state.set_state(AdminBatchStampStates.waiting_batch_text)
    await callback.message.answer(
        "✍️ <b>أرسل الآن بيانات المدرس أو المدرسين (مدرس واحد أو قائمة):</b>\n\n"
        "<b>الطريقة 1 (سطر لكل مدرس):</b>\n"
        "<code>د. أحمد علي | 01012345678 | اسم المذكرة | سلسلة التفوق | علامة مائية</code>\n\n"
        "<b>الطريقة 2 (بيانات مرقّمة أو كتل):</b>\n"
        "<code>الاسم: أ. محمد محمود\nالهاتف: 01122334455\nالمذكرة: فيزياء\nالسلسلة: النخبة</code>\n\n"
        "<i>(يمكنك إرسال أكثر من مدرس في نفس الرسالة وسأرتبهم بالتتابع!)</i>",
        parse_mode="HTML",
    )
    await callback.answer()


async def _prepare_and_show_batch_review(
    message: Message,
    state: FSMContext,
    raw_entries: List[Dict[str, Any]],
    source_label: str,
) -> None:
    if not raw_entries:
        await message.answer(
            "⚠️ لم يتم العثور على صفوف مدرسين صالحة في الملف/النص.\n"
            "تأكد من وجود أي بيان متاح للختم (الاسم، أو الهاتف، أو اسم المذكرة/السلسلة) أو حمّل القالب عبر /templates.\n"
            "💡 للأدمن: كل حقول البيانات اختيارية.",
            parse_mode="HTML",
        )
        return

    materials = await bridge_client.get_materials(include_unpublished=True)
    matched_entries: List[Dict[str, Any]] = []
    ready_count = 0
    need_default_count = 0
    is_admin = _is_admin(message.from_user.id)

    for idx, item in enumerate(raw_entries, start=1):
        entry = dict(item)
        entry["idx"] = idx
        # للأدمن لا يوجد أي عنصر يعتبر "بيانات ناقصة" نهائياً
        if is_admin:
            entry.pop("_incomplete", None)
        mat, method = match_material_by_ref(entry.get("material_ref"), materials)
        if not mat and not entry.get("material_ref") and entry.get("series_name"):
            cand_mat, cand_method = match_material_by_ref(entry.get("series_name"), materials)
            if cand_mat and cand_method in ("exact_id", "exact_title", "partial_title"):
                mat, method = cand_mat, cand_method
        if mat:
            entry["matched_material_id"] = mat["id"]
            entry["matched_material_title"] = mat["title"]
            entry["match_method"] = method
            entry["status_tag"] = "ready"
            ready_count += 1
        else:
            entry["matched_material_id"] = None
            entry["matched_material_title"] = None
            entry["status_tag"] = "need_material"
            need_default_count += 1
        matched_entries.append(entry)

    default_mat_id = materials[0]["id"] if (len(materials) == 1 and need_default_count > 0) else None
    default_mat_title = materials[0]["title"] if default_mat_id is not None else None

    await state.set_state(AdminBatchStampStates.reviewing_batch)
    await state.update_data(
        batch_entries=matched_entries,
        batch_source=source_label,
        batch_default_mat_id=default_mat_id,
        batch_default_mat_title=default_mat_title,
        batch_font_family=None,
        batch_override_frame_path=None,
    )

    await _send_batch_summary_card(message, await state.get_data())


def _render_batch_summary_text_and_kb(state_data: Dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    entries = state_data.get("batch_entries", [])
    source = state_data.get("batch_source", "دفعة")
    def_id = state_data.get("batch_default_mat_id")
    def_title = state_data.get("batch_default_mat_title")
    font_fam = state_data.get("batch_font_family")
    frame_path = state_data.get("batch_override_frame_path")

    runnable = 0
    missing_mat = 0
    pending_logos = [
        e for e in entries
        if str(e.get("watermark_type") or "").lower() == "logo" and not e.get("watermark_logo_path")
    ]
    ready_logos = [e for e in entries if e.get("watermark_logo_path")]

    lines = [
        f"📋 <b>مراجعة الدفعة المستخرجة ({html.escape(str(source))})</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for e in entries[:15]:
        name = html.escape(str(e.get("teacher_name") or "بدون اسم"))
        phone = html.escape(str(e.get("phone") or "بدون رقم"))
        series_lbl = f" | 📚 {html.escape(str(e.get('series_name')))}" if e.get("series_name") else ""
        if e.get("watermark_logo_path"):
            wm_lbl = " | 🖼️ لوجو ✅"
        elif str(e.get("watermark_type") or "").lower() == "logo":
            wm_lbl = " | 🖼️ مطلوب لوجو ⏳"
        elif e.get("watermark_text"):
            wm_lbl = f" | 💧 {html.escape(str(e.get('watermark_text')))}"
        else:
            wm_lbl = ""

        if e.get("matched_material_id"):
            runnable += 1
            lines.append(f"🟢 #{e['idx']} {name} (<code>{phone}</code>){series_lbl}{wm_lbl} ⬅️ <i>{html.escape(e['matched_material_title'])}</i>")
        elif def_id:
            runnable += 1
            lines.append(f"🟡 #{e['idx']} {name} (<code>{phone}</code>){series_lbl}{wm_lbl} ⬅️ <i>{html.escape(str(def_title))} (افتراضي)</i>")
        else:
            missing_mat += 1
            ref = html.escape(str(e.get("material_ref") or "غير محددة"))
            lines.append(f"⚠️ #{e['idx']} {name} (<code>{phone}</code>){series_lbl}{wm_lbl} — اختر مذكرة افتراضية: <code>{ref}</code>")

    # حساب البقية بعد 15
    for e in entries[15:]:
        if e.get("matched_material_id") or def_id:
            runnable += 1
        else:
            missing_mat += 1

    if len(entries) > 15:
        lines.append(f"<i>... و {len(entries) - 15} مدرس إضافي في نفس الدفعة</i>")

    lines.extend([
        "",
        f"📊 <b>الملخص:</b> جاهز للتنفيذ: <code>{runnable}</code> | يحتاج مذكرة افتراضية: <code>{missing_mat}</code>",
        f"🔤 <b>الخط الموحد:</b> <code>{font_fam or 'خط كل مذكرة المحفوظ'}</code>",
        f"🖼️ <b>إطار مخصص للدفعة:</b> <code>{'مُرفق ✅' if frame_path else 'إطار كل مذكرة الافتراضي'}</code>",
    ])

    if ready_logos:
        lines.append(f"🖼️ <b>صور لوجو جاهزة:</b> <code>{len(ready_logos)}</code> مدرس")

    if pending_logos:
        lines.append(
            f"\n🖼️ <b>مطلوب صور اللوجو:</b> يوجد <code>{len(pending_logos)}</code> مدرس في الملف يريدون علامة مائية بصورة لوجو — "
            "اضغط <b>🖼️ إرسال صور اللوجو المطلوبة</b> بالأسفل لإرسال صورهم:"
        )
    else:
        lines.append(
            "\n🖼️ <b>هل يوجد مدرس عنده صورة لوجو؟</b> لو في حد من المدرسين عاوز علامة مائية بصورة لوجو، "
            "اضغط <b>🖼️ إرفاق لوجو لمدرس</b> وابعت الصورة وهنربطها بيه فوراً."
        )

    lines.extend([
        "",
        "💡 اضغط <b>ابدأ الختم المتسلسل</b> ليتم ختم وإرسال المذكرات واحدة تلو الأخرى بأمان على المعالج:",
    ])

    kb_rows = []
    if runnable > 0:
        kb_rows.append([InlineKeyboardButton(
            text=f"🚀 ابدأ الختم المتسلسل الآن ({runnable} ملف)",
            callback_data="adm_batch:run",
        )])
    if pending_logos:
        kb_rows.append([InlineKeyboardButton(
            text=f"🖼️ إرسال صور اللوجو المطلوبة ({len(pending_logos)})",
            callback_data="adm_batch:collect_logos",
        )])
    kb_rows.append([
        InlineKeyboardButton(text="🖼️ إرفاق لوجو لمدرس / للدفعة", callback_data="adm_batch:pick_logo_teacher"),
    ])
    kb_rows.append([
        InlineKeyboardButton(text="📚 تعيين مذكرة افتراضية", callback_data="adm_batch:pick_default_mat"),
        InlineKeyboardButton(text="🔤 تغيير الخط للدفعة", callback_data="adm_batch:pick_font"),
    ])
    kb_rows.append([
        InlineKeyboardButton(text="🖼️ إرفاق إطار للدفعة", callback_data="adm_batch:attach_frame"),
        InlineKeyboardButton(text="❌ إلغاء الدفعة", callback_data="adm_batch:cancel"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)


async def _send_batch_summary_card(target_msg: Message, state_data: Dict[str, Any]) -> None:
    text, kb = _render_batch_summary_text_and_kb(state_data)
    await target_msg.answer(text, reply_markup=kb, parse_mode="HTML")


async def _safe_delete_message(msg: Optional[Message]) -> None:
    """حذف رسالة انتظار بأمان دون كسر التدفق إذا كانت محذوفة بالفعل."""
    if msg is None:
        return
    try:
        await msg.delete()
    except Exception:
        pass


async def _safe_report_wait_error(
    wait_msg: Optional[Message],
    fallback_msg: Message,
    text: str,
) -> None:
    """تعديل رسالة الانتظار بالخطأ أو إرسال رسالة جديدة إذا تعذر التعديل."""
    if wait_msg is not None:
        try:
            await wait_msg.edit_text(text, parse_mode="HTML")
            return
        except Exception:
            pass
    try:
        await fallback_msg.answer(text, parse_mode="HTML")
    except Exception as send_err:
        logger.warning(f"⚠️ تعذر إرسال تنبيه الخطأ للمشرف: {send_err}")


# استقبال ملف Excel/Word/CSV/TXT تلقائياً من الأدمن في الحالة الافتراضية أو أثناء جلسة الدفعات
@router.message(
    StateFilter(None, AdminBatchStampStates.waiting_batch_text, AdminBatchStampStates.reviewing_batch),
    F.document,
)
async def handle_idle_admin_batch_document(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not _is_admin(uid):
        return
    doc = message.document
    fname = (doc.file_name or "").lower()
    if not fname.endswith((".xlsx", ".docx", ".csv", ".txt")):
        if fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
            cap = (message.caption or "").lower()
            if any(k in cap for k in ("إطار", "اطار", "برواز", "frame")):
                if not await check_admin_perm(message, bot_config, "studio_fonts"):
                    return
                raw = await _download_telegram_bytes(message.bot, doc.file_id)
                await _prompt_attach_frame_to_material(message, state, raw, doc.file_name or "frame.png")
        return
    if not await check_admin_perm(message, bot_config, "batch_vip"):
        return

    wait_msg = await message.answer("🔍 جاري قراءة وتحليل ملف البيانات ومطابقة أسماء المذكرات...")
    try:
        file_bytes = await _download_telegram_bytes(message.bot, doc.file_id)
        entries, label = parse_uploaded_batch_file(
            doc.file_name or "batch.xlsx",
            file_bytes,
            is_admin=_is_admin(message.from_user.id),
        )
        await _prepare_and_show_batch_review(message, state, entries, label)
        await _safe_delete_message(wait_msg)
    except Exception as e:
        logger.exception(f"❌ [Batch Document] تعذر قراءة أو تجهيز الملف: {e}")
        await _safe_report_wait_error(
            wait_msg,
            message,
            f"⚠️ تعذر قراءة الملف: {html.escape(str(e))}",
        )


@router.message(StateFilter(None), F.photo)
async def handle_idle_admin_frame_photo(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    cap = (message.caption or "").lower()
    if any(k in cap for k in ("إطار", "اطار", "برواز", "frame")):
        if not await check_admin_perm(message, bot_config, "studio_fonts"):
            return
        raw = await _download_telegram_bytes(message.bot, message.photo[-1].file_id)
        await _prompt_attach_frame_to_material(message, state, raw, "frame.png")


@router.message(AdminBatchStampStates.waiting_batch_text)
async def process_batch_pasted_text(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "batch_vip", state=state):
        return
    entries = parse_text_teacher_entries(message.text or "", is_admin=True)
    await _prepare_and_show_batch_review(message, state, entries, "قائمة نصية")


@router.callback_query(F.data == "adm_batch:cancel")
async def handle_batch_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ تم إلغاء الدفعة.")
    await callback.answer()


@router.callback_query(F.data == "adm_batch:pick_default_mat", AdminBatchStampStates.reviewing_batch)
async def handle_batch_pick_default_mat(callback: CallbackQuery):
    materials = await bridge_client.get_materials(include_unpublished=True)
    usable = filter_usable_materials(materials)
    if not usable:
        await callback.answer("لا توجد مذكرات", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(
            text=f"📖 {m['title']} (#{m['id']})",
            callback_data=f"adm_batch_setmat:{m['id']}"
        )]
        for m in usable[:45]
    ]
    await callback.message.answer(
        "📚 <b>اختر المذكرة الافتراضية</b> (تُستخدم لأي سطر في الملف لم يحدد مذكرة واضحة):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_batch_setmat:"), AdminBatchStampStates.reviewing_batch)
async def handle_batch_set_default_mat(callback: CallbackQuery, state: FSMContext):
    mat_id = int(callback.data.split(":")[1])
    materials = await bridge_client.get_materials(include_unpublished=True)
    mat = next((m for m in materials if int(m["id"]) == mat_id), None)
    if mat:
        await state.update_data(batch_default_mat_id=mat["id"], batch_default_mat_title=mat["title"])
    await callback.answer("تم تحديد المذكرة الافتراضية ✅")
    await _send_batch_summary_card(callback.message, await state.get_data())


@router.callback_query(F.data == "adm_batch:pick_font", AdminBatchStampStates.reviewing_batch)
async def handle_batch_pick_font(callback: CallbackQuery):
    fonts = await bridge_client.get_fonts()
    if not fonts:
        fonts = [{"key": "cairo", "label": "Cairo Bold"}, {"key": "tahoma", "label": "Tahoma"}, {"key": "arial", "label": "Arial"}]
    rows = [[InlineKeyboardButton(text=f"🔤 {f['label']}", callback_data=f"adm_batch_setfont:{f['key']}")] for f in fonts]
    rows.append([InlineKeyboardButton(text="↩️ استخدام خط كل مذكرة المحفوظ", callback_data="adm_batch_setfont:none")])
    await callback.message.answer(
        "🔤 <b>اختر نوع الخط لتطبيقه على كل ملفات الدفعة:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_batch_setfont:"), AdminBatchStampStates.reviewing_batch)
async def handle_batch_set_font(callback: CallbackQuery, state: FSMContext):
    fkey = callback.data.split(":")[1]
    await state.update_data(batch_font_family=None if fkey == "none" else fkey)
    await callback.answer("تم تحديث الخط للدفعة ✅")
    await _send_batch_summary_card(callback.message, await state.get_data())


@router.callback_query(F.data == "adm_batch:attach_frame", AdminBatchStampStates.reviewing_batch)
async def handle_batch_attach_frame_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBatchStampStates.waiting_batch_frame)
    await callback.message.answer(
        "🖼️ <b>أرسل الآن صورة الإطار / البرواز</b> (كصورة Photo أو ملف PNG/JPG):\n"
        "<i>سيتم تفريغ الخلفية البيضاء تلقائياً وتركيبه على جميع ملفات الدفعة!</i>",
        parse_mode="HTML",
    )
    await callback.answer()


def _batch_entry_label(entry: Dict[str, Any]) -> str:
    return str(
        entry.get("teacher_name")
        or entry.get("series_name")
        or entry.get("phone")
        or f"مدرس #{entry.get('idx')}"
    )


def _find_next_pending_logo_entry(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for e in entries:
        if (
            str(e.get("watermark_type") or "").lower() == "logo"
            and not e.get("watermark_logo_path")
            and not e.get("_logo_skipped")
        ):
            return e
    return None


async def _prompt_next_batch_logo_or_summary(target_msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entries = data.get("batch_entries", [])
    if data.get("batch_logo_target") == "queue":
        nxt = _find_next_pending_logo_entry(entries)
        if nxt:
            await state.set_state(AdminBatchStampStates.waiting_batch_logo)
            await state.update_data(batch_logo_active_idx=nxt["idx"])
            lbl = html.escape(_batch_entry_label(nxt))
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭️ تخطي هذا المدرس", callback_data="adm_batch_logo:skip")],
                [InlineKeyboardButton(text="⬅️ رجوع لمراجعة الدفعة", callback_data="adm_batch_logo:back")],
            ])
            await target_msg.answer(
                f"🖼️ <b>ابعت صورة اللوجو للمدرس #{nxt['idx']}: {lbl}</b>\n"
                f"<i>(أرسلها كصورة Photo أو ملف PNG/JPG وسيتم تفريغ خلفيتها ووضعها علامة مائية له)</i>",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return

    await state.set_state(AdminBatchStampStates.reviewing_batch)
    await state.update_data(batch_logo_target=None, batch_logo_active_idx=None)
    await _send_batch_summary_card(target_msg, await state.get_data())


@router.callback_query(F.data == "adm_batch:collect_logos", AdminBatchStampStates.reviewing_batch)
async def handle_batch_collect_logos_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entries = [dict(e) for e in data.get("batch_entries", [])]
    for e in entries:
        e.pop("_logo_skipped", None)
    await state.update_data(batch_entries=entries, batch_logo_target="queue")
    await callback.answer()
    await _prompt_next_batch_logo_or_summary(callback.message, state)


@router.callback_query(F.data == "adm_batch:pick_logo_teacher", AdminBatchStampStates.reviewing_batch)
async def handle_batch_pick_logo_teacher(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entries = data.get("batch_entries", [])
    rows = [[InlineKeyboardButton(text="🖼️ تطبيق لوجو واحد على كل المدرسين", callback_data="adm_batch_setlogo_tgt:all")]]
    for e in entries[:25]:
        mark = "🖼️ ✅" if e.get("watermark_logo_path") else ("🖼️ ⏳" if str(e.get("watermark_type") or "").lower() == "logo" else "📝")
        lbl = _batch_entry_label(e)[:28]
        rows.append([InlineKeyboardButton(
            text=f"{mark} #{e['idx']} — {lbl}",
            callback_data=f"adm_batch_setlogo_tgt:{e['idx']}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع لمراجعة الدفعة", callback_data="adm_batch_logo:back")])
    await callback.message.answer(
        "🖼️ <b>اختر المدرس الذي تريد وضع لوجو صورة له:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_batch_setlogo_tgt:"), AdminBatchStampStates.reviewing_batch)
async def handle_batch_select_logo_target(callback: CallbackQuery, state: FSMContext):
    tgt = callback.data.split(":", 1)[1]
    data = await state.get_data()
    entries = data.get("batch_entries", [])
    if tgt == "all":
        lbl = "كل المدرسين في الدفعة"
        await state.update_data(batch_logo_target="all", batch_logo_active_idx=None)
    else:
        idx_val = int(tgt)
        entry = next((e for e in entries if int(e.get("idx", 0)) == idx_val), None)
        lbl = f"#{idx_val} — {_batch_entry_label(entry)}" if entry else f"مدرس #{idx_val}"
        await state.update_data(batch_logo_target=f"idx:{idx_val}", batch_logo_active_idx=idx_val)

    await state.set_state(AdminBatchStampStates.waiting_batch_logo)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ رجوع لمراجعة الدفعة", callback_data="adm_batch_logo:back")],
    ])
    await callback.message.answer(
        f"🖼️ <b>ابعت صورة اللوجو الآن لـ ({html.escape(lbl)}):</b>\n"
        f"<i>(أرسل الصورة الآن كصورة Photo أو ملف PNG/JPG)</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "adm_batch_logo:skip", AdminBatchStampStates.waiting_batch_logo)
async def handle_batch_logo_skip(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entries = [dict(e) for e in data.get("batch_entries", [])]
    active_idx = data.get("batch_logo_active_idx")
    for e in entries:
        if active_idx is not None and int(e.get("idx", 0)) == int(active_idx):
            e["_logo_skipped"] = True
    await state.update_data(batch_entries=entries)
    await callback.answer("تم التخطي")
    await _prompt_next_batch_logo_or_summary(callback.message, state)


@router.callback_query(F.data == "adm_batch_logo:back")
async def handle_batch_logo_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entries = [dict(e) for e in data.get("batch_entries", [])]
    for e in entries:
        e.pop("_logo_skipped", None)
    await state.set_state(AdminBatchStampStates.reviewing_batch)
    await state.update_data(batch_entries=entries, batch_logo_target=None, batch_logo_active_idx=None)
    await callback.answer()
    await _send_batch_summary_card(callback.message, await state.get_data())


@router.message(AdminBatchStampStates.waiting_batch_logo, F.photo | F.document)
async def handle_batch_logo_received(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "batch_vip", state=state):
        return

    if message.document and not message.photo:
        mime = (message.document.mime_type or "").lower()
        fname_chk = (message.document.file_name or "").lower()
        if not (mime.startswith("image/") or fname_chk.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))):
            await message.answer("⚠️ يرجى إرسال صورة صالحة (PNG / JPG / Photo).")
            return

    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    fname = (message.document.file_name if message.document else None) or "teacher_logo.png"
    raw_bytes = await _download_telegram_bytes(message.bot, file_id)

    logo_path = await bridge_client.upload_watermark_logo(raw_bytes, fname)
    if not logo_path:
        # مسار احتياطي محلي بنفس آلية تفريغ الإطارات
        logos_dir = os.path.join(BASE_DIR, "Z-PACT-main", "storage", "logos")
        os.makedirs(logos_dir, exist_ok=True)
        logo_name = f"logo_{secrets.token_hex(6)}.png"
        logo_abs = os.path.join(logos_dir, logo_name)
        try:
            from PIL import Image, ImageChops
            import io
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
            r, g, b, a = img.split()
            min_rgb = ImageChops.darker(ImageChops.darker(r, g), b)
            white_alpha = min_rgb.point(lambda v: 0 if v >= 242 else (int((242 - v) * 255 / 22) if v > 220 else 255))
            Image.merge("RGBA", (r, g, b, ImageChops.darker(a, white_alpha))).save(logo_abs, format="PNG", optimize=True)
        except Exception:
            with open(logo_abs, "wb") as f:
                f.write(raw_bytes)
        logo_path = f"/storage/logos/{logo_name}"

    data = await state.get_data()
    entries = [dict(e) for e in data.get("batch_entries", [])]
    target_mode = data.get("batch_logo_target")
    active_idx = data.get("batch_logo_active_idx")

    updated_labels = []
    for e in entries:
        if target_mode == "all" or (active_idx is not None and int(e.get("idx", 0)) == int(active_idx)):
            e["watermark_type"] = "logo"
            e["watermark_logo_path"] = logo_path
            e.pop("watermark_text", None)
            e.pop("_logo_skipped", None)
            updated_labels.append(_batch_entry_label(e))

    await state.update_data(batch_entries=entries)
    done_txt = "كل المدرسين" if target_mode == "all" else ("، ".join(updated_labels) or "المدرس")
    await message.answer(f"✅ تم حفظ صورة اللوجو وتعيينها كعلامة مائية لـ <b>{html.escape(done_txt)}</b>!", parse_mode="HTML")
    await _prompt_next_batch_logo_or_summary(message, state)


@router.message(AdminBatchStampStates.waiting_batch_frame, F.photo | F.document)
async def handle_batch_frame_received(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "batch_vip", state=state):
        return

    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    raw_bytes = await _download_telegram_bytes(message.bot, file_id)

    # حفظ الإطار المؤقت بعد معالجته من Z-PACT frames أو عبر حفظه مؤقتاً في storage/frames
    frames_dir = os.path.join(BASE_DIR, "Z-PACT-main", "storage", "frames")
    os.makedirs(frames_dir, exist_ok=True)
    from PIL import Image, ImageChops
    import io

    fr_name = f"batch_frm_{secrets.token_hex(4)}.png"
    fr_abs = os.path.join(frames_dir, fr_name)
    try:
        # تمرير أولي لـ PIL لو أمكن أو حفظه ليتم تفريغه داخل المحرك
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
        r, g, b, a = img.split()
        min_rgb = ImageChops.darker(ImageChops.darker(r, g), b)
        white_alpha = min_rgb.point(lambda v: 0 if v >= 235 else (int((235 - v) * 255 / 20) if v > 215 else 255))
        Image.merge("RGBA", (r, g, b, ImageChops.darker(a, white_alpha))).save(fr_abs, format="PNG", optimize=True)
    except Exception:
        with open(fr_abs, "wb") as f:
            f.write(raw_bytes)

    await state.set_state(AdminBatchStampStates.reviewing_batch)
    await state.update_data(batch_override_frame_path=f"/storage/frames/{fr_name}")
    await message.answer("✅ تم استلام الإطار وتفريغ خلفيته البيضاء بنجاح!")
    await _send_batch_summary_card(message, await state.get_data())


@router.callback_query(F.data == "adm_batch:run", AdminBatchStampStates.reviewing_batch)
async def handle_batch_execute_sequential(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "batch_vip"):
        return

    data = await state.get_data()
    await state.clear()

    entries = data.get("batch_entries", [])
    def_mat_id = data.get("batch_default_mat_id")
    batch_font = data.get("batch_font_family")
    batch_frame = data.get("batch_override_frame_path")

    runnable = []
    skipped = []
    for e in entries:
        mid = e.get("matched_material_id") or def_mat_id
        if not mid:
            skipped.append(f"#{e.get('idx')} ({e.get('teacher_name') or e.get('series_name') or e.get('phone') or '?'}) — لم تُحدد المذكرة")
            continue
        item = dict(e)
        item["final_material_id"] = int(mid)
        runnable.append(item)

    if not runnable:
        await callback.message.answer("⚠️ لا توجد عناصر جاهزة للتشغيل في هذه الدفعة.")
        await callback.answer()
        return

    await callback.answer("🚀 تم بدء الدفعة المتسلسلة!")
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    bot = callback.bot

    async def _run_queue():
        async with _BATCH_LOCK:
            total = len(runnable)
            ok_count = 0
            fail_lines = list(skipped)
            progress_msg = await bot.send_message(
                chat_id=chat_id,
                text=f"⏳ <b>بدء معالجة الدفعة بهدوء (0 / {total})</b> — ملف واحد كل مرة لحماية المعالج...",
                parse_mode="HTML",
            )

            for pos, item in enumerate(runnable, start=1):
                tname = str(item.get("teacher_name") or "").strip()
                tphone = str(item.get("phone") or "").strip()
                tseries = str(item.get("series_name") or "").strip()
                disp_name = tname or tseries or tphone or "بدون اسم"
                disp_phone = tphone or "بدون رقم"
                try:
                    await progress_msg.edit_text(
                        f"⚙️ <b>جاري المعالجة ({pos} / {total})...</b>\n"
                        f"👤 المدرس: {html.escape(disp_name)} (<code>{html.escape(disp_phone)}</code>)\n"
                        f"✅ مكتمل حتى الآن: <code>{ok_count}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                item_wm_logo = item.get("watermark_logo_path")
                item_wm_type = str(item.get("watermark_type") or "").strip().lower()
                if item_wm_type == "logo" and not item_wm_logo:
                    item_wm_type = "none"
                elif item_wm_logo:
                    item_wm_type = "logo"
                elif not item_wm_type:
                    item_wm_type = "text" if item.get("watermark_text") else "none"

                payload = {
                    "material_id": item["final_material_id"],
                    "buyer_name_stamp": tname,
                    "buyer_phone_stamp": tphone,
                    "buyer_phone_stamp_2": item.get("phone_2"),
                    "series_name": item.get("series_name"),
                    "doc_name_stamp": item.get("series_name"),
                    "watermark_text": None if item_wm_type == "logo" else item.get("watermark_text"),
                    "watermark_type": item_wm_type,
                    "watermark_logo_path": item_wm_logo,
                    "font_family": item.get("font_family") or batch_font,
                    "font_size": item.get("font_size"),
                    "override_frame_path": batch_frame,
                    "telegram_chat_id": str(chat_id),
                    "telegram_user_id": str(user_id),
                    "is_admin": bot_config.is_admin(user_id),
                }
                res = await bridge_client.direct_stamp_sync(payload)
                if not res.get("ok"):
                    fail_lines.append(f"#{item.get('idx')} {disp_name}: {res.get('error')}")
                    await asyncio.sleep(0.3)
                    continue

                info = res["data"]
                order_code = info.get("order_code")
                pdf_bytes = await bridge_client.download_order_pdf(order_code)
                if not pdf_bytes:
                    fail_lines.append(f"#{item.get('idx')} {disp_name}: تعذر تنزيل الـ PDF")
                    await asyncio.sleep(0.3)
                    continue

                mat_title = info.get("material_title", "booklet")
                safe_name = f"{_safe_filename_part(tname or tseries or tphone or 'stamp')}_{_safe_filename_part(mat_title)}_{order_code}.pdf"
                try:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=BufferedInputFile(pdf_bytes, filename=safe_name),
                        caption=(
                            f"✅ <b>[{pos}/{total}] {html.escape(str(mat_title))}</b>\n"
                            f"👤 {html.escape(disp_name)} | 📱 <code>{html.escape(disp_phone)}</code>\n"
                            f"🔖 <code>{html.escape(str(order_code))}</code> ({info.get('file_size_kb', 0)} KB)"
                        ),
                        parse_mode="HTML",
                    )
                    ok_count += 1
                    # حذف النسخة المؤقتة على السيرفر فور استلامها بنجاح في التيليجرام لمنع امتلاء القرص
                    await bridge_client.cleanup_order_pdf(order_code)
                except Exception as send_err:
                    fail_lines.append(f"#{item.get('idx')} {disp_name}: خطأ إرسال ({send_err})")
                finally:
                    del pdf_bytes
                    gc.collect()

                # راحة قصيرة بين كل ملف والآخر لخفض استهلاك المعالج
                await asyncio.sleep(0.5)

            summary = [
                "🎉 <b>اكتملت الدفعة التسلسلية بنجاح!</b>",
                "━━━━━━━━━━━━━━━━━━",
                f"✅ ملفات مرسلة بنجاح: <code>{ok_count}</code> من <code>{total}</code>",
                f"⚠️ متخطى / أخطاء: <code>{len(fail_lines)}</code>",
            ]
            if fail_lines:
                summary.append("\n<b>تفاصيل الملاحظات:</b>")
                for fl in fail_lines[:10]:
                    summary.append(f"• {html.escape(str(fl))}")
            try:
                await progress_msg.edit_text("\n".join(summary), parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=chat_id, text="\n".join(summary), parse_mode="HTML")

    asyncio.create_task(_run_queue())


# ================================================================
# 3. معالج رفع مذكرة كاملة بالتفاصيل من الموبايل (PDF + تصنيف + سعر + وصف + غلاف + إطار)
# ================================================================

@router.callback_query(F.data == "adm_mat_upload")
async def handle_full_material_upload_start(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "materials"):
        return
    await state.set_state(AdminMaterialManageStates.uploading_pdf)
    await callback.message.answer(
        "📤 <b>رفع مذكرة جديدة بكامل تفاصيلها من الموبايل:</b>\n\n"
        "👉 <b>الخطوة 1/7:</b> أرسل الآن ملف الـ <b>PDF</b> الأصلي هنا في الشات:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminMaterialManageStates.uploading_pdf, F.document)
async def process_full_material_pdf(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "materials", state=state):
        return
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".pdf"):
        await message.answer("⚠️ يجب أن يكون الملف بصيغة PDF.")
        return

    wait_msg = await message.answer("⏳ جاري استلام ملف الـ PDF...")
    pdf_bytes = await _download_telegram_bytes(message.bot, doc.file_id)
    tmp_pdf = os.path.join(TMP_UPLOAD_DIR, f"up_{secrets.token_hex(6)}.pdf")
    with open(tmp_pdf, "wb") as f:
        f.write(pdf_bytes)
    del pdf_bytes

    await wait_msg.delete()
    await state.update_data(tmp_pdf_path=tmp_pdf, raw_pdf_filename=doc.file_name)
    await state.set_state(AdminMaterialManageStates.waiting_material_title)
    await message.answer("✍️ <b>الخطوة 2/7:</b> أدخل اسم / عنوان المذكرة:", parse_mode="HTML")


@router.message(AdminMaterialManageStates.waiting_material_title)
async def process_full_material_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("⚠️ يرجى كتابة عنوان واضح للمذكرة.")
        return
    await state.update_data(material_title=title)
    await state.set_state(AdminMaterialManageStates.waiting_material_tag)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="فيزياء", callback_data="adm_up_tag:فيزياء"),
            InlineKeyboardButton(text="كيمياء", callback_data="adm_up_tag:كيمياء"),
            InlineKeyboardButton(text="أحياء", callback_data="adm_up_tag:أحياء"),
        ],
        [
            InlineKeyboardButton(text="رياضيات", callback_data="adm_up_tag:رياضيات"),
            InlineKeyboardButton(text="لغة عربية", callback_data="adm_up_tag:لغة عربية"),
            InlineKeyboardButton(text="إنجليزي", callback_data="adm_up_tag:إنجليزي"),
        ],
        [InlineKeyboardButton(text="⏭️ مذكرات عامة", callback_data="adm_up_tag:مذكرات عامة")],
    ])
    await message.answer("🏷️ <b>الخطوة 3/7:</b> اختر التصنيف / المادة أو اكتبه الآن:", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_up_tag:"), AdminMaterialManageStates.waiting_material_tag)
async def pick_full_material_tag(callback: CallbackQuery, state: FSMContext):
    tag = callback.data.split(":", 1)[1]
    await state.update_data(material_tag=tag)
    await state.set_state(AdminMaterialManageStates.waiting_material_price)
    await callback.message.answer(f"🏷️ التصنيف: <b>{html.escape(tag)}</b>\n\n💵 <b>الخطوة 4/7:</b> أدخل السعر بالجنيه (مثال: 120):", parse_mode="HTML")
    await callback.answer()


@router.message(AdminMaterialManageStates.waiting_material_tag)
async def process_full_material_tag(message: Message, state: FSMContext):
    tag = (message.text or "").strip() or "مذكرات عامة"
    await state.update_data(material_tag=tag)
    await state.set_state(AdminMaterialManageStates.waiting_material_price)
    await message.answer("💵 <b>الخطوة 4/7:</b> أدخل سعر المذكرة بالجنيه المصري (مثال: 120):", parse_mode="HTML")


@router.message(AdminMaterialManageStates.waiting_material_price)
async def process_full_material_price(message: Message, state: FSMContext):
    try:
        price = float((message.text or "").strip())
    except ValueError:
        await message.answer("⚠️ يرجى إدخال رقم صحيح للسعر.")
        return
    await state.update_data(material_price=price)
    await state.set_state(AdminMaterialManageStates.waiting_material_description)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ استخدام وصف تلقائي", callback_data="adm_up_desc:auto")]])
    await message.answer("📝 <b>الخطوة 5/7:</b> أدخل وصفاً للمذكرة (اختياري):", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm_up_desc:auto", AdminMaterialManageStates.waiting_material_description)
async def skip_full_material_desc(callback: CallbackQuery, state: FSMContext):
    await state.update_data(material_description=None)
    await state.set_state(AdminMaterialManageStates.waiting_material_cover)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ استخراج الغلاف من الصفحة الأولى", callback_data="adm_up_cover:auto")]])
    await callback.message.answer("🖼️ <b>الخطوة 6/7:</b> أرسل صورة غلاف المذكرة (اختياري):", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminMaterialManageStates.waiting_material_description)
async def process_full_material_desc(message: Message, state: FSMContext):
    await state.update_data(material_description=(message.text or "").strip() or None)
    await state.set_state(AdminMaterialManageStates.waiting_material_cover)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ استخراج الغلاف من الصفحة الأولى", callback_data="adm_up_cover:auto")]])
    await message.answer("🖼️ <b>الخطوة 6/7:</b> أرسل صورة غلاف المذكرة (اختياري):", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm_up_cover:auto", AdminMaterialManageStates.waiting_material_cover)
async def skip_full_material_cover(callback: CallbackQuery, state: FSMContext):
    await state.update_data(tmp_cover_path=None)
    await state.set_state(AdminMaterialManageStates.waiting_material_frame)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ بدون إطار مخصص", callback_data="adm_up_frame:none")]])
    await callback.message.answer("🖼️ <b>الخطوة 7/7:</b> أرسل صورة الإطار/البرواز لدمجه في الصفحات (سيُفرّغ لونه الأبيض تلقائياً) — اختياري:", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AdminMaterialManageStates.waiting_material_cover, F.photo | F.document)
async def process_full_material_cover(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    cov_bytes = await _download_telegram_bytes(message.bot, file_id)
    tmp_cov = os.path.join(TMP_UPLOAD_DIR, f"cov_{secrets.token_hex(6)}.png")
    with open(tmp_cov, "wb") as f:
        f.write(cov_bytes)
    await state.update_data(tmp_cover_path=tmp_cov)
    await state.set_state(AdminMaterialManageStates.waiting_material_frame)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭️ بدون إطار مخصص", callback_data="adm_up_frame:none")]])
    await message.answer("🖼️ <b>الخطوة 7/7:</b> أرسل صورة الإطار/البرواز لدمجه في الصفحات (اختياري):", reply_markup=kb, parse_mode="HTML")


async def _finalize_material_upload(target_msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    tmp_pdf = data.get("tmp_pdf_path")
    tmp_cov = data.get("tmp_cover_path")
    tmp_frm = data.get("tmp_frame_path")

    status_msg = await target_msg.answer("⏳ جاري رفع المذكرة وتجهيز الغلاف والإطار والتنسيق الافتراضي...")
    try:
        with open(tmp_pdf, "rb") as f:
            pdf_bytes = f.read()
        cov_bytes = open(tmp_cov, "rb").read() if tmp_cov and os.path.exists(tmp_cov) else None
        frm_bytes = open(tmp_frm, "rb").read() if tmp_frm and os.path.exists(tmp_frm) else None

        res = await bridge_client.upload_material(
            file_bytes=pdf_bytes,
            filename=data.get("raw_pdf_filename") or "booklet.pdf",
            title=data.get("material_title", "مذكرة"),
            price=float(data.get("material_price", 0)),
            tag=data.get("material_tag"),
            description=data.get("material_description"),
            cover_bytes=cov_bytes,
            frame_bytes=frm_bytes,
        )
    finally:
        _cleanup_temp_path(tmp_pdf)
        _cleanup_temp_path(tmp_cov)
        _cleanup_temp_path(tmp_frm)

    if not res:
        await status_msg.edit_text("⚠️ فشل رفع المذكرة للسيرفر، تأكد من تشغيل المنصة الأساسية.")
        return

    mid = res["id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔤 ضبط الخط والأحجام والموقع", callback_data=f"adm_mat_font:{mid}"),
            InlineKeyboardButton(text="👁️ معاينة صفحة 1", callback_data=f"adm_mat_preview:{mid}"),
        ],
        [InlineKeyboardButton(text="🔙 لوحة الإدارة", callback_data="adm_view:stats")],
    ])
    await status_msg.edit_text(
        f"🎉 <b>تم رفع ونشر المذكرة بنجاح!</b>\n\n"
        f"📖 <b>العنوان:</b> {html.escape(res.get('title', ''))} (<code>#{mid}</code>)\n"
        f"🏷️ <b>التصنيف:</b> {html.escape(res.get('tag') or 'عام')}\n"
        f"💵 <b>السعر:</b> {res.get('price')} ج.م | 📄 <b>الصفحات:</b> {res.get('total_pages')} ص\n"
        f"🖼️ <b>الإطار:</b> {'مُدمج بشفافية ✅' if res.get('page_frame_path') else 'بدون إطار'}\n\n"
        f"أصبحت متاحة للطلب والختم عبر البوت والـ Excel!",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm_up_frame:none", AdminMaterialManageStates.waiting_material_frame)
async def skip_full_material_frame(callback: CallbackQuery, state: FSMContext):
    await state.update_data(tmp_frame_path=None)
    await callback.answer("جاري الرفع والنشر...")
    await _finalize_material_upload(callback.message, state)


@router.message(AdminMaterialManageStates.waiting_material_frame, F.photo | F.document)
async def process_full_material_frame(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    fr_bytes = await _download_telegram_bytes(message.bot, file_id)
    tmp_frm = os.path.join(TMP_UPLOAD_DIR, f"frm_{secrets.token_hex(6)}.png")
    with open(tmp_frm, "wb") as f:
        f.write(fr_bytes)
    await state.update_data(tmp_frame_path=tmp_frm)
    await _finalize_material_upload(message, state)


# ================================================================
# 4. رفع وتغيير وحذف إطار الصفحات لأي مذكرة قائمة
# ================================================================

async def _prompt_attach_frame_to_material(message: Message, state: FSMContext, raw_bytes: bytes, filename: str):
    tmp_frm = os.path.join(TMP_UPLOAD_DIR, f"idle_frm_{secrets.token_hex(6)}.png")
    with open(tmp_frm, "wb") as f:
        f.write(raw_bytes)
    materials = await bridge_client.get_materials(include_unpublished=True)
    usable = filter_usable_materials(materials)
    if not usable:
        _cleanup_temp_path(tmp_frm)
        await message.answer("⚠️ لا توجد مذكرات في النظام لتركيب الإطار عليها.")
        return
    await state.update_data(pending_idle_frame_path=tmp_frm, pending_idle_frame_name=filename)
    rows = [
        [InlineKeyboardButton(text=f"🖼️ تركيب على: {m['title']}", callback_data=f"adm_apply_idle_frame:{m['id']}")]
        for m in usable[:45]
    ]
    rows.append([InlineKeyboardButton(text="❌ إلغاء", callback_data="adm_cancel_idle_frame")])
    await message.answer(
        "🖼️ <b>تم اكتشاف صورة إطار! اختر المذكرة التي تريد تركيب هذا الإطار الشفاف عليها:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm_cancel_idle_frame")
async def cancel_idle_frame(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    _cleanup_temp_path(data.get("pending_idle_frame_path"))
    await state.clear()
    await callback.message.edit_text("❌ تم إلغاء تركيب الإطار.")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_apply_idle_frame:"))
async def apply_idle_frame_to_mat(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    mat_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    tmp_frm = data.get("pending_idle_frame_path")
    await state.clear()
    if not tmp_frm or not os.path.exists(tmp_frm):
        await callback.answer("انتهت صلاحية صورة الإطار المؤقتة", show_alert=True)
        return

    try:
        with open(tmp_frm, "rb") as f:
            fr_bytes = f.read()
        res = await bridge_client.upload_material_frame(mat_id, fr_bytes, data.get("pending_idle_frame_name") or "frame.png")
    finally:
        _cleanup_temp_path(tmp_frm)

    if not res:
        await callback.message.answer("⚠️ فشل تركيب الإطار على المذكرة.")
        await callback.answer()
        return

    await callback.answer("تم تركيب الإطار ✅")
    png = await bridge_client.get_preview_page_png(mat_id, page=1, stamped=1, zoom=1.2)
    if png:
        await callback.message.answer_photo(
            photo=BufferedInputFile(png, filename=f"preview_frame_{mat_id}.png"),
            caption=f"✅ <b>تم تركيب الإطار الشفاف بنجاح على:</b> {html.escape(res.get('title', ''))}",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("adm_mat_frame:"))
async def handle_mat_frame_start(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    mat_id = int(callback.data.split(":")[1])
    await state.set_state(AdminMaterialManageStates.waiting_frame_image)
    await state.update_data(target_material_id=mat_id)
    await callback.message.answer(
        f"🖼️ <b>رفع / تغيير إطار الصفحات للمذكرة <code>#{mat_id}</code>:</b>\n\n"
        "👉 أرسل الآن صورة الإطار (PNG أو JPG) وسيتم تفريغ خلفيتها البيضاء تلقائياً وحفظها على المذكرة!",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminMaterialManageStates.waiting_frame_image, F.photo | F.document)
async def process_mat_frame_upload(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "studio_fonts", state=state):
        return
    data = await state.get_data()
    mat_id = int(data.get("target_material_id"))
    await state.clear()

    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    fr_bytes = await _download_telegram_bytes(message.bot, file_id)
    wait_msg = await message.answer("⏳ جاري تفريغ الخلفية البيضاء وتركيب الإطار ومعاينة الصفحة الأولى...")
    res = await bridge_client.upload_material_frame(mat_id, fr_bytes, "frame.png")
    if not res:
        await wait_msg.edit_text("⚠️ تعذر حفظ الإطار على السيرفر.")
        return

    png = await bridge_client.get_preview_page_png(mat_id, page=1, stamped=1, zoom=1.2)
    await wait_msg.delete()
    if png:
        await message.answer_photo(
            photo=BufferedInputFile(png, filename=f"frame_{mat_id}.png"),
            caption=f"✅ <b>تم تركيب الإطار بنجاح على:</b> {html.escape(res.get('title', ''))}",
            parse_mode="HTML",
        )
    else:
        await message.answer(f"✅ تم تركيب الإطار بنجاح على المذكرة #{mat_id}.")


@router.callback_query(F.data.startswith("adm_mat_frame_del:"))
async def handle_mat_frame_delete(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    mat_id = int(callback.data.split(":")[1])
    ok = await bridge_client.delete_material_frame(mat_id)
    if ok:
        await callback.answer("تمت إزالة الإطار 🧹", show_alert=True)
        await callback.message.answer(f"🧹 تمت إزالة الإطار عن المذكرة #{mat_id}.")
    else:
        await callback.answer("تعذر إزالة الإطار", show_alert=True)


# ================================================================
# 5. إعدادات الخطوط والأحجام والإحداثيات + معاينة فورية (Font & Layout Studio in Bot)
# ================================================================

def _build_font_studio_text_and_kb(
    layout_payload: Dict[str, Any],
    selected_target: str = "all",
    preview_page: int = 1,
) -> tuple[str, InlineKeyboardMarkup]:
    mat = layout_payload.get("material", {})
    mat_id = mat.get("id")
    title = mat.get("title", "مذكرة")
    elements = layout_payload.get("stamp_elements", [])
    fonts = layout_payload.get("fonts", [])

    target_labels = {
        "all": "كل العناصر",
        "name": "اسم المدرس",
        "phone": "رقم الهاتف",
        "doc_name": "اسم السلسلة",
        "watermark": "العلامة المائية",
    }

    el_lines = []
    for el in elements:
        et = el.get("type", "?")
        lbl = target_labels.get(et, et)
        marker = "👉 " if (selected_target == et or selected_target == "all") else "   "
        el_lines.append(
            f"{marker}<b>{lbl}:</b> خط <code>{el.get('font_family', 'cairo')}</code> | "
            f"حجم <code>{el.get('font_size', 14)}</code> | X=<code>{el.get('x_pct', 50)}%</code>, Y=<code>{el.get('y_pct', 50)}%</code>"
        )

    text = (
        f"🔤 <b>استوديو الخط والأحجام والمواقع: {html.escape(str(title))}</b> (<code>#{mat_id}</code>)\n"
        f"🎯 <b>المستهدف حالياً:</b> <code>{target_labels.get(selected_target, selected_target)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(el_lines) +
        "\n\n<i>أي تغيير يتم تطبيقه فوراً في قاعدة البيانات مع تحديث صورة المعاينة!</i>"
    )

    tgt_row1 = []
    for t_key, t_short in [("all", "الكل"), ("name", "الاسم"), ("phone", "الهاتف"), ("doc_name", "السلسلة"), ("watermark", "المائية")]:
        prefix = "🔘 " if selected_target == t_key else ""
        tgt_row1.append(InlineKeyboardButton(text=f"{prefix}{t_short}", callback_data=f"adm_fnt_tgt:{mat_id}:{t_key}:{preview_page}"))

    font_rows = []
    cur_row = []
    for f in fonts[:8]:
        fshort = f["label"].split("(")[0].strip()
        cur_row.append(InlineKeyboardButton(
            text=f"✒️ {fshort}",
            callback_data=f"adm_fnt_fam:{mat_id}:{selected_target}:{f['key']}:{preview_page}",
        ))
        if len(cur_row) == 2:
            font_rows.append(cur_row)
            cur_row = []
    if cur_row:
        font_rows.append(cur_row)

    rows = [
        tgt_row1[:3],
        tgt_row1[3:],
        [
            InlineKeyboardButton(text="➖ تصغير (-2)", callback_data=f"adm_fnt_adj:{mat_id}:{selected_target}:fsz:-2:{preview_page}"),
            InlineKeyboardButton(text="🔢 حجم مخصص", callback_data=f"adm_fnt_asksz:{mat_id}:{selected_target}:{preview_page}"),
            InlineKeyboardButton(text="➕ تكبير (+2)", callback_data=f"adm_fnt_adj:{mat_id}:{selected_target}:fsz:+2:{preview_page}"),
        ],
        [
            InlineKeyboardButton(text="⬆️ أعلى", callback_data=f"adm_fnt_adj:{mat_id}:{selected_target}:dy:-2:{preview_page}"),
            InlineKeyboardButton(text="⬇️ أسفل", callback_data=f"adm_fnt_adj:{mat_id}:{selected_target}:dy:+2:{preview_page}"),
            InlineKeyboardButton(text="⬅️ يسار", callback_data=f"adm_fnt_adj:{mat_id}:{selected_target}:dx:-2:{preview_page}"),
            InlineKeyboardButton(text="➡️ يمين", callback_data=f"adm_fnt_adj:{mat_id}:{selected_target}:dx:+2:{preview_page}"),
        ],
        *font_rows,
        [
            InlineKeyboardButton(text="🖼️ تغيير إطار المذكرة", callback_data=f"adm_mat_frame:{mat_id}"),
            InlineKeyboardButton(text="👁️ تحديث المعاينة", callback_data=f"adm_mat_preview:{mat_id}:{preview_page}"),
        ],
    ]

    miniapp_url = bot_config.get_live_miniapp_url(cache_bust=True)
    if miniapp_url.lower().startswith("https://"):
        sep = "&" if "?" in miniapp_url else "?"
        studio_url = f"{miniapp_url}{sep}tab=admin&studio_mat={mat_id}"
        rows.append([InlineKeyboardButton(text="📱 فتح استوديو الإحداثيات بالسحب على الموبايل", web_app=WebAppInfo(url=studio_url))])

    rows.append([InlineKeyboardButton(text="🔙 رجوع لتفاصيل المذكرة", callback_data=f"adm_mat_sel:{mat_id}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_or_refresh_studio_preview(
    bot: Bot,
    chat_id: int,
    mat_id: int,
    selected_target: str = "all",
    preview_page: int = 1,
    edit_message: Optional[Message] = None,
) -> None:
    layout_data = await bridge_client.get_material_layout(mat_id)
    if not layout_data:
        await bot.send_message(chat_id, "⚠️ تعذر تحميل تنسيق المذكرة.")
        return

    text, kb = _build_font_studio_text_and_kb(layout_data, selected_target, preview_page)
    png_bytes = await bridge_client.get_preview_page_png(mat_id, page=preview_page, stamped=1, zoom=1.2)

    if png_bytes:
        media = InputMediaPhoto(
            media=BufferedInputFile(png_bytes, filename=f"studio_{mat_id}_p{preview_page}.png"),
            caption=text,
            parse_mode="HTML",
        )
        if edit_message and edit_message.photo:
            try:
                await edit_message.edit_media(media=media, reply_markup=kb)
                return
            except Exception:
                pass
        await bot.send_photo(chat_id=chat_id, photo=media.media, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_mat_dl_orig:"))
async def handle_mat_download_original(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "materials"):
        return
    mat_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ جاري سحب الملف الأصلي من السيرفر...")
    res = await bridge_client.download_material_original(mat_id)
    if not res or not res.get("bytes"):
        await callback.message.answer("❌ تعذر تحميل الملف الأصلي لهذه المذكرة من السيرفر.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 رفع ملف PDF بديل لهذه المذكرة", callback_data=f"adm_mat_replace_pdf:{mat_id}")],
        [InlineKeyboardButton(text="🔙 رجوع لتفاصيل المذكرة", callback_data=f"adm_mat_sel:{mat_id}")],
    ])
    await callback.message.answer_document(
        document=BufferedInputFile(res["bytes"], filename=res["filename"]),
        caption=(
            f"📥 <b>الملف الأصلي للمذكرة (#{mat_id})</b>\n"
            "بعد إصلاح الملف أو تعديله تقدر تضغط على <b>📄 رفع ملف PDF بديل</b> ليتم استبداله مع الحفاظ على نفس البيانات وأماكن وأحجام الأختام."
        ),
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm_mat_replace_pdf:"))
async def handle_mat_replace_pdf_start(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "materials"):
        return
    mat_id = int(callback.data.split(":")[1])
    materials = await bridge_client.get_materials(include_unpublished=True)
    mat = next((m for m in materials if int(m.get("id", 0)) == mat_id), None)
    mat_title = mat.get("title") if mat else f"#{mat_id}"

    await state.set_state(AdminMaterialManageStates.waiting_replace_pdf)
    await state.update_data(replace_mat_id=mat_id, replace_mat_title=mat_title)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء الاستبدال", callback_data=f"adm_mat_replace_cancel:{mat_id}")],
    ])
    await callback.message.answer(
        f"📄 <b>استبدال ملف الـ PDF للمذكرة:</b> <code>{html.escape(str(mat_title))}</code> (#{mat_id})\n\n"
        "• أرسل الآن <b>ملف الـ PDF الجديد</b> هنا كـ Document.\n"
        "• سيتم استبدال الملف فقط مع الحفاظ على:\n"
        "  ✅ نفس الاسم والسعر والوصف والمادة\n"
        "  ✅ نفس أماكن وأحجام وخطوط الأختام المحفوظة\n"
        "  ✅ نفس الغلاف وإطار الصفحات",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_mat_replace_cancel:"))
async def handle_mat_replace_pdf_cancel(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "materials"):
        return
    await state.clear()
    mat_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        f"✅ تم إلغاء استبدال الملف للمذكرة #{mat_id}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 رجوع لتفاصيل المذكرة", callback_data=f"adm_mat_sel:{mat_id}")]
        ]),
    )
    await callback.answer()


@router.message(AdminMaterialManageStates.waiting_replace_pdf)
async def handle_mat_replace_pdf_receive(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "materials", state=state):
        return

    data = await state.get_data()
    mat_id = int(data.get("replace_mat_id") or 0)
    mat_title = data.get("replace_mat_title") or f"#{mat_id}"

    if message.text and message.text.strip().lower() in ("إلغاء", "الغاء", "cancel", "/cancel"):
        await state.clear()
        await message.answer("✅ تم إلغاء عملية استبدال ملف الـ PDF.")
        return

    if not message.document:
        await message.answer("⚠️ يرجى إرسال ملف بصيغة <b>PDF</b> كـ Document أو كتابة <code>إلغاء</code>.", parse_mode="HTML")
        return

    filename = message.document.file_name or "booklet.pdf"
    if not filename.lower().endswith(".pdf"):
        await message.answer("⚠️ يجب أن يكون الملف المرفوع بصيغة <code>.pdf</code> فقط.", parse_mode="HTML")
        return

    if not mat_id:
        await state.clear()
        await message.answer("⚠️ انتهت الجلسة، يرجى إعادة اختيار المذكرة.")
        return

    wait_msg = await message.answer("⏳ جاري تنزيل ملف الـ PDF الجديد ومعالجته وضغطه مع الحفاظ على كافة الأختام...")
    try:
        pdf_bytes = await _download_telegram_bytes(message.bot, message.document.file_id)
        res = await bridge_client.replace_material_pdf(mat_id, pdf_bytes, filename=filename)
    except Exception as e:
        logger.exception("Error downloading/replacing material PDF via Telegram")
        await wait_msg.edit_text(f"❌ حدث خطأ أثناء استبدال الملف: {html.escape(str(e))}", parse_mode="HTML")
        return

    if not res.get("ok"):
        await wait_msg.edit_text(
            f"❌ تعذر استبدال الملف: {html.escape(str(res.get('error') or 'خطأ غير معروف'))}",
            parse_mode="HTML",
        )
        return

    await state.clear()
    updated = res.get("data") or {}
    total_pages = updated.get("total_pages", "؟")
    actual_title = updated.get("title") or mat_title

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👁️ معاينة صفحة 1 بالأختام", callback_data=f"adm_mat_preview:{mat_id}"),
            InlineKeyboardButton(text="🔤 استوديو الخطوط", callback_data=f"adm_mat_font:{mat_id}"),
        ],
        [InlineKeyboardButton(text="🔙 تفاصيل المذكرة", callback_data=f"adm_mat_sel:{mat_id}")],
    ])
    await wait_msg.edit_text(
        f"✅ <b>تم استبدال ملف الـ PDF بنجاح!</b>\n"
        f"📘 المذكرة: <b>{html.escape(str(actual_title))}</b> (#{mat_id})\n"
        f"📄 عدد الصفحات الجديد: <b>{total_pages}</b> صفحة\n\n"
        "🔒 تم الحفاظ على نفس البيانات (الاسم/السعر/الوصف/الإطار) ونفس أماكن وأحجام الأختام المحفوظة.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm_view:font_settings")
async def handle_font_settings_pick_material(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    materials = await bridge_client.get_materials(include_unpublished=True)
    usable = filter_usable_materials(materials)
    if not usable:
        await callback.message.answer("📭 لا توجد مذكرات مسجلة حالياً.")
        await callback.answer()
        return

    rows = [
        [InlineKeyboardButton(text=f"🔤 {m['title']} (#{m['id']})", callback_data=f"adm_mat_font:{m['id']}")]
        for m in usable[:45]
    ]
    rows.append([InlineKeyboardButton(text="🔙 رجوع للوحة التحكم", callback_data="adm_view:stats")])
    await callback.message.answer(
        "🔤 <b>إعدادات الخطوط والأحجام والإحداثيات:</b>\n"
        "اختر المذكرة التي تريد تعديل خطها أو حجم أختامها أو موقعها:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_mat_font:"))
async def handle_open_font_studio(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    mat_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ جاري تحميل المعاينة والتنسيق...")
    await _send_or_refresh_studio_preview(callback.bot, callback.message.chat.id, mat_id, "all", 1)


@router.callback_query(F.data.startswith("adm_mat_preview:"))
async def handle_material_preview_only(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, ("studio_fonts", "materials")):
        return
    parts = callback.data.split(":")
    mat_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    await callback.answer("⏳ جاري توليد معاينة الصفحة...")
    png = await bridge_client.get_preview_page_png(mat_id, page=page, stamped=1, zoom=1.25)
    if png:
        await callback.message.answer_photo(
            photo=BufferedInputFile(png, filename=f"preview_{mat_id}_p{page}.png"),
            caption=f"👁️ <b>معاينة المذكرة #{mat_id} — صفحة {page}</b>",
            parse_mode="HTML",
        )
    else:
        await callback.message.answer("⚠️ تعذر توليد المعاينة.")


@router.callback_query(F.data.startswith("adm_fnt_tgt:"))
async def handle_studio_switch_target(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    _, mid, tgt, pg = callback.data.split(":")
    await callback.answer(f"المستهدف: {tgt}")
    await _send_or_refresh_studio_preview(
        callback.bot, callback.message.chat.id, int(mid), tgt, int(pg), edit_message=callback.message
    )


@router.callback_query(F.data.startswith("adm_fnt_fam:"))
async def handle_studio_change_font_family(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    _, mid, tgt, fam, pg = callback.data.split(":")
    await callback.answer("⏳ جاري تطبيق الخط وتحديث المعاينة...")
    await bridge_client.quick_font_adjust(int(mid), {"target_type": tgt, "font_family": fam})
    await _send_or_refresh_studio_preview(
        callback.bot, callback.message.chat.id, int(mid), tgt, int(pg), edit_message=callback.message
    )


@router.callback_query(F.data.startswith("adm_fnt_adj:"))
async def handle_studio_quick_adjust(callback: CallbackQuery):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    _, mid, tgt, op, val_str, pg = callback.data.split(":")
    val = float(val_str)
    payload: Dict[str, Any] = {"target_type": tgt}
    if op == "fsz":
        payload["font_size_delta"] = val
    elif op == "dx":
        payload["dx_pct"] = val
    elif op == "dy":
        payload["dy_pct"] = val

    await callback.answer("⏳ جاري التعديل والتحديث التلقائي...")
    await bridge_client.quick_font_adjust(int(mid), payload)
    await _send_or_refresh_studio_preview(
        callback.bot, callback.message.chat.id, int(mid), tgt, int(pg), edit_message=callback.message
    )


@router.callback_query(F.data.startswith("adm_fnt_asksz:"))
async def handle_studio_ask_custom_size(callback: CallbackQuery, state: FSMContext):
    if not await check_admin_perm(callback, bot_config, "studio_fonts"):
        return
    _, mid, tgt, pg = callback.data.split(":")
    await state.set_state(AdminFontSettingsStates.waiting_custom_font_size)
    await state.update_data(font_mat_id=int(mid), font_target=tgt, font_page=int(pg))
    await callback.message.answer(
        f"🔢 <b>أدخل الآن حجم الخط الجديد بالأرقام</b> (مثلاً: <code>14</code> أو <code>18</code> أو <code>32</code> للعناوين):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFontSettingsStates.waiting_custom_font_size)
async def process_studio_custom_size(message: Message, state: FSMContext):
    if not await check_admin_perm(message, bot_config, "studio_fonts", state=state):
        return
    try:
        sz = float((message.text or "").strip())
    except ValueError:
        await message.answer("⚠️ يرجى إدخال رقم صحيح (مثال: 16).")
        return

    data = await state.get_data()
    mid = int(data.get("font_mat_id"))
    tgt = str(data.get("font_target", "all"))
    pg = int(data.get("font_page", 1))
    await state.clear()

    await bridge_client.quick_font_adjust(mid, {"target_type": tgt, "font_size": sz})
    await message.answer(f"✅ تم تعديل حجم الخط إلى <code>{sz}</code>! جاري إرسال المعاينة التلقائية...", parse_mode="HTML")
    await _send_or_refresh_studio_preview(message.bot, message.chat.id, mid, tgt, pg)

