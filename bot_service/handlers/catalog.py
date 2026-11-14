# ================================================================
# bot_service/handlers/catalog.py — استعراض كتالوج المذكرات والبطاقات
# ================================================================

import html
import json
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot_service.config import bot_config
import stamp_settings
from bot_service.services.bridge_client import bridge_client
from bot_service.services.batch_parser import filter_usable_materials
from bot_service.keyboards.inline import get_materials_keyboard, get_material_actions_keyboard
from bot_service.states.order_flow import BundleCartStates

logger = logging.getLogger("bot_service.handlers.catalog")
router = Router(name="catalog_router")


async def render_catalog_view(target, page: int = 1, tag: str = None, edit: bool = False):
    """توليد واستعراض كتالوج المذكرات مع دعم الفرز والصفحات"""
    materials = filter_usable_materials(await bridge_client.get_materials(tag=tag if tag != "all" else None))
    
    # استخراج قائمة كل المواد المتاحة لبناء الفلاتر
    all_materials = filter_usable_materials(await bridge_client.get_materials())
    all_tags = sorted(list({m.get("tag") for m in all_materials if m.get("tag")}))

    if not materials:
        text = "📭 **لا توجد مذكرات منشورة حالياً في هذا القسم.**"
        if edit and isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=get_materials_keyboard([], 1, 4, tag, all_tags))
        else:
            await target.answer(text, reply_markup=get_materials_keyboard([], 1, 4, tag, all_tags))
        return

    text = (
        f"📚 <b>كتالوج المذكرات التعليمية المعتمدة</b> 🎓\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"اختر المذكرة التي ترغب بها لعرض التفاصيل وتخصيص أختامك:\n"
        f"<i>(إجمالي المذكرات المتوفرة: {len(materials)} مذكرة)</i>"
    )

    kb = get_materials_keyboard(
        materials=materials,
        current_page=page,
        page_size=4,
        selected_tag=tag if tag != "all" else None,
        all_tags=all_tags
    )

    if edit and isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "📚 تصفح المذكرات والطلب")
@router.message(Command("catalog"))
async def handle_catalog_command(message: Message):
    await render_catalog_view(message, page=1, tag=None, edit=False)


@router.callback_query(F.data.startswith("cat_page:"))
async def handle_catalog_page(callback: CallbackQuery):
    page_num = int(callback.data.split(":")[1])
    await render_catalog_view(callback, page=page_num, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("cat_filter:"))
async def handle_catalog_filter(callback: CallbackQuery):
    tag = callback.data.split(":")[1]
    await render_catalog_view(callback, page=1, tag=tag, edit=True)
    await callback.answer()


@router.callback_query(F.data == "catalog_refresh")
async def handle_catalog_refresh(callback: CallbackQuery):
    await render_catalog_view(callback, page=1, tag=None, edit=True)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("mat_detail:"))
async def handle_material_detail(callback: CallbackQuery):
    """عرض بطاقة تفاصيل المذكرة الفردية"""
    mat_id = int(callback.data.split(":")[1])
    materials = await bridge_client.get_materials()
    mat = next((m for m in materials if m["id"] == mat_id), None)

    if not mat:
        await callback.answer("المذكرة غير متوفرة أو تم تعديلها", show_alert=True)
        return

    title = mat.get("title")
    tag = mat.get("tag") or "عام"
    price = mat.get("price")
    pages = mat.get("total_pages")
    desc = mat.get("description") or "مذكرة دراسية متميزة تحتوي على شرح وتدريبات نموذجية."

    detail_text = (
        f"📖 **{title}**\n\n"
        f"🔬 **المادة / القسم:** {tag}\n"
        f"📄 **عدد الصفحات:** {pages} صفحة (جودة متجهة Vector فائقة الوضوح)\n"
        f"💵 **السعر المطلوب:** `{price}` ج.م فقط\n"
        f"🛡️ **مستوى الأمان:** حماية ضد التسريب + علامات مائية مشفرة\n\n"
        f"📝 **الوصف والملخص:**\n{desc}\n\n"
        f"اضغط على الزر بالأسفل لبدء تخصيص الأختام باسمك وهاتفك 👇"
    )

    await callback.message.edit_text(
        text=detail_text,
        reply_markup=get_material_actions_keyboard(mat_id),
        parse_mode="Markdown"
    )
    await callback.answer()


# ================================================================
# سلة شراء عدة مذكرات معاً ببيانات ختم موحدة وصورة تحويل
# ================================================================

def _build_bundle_picker_kb(materials: list, selected_ids: list) -> InlineKeyboardMarkup:
    rows = []
    total_egp = 0.0
    for m in materials[:25]:
        mid = int(m["id"])
        price = float(m.get("price", 0))
        checked = mid in selected_ids
        if checked:
            total_egp += price
        mark = "✅" if checked else "⬜"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {m.get('title', 'مذكرة')} — ({price:.0f} ج.م)",
                callback_data=f"bnd_toggle:{mid}",
            )
        ])

    if selected_ids:
        rows.append([
            InlineKeyboardButton(
                text=f"⚡ إتمام شراء السلة ({len(selected_ids)} مذكرات | {total_egp:.0f} ج.م)",
                callback_data="bnd_checkout_go",
            )
        ])
        rows.append([
            InlineKeyboardButton(text="🗑️ تفريغ الاختيار", callback_data="bnd_clear_all")
        ])

    rows.append([InlineKeyboardButton(text="❌ إغلاق السلة", callback_data="bnd_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "🛒 شراء عدة مذكرات معاً")
@router.message(Command("bundle"))
async def handle_bundle_cart_start(message: Message, state: FSMContext):
    materials = await bridge_client.get_materials()
    if not materials:
        await message.answer("📭 لا توجد مذكرات متاحة حالياً بالمتجر.")
        return

    await state.set_state(BundleCartStates.selecting_items)
    await state.update_data(bundle_selected_ids=[])
    await message.answer(
        "🛒 <b>سلة الشراء المجمعة لعدة مذكرات معاً</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "اضغط على المذكرات التي ترغب بشرائها معاً لتحديدها بعلامة (✅)، ثم اضغط على <b>إتمام شراء السلة</b> بالأسفل 👇",
        reply_markup=_build_bundle_picker_kb(materials, []),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("bnd_toggle:"))
async def handle_bundle_toggle(callback: CallbackQuery, state: FSMContext):
    mid = int(callback.data.split(":")[1])
    data = await state.get_data()
    sel = list(data.get("bundle_selected_ids", []))
    if mid in sel:
        sel.remove(mid)
    else:
        sel.append(mid)
    await state.update_data(bundle_selected_ids=sel)
    materials = await bridge_client.get_materials()
    try:
        await callback.message.edit_reply_markup(reply_markup=_build_bundle_picker_kb(materials, sel))
    except Exception:
        pass
    await callback.answer(f"في السلة: {len(sel)} مذكرات")


@router.callback_query(F.data == "bnd_clear_all")
async def handle_bundle_clear(callback: CallbackQuery, state: FSMContext):
    await state.update_data(bundle_selected_ids=[])
    materials = await bridge_client.get_materials()
    try:
        await callback.message.edit_reply_markup(reply_markup=_build_bundle_picker_kb(materials, []))
    except Exception:
        pass
    await callback.answer("تم تفريغ السلة")


@router.callback_query(F.data == "bnd_cancel")
async def handle_bundle_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("تم الإلغاء")


@router.callback_query(F.data == "bnd_checkout_go")
async def handle_bundle_checkout_go(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sel = list(data.get("bundle_selected_ids", []))
    if not sel:
        await callback.answer("اختر مذكرة واحدة على الأقل أولاً", show_alert=True)
        return

    profile = await bridge_client.get_teacher_profile(callback.from_user.id)
    is_admin = bot_config.is_admin(callback.from_user.id)
    if profile and (is_admin or stamp_settings.has_any_available_stamp_data(profile)):
        await state.update_data(
            bnd_stamp_name=profile.get("teacher_name") or "",
            bnd_stamp_phone=profile.get("primary_phone") or "",
        )
        await state.set_state(BundleCartStates.waiting_from_wallet)
        materials = await bridge_client.get_materials()
        chosen = [m for m in materials if int(m["id"]) in sel]
        total = sum(float(m.get("price", 0)) for m in chosen)
        await callback.message.answer(
            f"✅ <b>تم اعتماد بيانات ختمك المحفوظة تلقائياً:</b>\n"
            f"👤 {html.escape(profile.get('teacher_name') or 'بدون اسم')} — <code>{html.escape(profile.get('primary_phone') or 'بدون رقم')}</code>\n\n"
            f"💳 <b>إجمالي السلة المطلوب تحويله ({len(chosen)} مذكرات):</b> <code>{total:.0f}</code> ج.م\n"
            f"  • فودافون كاش: <code>{bot_config.PAYMENT_WALLET_NUMBER}</code> أو <code>{bot_config.PAYMENT_WALLET_NUMBER_2}</code>\n"
            f"  • إنستاباي (رقم الهاتف): <code>{bot_config.INSTAPAY_ADDRESS}</code>\n\n"
            f"👉 <b>أرسل الآن الرقم الذي قمت بالتحويل منه:</b>",
            parse_mode="HTML",
        )
    else:
        await state.set_state(BundleCartStates.waiting_stamp_name)
        await callback.message.answer(
            "✍️ <b>أدخل اسم المدرس المراد طباعته بالختم على جميع المذكرات المختارة:</b>\n"
            "<i>(أو أرسل كلمة <code>تخطي</code> للاكتفاء برقم الهاتف، وللأدمن يمكن تخطي كل الحقول)</i>",
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(BundleCartStates.waiting_stamp_name)
async def process_bnd_stamp_name(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if raw in ("تخطي", "تخطي ⏩", "-", "بدون"):
        stamp_name = ""
    elif not is_admin and len(raw) < stamp_settings.MIN_STAMP_VALUE_LENGTH_WHEN_PRESENT:
        await message.answer("⚠️ يرجى إدخال اسم المدرس بشكل صحيح أو إرسال كلمة <code>تخطي</code>.", parse_mode="HTML")
        return
    else:
        stamp_name = raw
    await state.update_data(bnd_stamp_name=stamp_name)
    await state.set_state(BundleCartStates.waiting_stamp_phone)
    await message.answer(
        "📱 <b>أدخل رقم هاتف المدرس للختم (واتساب):</b>\n"
        "<i>(أو أرسل كلمة <code>تخطي</code>)</i>",
        parse_mode="HTML",
    )


@router.message(BundleCartStates.waiting_stamp_phone)
async def process_bnd_stamp_phone(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if raw in ("تخطي", "تخطي ⏩", "-", "بدون"):
        stamp_phone = ""
    elif not is_admin and len(raw) < 5:
        await message.answer("⚠️ يرجى إدخال رقم هاتف صحيح أو إرسال كلمة <code>تخطي</code>.", parse_mode="HTML")
        return
    else:
        stamp_phone = raw

    data = await state.get_data()
    stamp_name = (data.get("bnd_stamp_name") or "").strip()
    ok_stamp, err_msg = stamp_settings.validate_stamp_fields(
        buyer_name_stamp=stamp_name,
        buyer_phone_stamp=stamp_phone,
        is_admin=is_admin,
    )
    if not ok_stamp:
        await state.set_state(BundleCartStates.waiting_stamp_name)
        await message.answer(
            f"⚠️ {err_msg or stamp_settings.MISSING_STAMP_DATA_MESSAGE_AR}\n\n"
            f"✍️ <b>أدخل اسم المدرس المراد طباعته بالختم</b> (أو أرسل <code>تخطي</code> لإدخال رقم الهاتف):",
            parse_mode="HTML",
        )
        return

    await state.update_data(bnd_stamp_phone=stamp_phone)
    sel = list(data.get("bundle_selected_ids", []))
    materials = await bridge_client.get_materials()
    chosen = [m for m in materials if int(m["id"]) in sel]
    total = sum(float(m.get("price", 0)) for m in chosen)
    await state.set_state(BundleCartStates.waiting_from_wallet)
    await message.answer(
        f"💳 <b>إجمالي السلة ({len(chosen)} مذكرات):</b> <code>{total:.0f}</code> ج.م\n"
        f"  • فودافون كاش: <code>{bot_config.PAYMENT_WALLET_NUMBER}</code> أو <code>{bot_config.PAYMENT_WALLET_NUMBER_2}</code>\n"
        f"  • إنستاباي (رقم الهاتف): <code>{bot_config.INSTAPAY_ADDRESS}</code>\n\n"
        f"👉 <b>أرسل الآن الرقم الذي قمت بالتحويل منه:</b>",
        parse_mode="HTML",
    )


@router.message(BundleCartStates.waiting_from_wallet)
async def process_bnd_from_wallet(message: Message, state: FSMContext):
    is_admin = bot_config.is_admin(message.from_user.id)
    raw = (message.text or "").strip()
    if is_admin and raw in ("تخطي", "تخطي ⏩", "-", "بدون", ""):
        from_wallet = ""
    elif not is_admin and len(raw) < 5:
        await message.answer("⚠️ أرسل رقم المحفظة أو الحساب المحول منه.")
        return
    else:
        from_wallet = raw
    await state.update_data(bnd_from_wallet=from_wallet)
    await state.set_state(BundleCartStates.waiting_receipt_photo)
    await message.answer(
        "📸 <b>أرسل الآن صورة إيصال التحويل (Screenshot)</b> لتأكيد السلة فوراً\n"
        "<i>(أو أرسل كلمة <code>تخطي</code> للمطابقة التلقائية عبر رادار الـ SMS):</i>",
        parse_mode="HTML",
    )


@router.message(BundleCartStates.waiting_receipt_photo)
async def process_bnd_receipt_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    sel = list(data.get("bundle_selected_ids", []))
    stamp_name = (data.get("bnd_stamp_name") or "").strip()
    stamp_phone = (data.get("bnd_stamp_phone") or "").strip()
    from_wallet = (data.get("bnd_from_wallet") or stamp_phone or "").strip()
    is_admin = bot_config.is_admin(message.from_user.id)

    receipt_bytes = None
    if message.photo:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        bio = await bot.download_file(file_info.file_path)
        receipt_bytes = bio.read()

    await state.clear()
    wait_msg = await message.answer("⏳ جاري تسجيل السلة المجمعة ومطابقة التحويل...")

    fields = {
        "material_ids_json": json.dumps(sel),
        "buyer_name_stamp": stamp_name,
        "buyer_phone_stamp": stamp_phone,
        "payment_mode": "receipt_pending",
        "from_wallet_phone": from_wallet,
        "to_wallet_phone": bot_config.PAYMENT_WALLET_NUMBER,
        "telegram_user_id": str(message.from_user.id),
        "telegram_chat_id": str(message.chat.id),
        "is_admin": "true" if is_admin else "false",
    }

    res = await bridge_client.checkout_bundle_multipart(fields, receipt_bytes=receipt_bytes)
    if not res:
        await wait_msg.edit_text("⚠️ تعذر تسجيل السلة حالياً، يرجى المحاولة مرة أخرى.")
        return

    await wait_msg.edit_text(
        f"🎉 <b>{html.escape(res.get('message', 'تم استلام سلة طلباتك بنجاح!'))}</b>\n\n"
        f"🔖 <b>كود السلة المجمعة:</b> <code>{html.escape(str(res.get('bundle_code', '')))}</code>\n"
        f"📚 <b>عدد المذكرات:</b> <code>{res.get('pdfs_count', len(sel))}</code> مذكرات\n"
        f"📩 ستصلك جميع ملفات الـ PDF المختومة هنا في المحادثة تلقائياً فور اكتمال التجهيز!",
        parse_mode="HTML",
    )


