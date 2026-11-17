# ================================================================
# bot_service/services/batch_parser.py
# محلل ملفات الإكسيل والوورد والنصوص السريعة ومولد القوالب الجاهزة
# ================================================================

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

import stamp_settings


ARABIC_DIGITS_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_arabic_text(text: str) -> str:
    """توحيد الحروف العربية وتنظيف المسافات والتشكيل لمطابقة دقيقة."""
    if not text:
        return ""
    s = str(text).translate(ARABIC_DIGITS_MAP).strip().lower()
    # إزالة التشكيل والرموز الزائدة
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)
    s = re.sub(r"[أإآ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"[^\w\s\u0600-\u06FF]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_phone_value(raw: Any) -> str:
    """تنظيف رقم الهاتف وإرجاع الصفر البادئ المفقود من خلايا الإكسيل الرقمية."""
    if raw is None:
        return ""
    # معالجة أرقام Excel مثل 1012345678.0
    s = str(raw).strip()
    if s.endswith(".0") and s[:-2].replace(".", "").isdigit():
        s = s[:-2]
    s = s.translate(ARABIC_DIGITS_MAP)
    digits = re.sub(r"\D+", "", s)
    if not digits:
        return ""
    if digits.startswith("0020") and len(digits) >= 14:
        digits = "0" + digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits


_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "teacher_name": (
        "اسم المدرس", "الاسم", "اسم المعلم", "المدرس", "المعلم", "الاسم كامل",
        "اسم الطباعه", "teacher name", "teacher", "name", "full name", "instructor",
    ),
    "phone": (
        "رقم الهاتف", "الهاتف", "الموبايل", "التليفون", "تليفون", "هاتف 1",
        "رقم الهاتف 1", "رقم الموبايل", "الرقم", "موبايل", "phone", "phone 1",
        "mobile", "primary phone", "tel", "whatsapp", "واتساب",
    ),
    "phone_2": (
        "هاتف 2", "رقم الهاتف 2", "هاتف اضافي", "رقم اضافي", "رقم بديل",
        "موبايل 2", "phone 2", "secondary phone", "alt phone",
    ),
    "material_ref": (
        "المذكره", "اسم المذكره", "الماده", "الملف", "كود المذكره", "رقم المذكره",
        "اسم الملف", "الكتاب", "booklet", "material", "material name", "material id",
        "book", "subject",
    ),
    "series_name": (
        "السلسله", "اسم السلسله", "اسم السلسه", "عنوان السلسله",
        "series", "series name", "doc name", "doc_name",
    ),
    "watermark_text": (
        "العلامه المائيه", "نص العلامه المائيه", "علامه مائيه", "ملاحظات الختم",
        "watermark", "watermark text", "watermark_text",
    ),
    "watermark_type": (
        "نوع العلامه المائيه", "نوع العلامه", "watermark type", "watermark_type",
    ),
    "font_family": (
        "الخط", "نوع الخط", "font", "font family", "font_family",
    ),
    "font_size": (
        "حجم الخط", "حجم", "font size", "font_size", "size",
    ),
    "material_description": (
        "وصف المذكره", "الوصف", "وصف", "التفاصيل", "تفاصيل المذكره",
        "description", "desc", "booklet description", "material description",
    ),
    "material_price": (
        "سعر المذكره", "السعر", "سعر", "سعر الجنيه", "price", "egp price", "material price",
    ),
    "material_price_coins": (
        "سعر الكوينز", "كوينز المذكره", "سعر العملات", "عملات المذكره",
        "price_coins", "price coins", "booklet coins",
    ),
    "material_tag": (
        "التصنيف", "القسم", "الشعبه", "تاج", "التاج", "category", "tag",
    ),
    "add_coins": (
        "شحن كوينز", "اضافه كوينز", "اضافة كوينز", "شحن رصيد", "اضافه رصيد",
        "كوينز مضافه", "العملات المضافه", "add_coins", "add coins", "recharge",
    ),
    "user_email": (
        "الايميل", "البريد", "البريد الالكتروني", "email", "mail", "user email",
    ),
    "user_id": (
        "معرف المستخدم", "رقم المستخدم", "user_id", "user id",
    ),
    "coins_note": (
        "ملاحظه الشحن", "سبب الشحن", "السبب", "ملاحظه", "note", "reason",
    ),
}

_NORMALIZED_HEADER_LOOKUP: Dict[str, str] = {}
for _canonical, _aliases in _HEADER_ALIASES.items():
    for _alias in _aliases:
        _NORMALIZED_HEADER_LOOKUP[normalize_arabic_text(_alias)] = _canonical


_SKIP_VALUE_TOKENS = {
    "-", "—", "–", "--", "n/a", "na", "none", "null", "skip",
    "بدون", "بدون رقم", "لا يوجد", "غير متوفر", "تخطي", "تخطي ⏩", "اختياري",
}


_LOGO_WATERMARK_TOKENS = {
    "لوجو", "لوقو", "شعار", "صوره", "لوجو صوره", "صوره لوجو",
    "علامه مائيه صوره", "علامه صوره", "لوجو المدرس", "شعار المدرس",
    "صوره المدرس", "صوره علامه مائيه", "لوجو/صوره", "صوره/لوجو",
    "logo", "image", "img", "pic", "picture", "photo",
    "watermark logo", "teacher logo", "logo watermark", "image watermark",
}


def is_logo_watermark_token(val: Any) -> bool:
    """التحقق مما إذا كانت قيمة الخلية تشير إلى طلب علامة مائية بصورة (لوجو)."""
    s = str(val or "").strip()
    if not s:
        return False
    norm = normalize_arabic_text(s)
    if norm in _LOGO_WATERMARK_TOKENS:
        return True
    tokens = set(norm.split())
    allowed = {
        "لوجو", "لوقو", "شعار", "صوره", "علامه", "مائيه", "المدرس",
        "logo", "image", "img", "pic", "picture", "photo", "teacher", "watermark",
    }
    if tokens and tokens.issubset(allowed):
        return any(t in tokens for t in ("لوجو", "لوقو", "شعار", "صوره", "logo", "image", "img", "pic", "picture", "photo"))
    return False


def _normalize_watermark_entry_fields(entry: Dict[str, Any]) -> None:
    """توحيد حقول العلامة المائية في عنصر الدفعة لدعم اختيار (لوجو/صورة) بجانب النصوص."""
    wm_type_raw = entry.get("watermark_type")
    wm_text_raw = entry.get("watermark_text")

    if is_logo_watermark_token(wm_type_raw) or is_logo_watermark_token(wm_text_raw):
        entry["watermark_type"] = "logo"
        entry.pop("watermark_text", None)
        return

    if wm_type_raw is not None:
        wt_norm = normalize_arabic_text(str(wm_type_raw))
        if wt_norm in ("نص", "كتابه", "text"):
            entry["watermark_type"] = "text"
        elif wt_norm in ("بدون", "لا", "none"):
            entry["watermark_type"] = "none"
            entry.pop("watermark_text", None)
        else:
            entry["watermark_type"] = str(wm_type_raw).strip().lower()


def _is_skip_token(val: Any) -> bool:
    s = str(val or "").strip()
    if not s:
        return True
    s_low = s.lower()
    return s in _SKIP_VALUE_TOKENS or s_low in _SKIP_VALUE_TOKENS


def _map_header_cell(cell_val: Any, allow_fuzzy: bool = True) -> Optional[str]:
    norm = normalize_arabic_text(str(cell_val or ""))
    if not norm:
        return None
    if norm in _NORMALIZED_HEADER_LOOKUP:
        return _NORMALIZED_HEADER_LOOKUP[norm]
    # إزالة كلمات اختياري / أساسي / مطلوب في نهاية اسم العمود للمطابقة الدقيقة
    cleaned = re.sub(r"\s*[\(\[]?(?:اختياري|اساسي|مطلوب|optional)[\)\]]?\s*$", "", norm).strip()
    if cleaned and cleaned in _NORMALIZED_HEADER_LOOKUP:
        return _NORMALIZED_HEADER_LOOKUP[cleaned]
    if not allow_fuzzy:
        return None
    if _looks_like_phone(str(cell_val or "")) or len(norm) < 4 or len(norm) > 28 or len(norm.split()) > 3:
        return None
    # منع اعتبار قيم البيانات الفعلية (مثل: "مذكرة الرياضيات" أو "سلسلة التفوق" أو "مستر محمد") كصف عناوين
    if norm.startswith((
        "مذكره ", "سلسله ", "كتاب ", "ملزمه ",
        "مستر ", "استاذ ", "استاذه ", "دكتور ", "دكتوره ",
        "مهندس ", "مهندسه ", "باشمهندس ", "كابتن ", "كوتش ",
        "شيخ ", "ابله ", "معلم ", "تيتشر ", "مس ", "ميس ", "مدرس ",
    )):
        return None
    for alias_norm, canonical in _NORMALIZED_HEADER_LOOKUP.items():
        if len(alias_norm) >= 4 and alias_norm in norm:
            return canonical
    return None


def _looks_like_phone(raw_val: Any) -> bool:
    """التحقق السريع مما إذا كان النص يمثل رقم هاتف (وليس اسم مدرس أو اسم مذكرة)."""
    s = str(raw_val or "").strip().translate(ARABIC_DIGITS_MAP)
    if not s or any(ch.isalpha() for ch in s):
        return False
    digits = re.sub(r"\D+", "", s)
    return len(digits) >= 7


def _row_to_entry(
    col_map: Dict[int, str],
    row_values: List[Any],
    is_admin: bool = False,
) -> Optional[Dict[str, Any]]:
    entry: Dict[str, Any] = {}
    for idx, field in col_map.items():
        if idx >= len(row_values):
            continue
        val = row_values[idx]
        if val is None:
            continue
        sval = str(val).strip()
        if not sval or _is_skip_token(sval):
            continue
        if field in ("phone", "phone_2"):
            if field == "phone" and "@" in sval and "user_email" not in entry:
                entry["user_email"] = sval
                continue
            norm_p = normalize_phone_value(val)
            if norm_p:
                entry[field] = norm_p
        elif field in ("font_size", "material_price", "material_price_coins", "add_coins"):
            try:
                num_str = re.sub(r"[^\d.\-]", "", str(val).translate(ARABIC_DIGITS_MAP).strip())
                if num_str:
                    entry[field] = float(num_str)
            except ValueError:
                pass
        elif field == "user_id":
            try:
                num_str = str(val).translate(ARABIC_DIGITS_MAP).strip()
                if num_str.endswith(".0"):
                    num_str = num_str[:-2]
                if num_str.isdigit():
                    entry[field] = int(num_str)
            except ValueError:
                pass
        elif field == "font_family":
            entry[field] = sval.lower()
        else:
            entry[field] = sval

    if not entry:
        return None

    _normalize_watermark_entry_fields(entry)

    # التحقق الموحد عبر stamp_settings.py:
    # - للأدمن: كل بيانات الختم اختيارية بالكامل ولا يُوسم أي صف كناقص.
    # - لغير الأدمن: يُقبل أي بيان متاح (الاسم، الهاتف، المذكرة/السلسلة، إلخ).
    if not (is_admin and stamp_settings.ADMIN_ALL_STAMP_FIELDS_OPTIONAL):
        ok_stamp, _ = stamp_settings.validate_stamp_payload(entry, is_admin=is_admin)
        if not ok_stamp and not stamp_settings.has_any_available_stamp_data(entry):
            entry["_incomplete"] = True
    return entry


def _infer_positional_col_map(row: List[Any]) -> Dict[int, str]:
    """استنتاج ذكي لترتيب الأعمدة في الملفات أو السطور التي لا تحتوي على صف عناوين، بدون إسقاط الصف الأول إن خلا من رقم الهاتف."""
    cleaned = [("" if _is_skip_token(c) else str(c or "").strip()) for c in row]
    last_non_empty = max((i for i, v in enumerate(cleaned) if v), default=-1)
    if last_non_empty < 0:
        return {}
    trimmed = cleaned[:last_non_empty + 1]
    if len(trimmed) == 1:
        c0 = trimmed[0]
        if _looks_like_phone(c0):
            return {0: "phone"}
        if normalize_arabic_text(c0).startswith(("سلسله ", "مذكره ", "كتاب ", "ملزمه ")):
            return {0: "series_name"}
        return {0: "teacher_name"}

    c0, c1 = trimmed[0], trimmed[1]
    c0_is_phone = bool(c0 and _looks_like_phone(c0))
    c1_is_phone = bool(c1 and _looks_like_phone(c1))

    if len(trimmed) == 2:
        if not c1:
            return {0: "phone" if c0_is_phone else "teacher_name"}
        if c1_is_phone:
            return {0: "teacher_name", 1: "phone"}
        second_clean = c1.translate(ARABIC_DIGITS_MAP).strip()
        second_field = "material_ref" if (second_clean.isdigit() and len(second_clean) < 7) else "series_name"
        return {0: "phone" if c0_is_phone else "teacher_name", 1: second_field}

    if c1 and not c1_is_phone:
        if len(trimmed) >= 3 and trimmed[2] and _looks_like_phone(trimmed[2]):
            return {0: "teacher_name", 1: "series_name", 2: "phone", 3: "watermark_text", 4: "material_ref", 5: "phone_2"}
        return {0: "teacher_name", 1: "material_ref", 2: "series_name", 3: "watermark_text", 4: "phone_2"}

    return {0: "teacher_name", 1: "phone", 2: "material_ref", 3: "series_name", 4: "watermark_text", 5: "phone_2"}


def _reconcile_ambiguous_coin_headers(col_map: Dict[int, str], row: List[Any], is_coin_sheet: bool = False) -> Dict[int, str]:
    """تمييز عمود 'الكوينز' تلقائياً بين سعر المذكرة (price_coins) وشحن محفظة المستخدم (add_coins)."""
    updated = dict(col_map)
    has_material_context = any(v in ("material_ref", "material_description", "series_name") for v in updated.values())
    for idx, cell in enumerate(row):
        norm = normalize_arabic_text(str(cell or ""))
        if norm in ("كوينز", "الكوينز", "coins"):
            if is_coin_sheet or not has_material_context:
                updated[idx] = "add_coins"
            elif "material_price_coins" not in updated.values():
                updated[idx] = "material_price_coins"
    return updated


def _is_pure_coin_recharge_row(entry: Dict[str, Any]) -> bool:
    """التحقق مما إذا كان الصف خاصاً بشحن الكوينز فقط دون بيانات مذكرة أو ختم."""
    has_coin = entry.get("add_coins") is not None
    has_stamp_or_mat = any(
        entry.get(k)
        for k in ("material_ref", "series_name", "watermark_text", "watermark_logo_path", "material_description", "material_price", "material_price_coins")
    )
    return bool(has_coin and not has_stamp_or_mat)


def _extract_from_matrix(
    rows: List[List[Any]],
    is_admin: bool = False,
    is_coin_sheet: bool = False,
) -> List[Dict[str, Any]]:
    """استخراج قائمة المدرسين من مصفوفة صفوف (تُستخدم للإكسيل وجداول الوورد وCSV)."""
    if not rows:
        return []

    header_row_idx = -1
    best_col_map: Dict[int, str] = {}
    single_col_fallback: Dict[int, str] = {}
    single_col_row_idx = -1

    # فحص أول 10 صفوف للعثور على صف العناوين الفعلي (يفضل متعدد الأعمدة، ثم يقبل عموداً واحداً صريحاً فقط)
    for r_idx, row in enumerate(rows[:10]):
        # إذا احتوى الصف على رقم هاتف، فهو صف بيانات مؤكد وليس صف عناوين
        if any(_looks_like_phone(str(c or "")) for c in row if c is not None and str(c).strip()):
            continue
        candidate_map: Dict[int, str] = {}
        exact_matches = 0
        non_empty_cells = 0
        for c_idx, cell in enumerate(row):
            if cell is not None and str(cell).strip():
                non_empty_cells += 1
            mapped = _map_header_cell(cell)
            if mapped and mapped not in candidate_map.values():
                candidate_map[c_idx] = mapped
                if _map_header_cell(cell, allow_fuzzy=False) is not None:
                    exact_matches += 1
        candidate_map = _reconcile_ambiguous_coin_headers(candidate_map, list(row), is_coin_sheet=is_coin_sheet)
        has_stamp_col = any(
            k in candidate_map.values()
            for k in (
                "teacher_name", "phone", "phone_2", "material_ref", "series_name",
                "watermark_text", "material_description", "material_price",
                "material_price_coins", "add_coins", "user_email", "user_id",
            )
        )
        if len(candidate_map) >= 2 and has_stamp_col:
            best_col_map = candidate_map
            header_row_idx = r_idx
            break
        if (
            not single_col_fallback
            and len(candidate_map) == 1
            and has_stamp_col
            and exact_matches == 1
            and non_empty_cells == 1
        ):
            single_col_fallback = candidate_map
            single_col_row_idx = r_idx

    if not best_col_map and single_col_fallback:
        best_col_map = single_col_fallback
        header_row_idx = single_col_row_idx

    # خطة بديلة إذا لم تُكتب عناوين أعمدة واضحة: الاستنتاج الذكي لكل صف بدون رفض أي صف بدون رقم
    if not best_col_map:
        first_non_empty = next((i for i, r in enumerate(rows) if any(str(c or "").strip() for c in r)), -1)
        if first_non_empty == -1:
            return []
        entries: List[Dict[str, Any]] = []
        for row in rows[first_non_empty:]:
            if not any(str(c or "").strip() for c in row):
                continue
            inferred_map = _infer_positional_col_map(list(row))
            if not inferred_map:
                continue
            item = _row_to_entry(inferred_map, list(row), is_admin=is_admin)
            if item:
                entries.append(item)
        return entries

    start_idx = header_row_idx + 1
    entries: List[Dict[str, Any]] = []
    for row in rows[start_idx:]:
        if not any(str(c or "").strip() for c in row):
            continue
        item = _row_to_entry(best_col_map, row, is_admin=is_admin)
        if item:
            entries.append(item)
    return entries


_COIN_SHEET_KEYWORDS = ("شحن الكوينز", "اضافه كوينز", "اضافة كوينز", "شحن رصيد", "كوينز", "coins")


def parse_excel_bytes(file_bytes: bytes, is_admin: bool = False) -> List[Dict[str, Any]]:
    """استخراج بيانات المدرسين والمذكرات من ملف Excel (.xlsx)."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    all_entries: List[Dict[str, Any]] = []
    try:
        for sheet_name in wb.sheetnames:
            norm_sheet = normalize_arabic_text(sheet_name)
            if any(skip in norm_sheet for skip in ("تعليمات", "instruction", "دليل", "notes")):
                continue
            if any(cs in norm_sheet for cs in ("شحن الكوينز", "اضافه كوينز", "شحن رصيد", "coins")):
                continue
            ws = wb[sheet_name]
            matrix = [list(row) for row in ws.iter_rows(values_only=True)]
            parsed = _extract_from_matrix(matrix, is_admin=is_admin, is_coin_sheet=False)
            for p in parsed:
                if not _is_pure_coin_recharge_row(p):
                    all_entries.append(p)
    finally:
        wb.close()
    return all_entries


def parse_csv_bytes(file_bytes: bytes, is_admin: bool = False) -> List[Dict[str, Any]]:
    """استخراج بيانات المدرسين من ملف CSV."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return []
    reader = csv.reader(io.StringIO(text))
    matrix = [row for row in reader]
    return _extract_from_matrix(matrix, is_admin=is_admin)


def parse_text_teacher_entries(raw_text: str, is_admin: bool = False) -> List[Dict[str, Any]]:
    """
    تحليل نصوص المدرسين المرسلة مباشرة أو المستخرجة من فقرات الوورد:
    1) أسلوب الكتل:
       الاسم: ...
       الهاتف: ...
       المذكرة: ...
    2) أسلوب السطور المفصولة بـ | أو ؛ أو Tab:
       أحمد علي | 01012345678 | مذكرة الفيزياء | سلسلة التفوق
       أو: أحمد علي | سلسلة التفوق (الاسم مع اسم المذكرة بدون رقم)
    3) سطر واحد منفرد: الاسم فقط، أو الرقم فقط، أو اسم السلسلة/المذكرة فقط.
    """
    lines = [ln.strip() for ln in (raw_text or "").splitlines()]
    entries: List[Dict[str, Any]] = []
    current_block: Dict[str, Any] = {}

    def _flush_block():
        nonlocal current_block
        if current_block:
            _normalize_watermark_entry_fields(current_block)
            if not (is_admin and stamp_settings.ADMIN_ALL_STAMP_FIELDS_OPTIONAL):
                ok_stamp, _ = stamp_settings.validate_stamp_payload(current_block, is_admin=is_admin)
                if not ok_stamp and not stamp_settings.has_any_available_stamp_data(current_block):
                    current_block["_incomplete"] = True
            entries.append(current_block)
        current_block = {}

    for line in lines:
        if not line or re.fullmatch(r"[-_=*]{3,}", line):
            _flush_block()
            continue

        # 1. التحقق من صيغة "مفتاح: قيمة"
        if ":" in line:
            k_raw, v_raw = line.split(":", 1)
            mapped = _map_header_cell(k_raw)
            if mapped:
                val = v_raw.strip()
                if mapped in ("phone", "phone_2"):
                    val = normalize_phone_value(val)
                elif mapped in ("font_size", "material_price", "material_price_coins", "add_coins"):
                    try:
                        num_str = re.sub(r"[^\d.\-]", "", val.translate(ARABIC_DIGITS_MAP).strip())
                        val = float(num_str) if num_str else None
                    except ValueError:
                        val = None
                elif mapped == "user_id":
                    num_str = val.translate(ARABIC_DIGITS_MAP).strip()
                    val = int(num_str) if num_str.isdigit() else None
                if val is not None and val != "":
                    # لو الحقل اتكرر في نفس الكتلة نبدأ عنصراً جديداً
                    if mapped in current_block and stamp_settings.has_any_available_stamp_data(current_block):
                        _flush_block()
                    current_block[mapped] = val
                continue

        # 2. التحقق من سطر مفصول بـ | أو ؛ أو Tab
        sep = "|" if "|" in line else ("؛" if "؛" in line else ("\t" if "\t" in line else None))
        if sep:
            parts = [p.strip() for p in line.split(sep)]
            if len(parts) >= 2:
                _flush_block()
                # تجاهل سطر العناوين الصريح فقط إن وُجد (مثل: الاسم | الهاتف | المذكرة)
                non_empty_parts = [p for p in parts if p]
                if (
                    len(non_empty_parts) >= 2
                    and all(_map_header_cell(p, allow_fuzzy=False) is not None for p in non_empty_parts)
                    and not any(_looks_like_phone(p) for p in non_empty_parts)
                ):
                    continue
                cleaned_parts = ["" if _is_skip_token(p) else p for p in parts]
                col_map = _infer_positional_col_map(cleaned_parts)
                item = _row_to_entry(col_map, cleaned_parts, is_admin=is_admin) if col_map else None
                if item:
                    entries.append(item)
                continue

        # 3. سطر واحد منفرد بدون فواصل (اسم فقط أو رقم فقط أو اسم سلسلة/مذكرة)
        _flush_block()
        if _is_skip_token(line):
            continue
        if _looks_like_phone(line):
            entries.append({"phone": normalize_phone_value(line)})
        elif normalize_arabic_text(line).startswith(("سلسله ", "مذكره ", "كتاب ", "ملزمه ")):
            entries.append({"series_name": line})
        else:
            entries.append({"teacher_name": line})

    _flush_block()
    return entries


def parse_word_bytes(file_bytes: bytes, is_admin: bool = False) -> List[Dict[str, Any]]:
    """استخراج بيانات المدرسين من ملف Word (.docx) عبر الجداول أو الفقرات المنظمة."""
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    entries: List[Dict[str, Any]] = []

    for table in doc.tables:
        matrix = []
        for row in table.rows:
            matrix.append([cell.text.strip() for cell in row.cells])
        parsed = _extract_from_matrix(matrix, is_admin=is_admin)
        if parsed:
            entries.extend(parsed)

    if entries:
        return entries

    # لم يُعثر على جدول بيانات؛ نحاول قراءة فقرات النص المنظم (الاسم: / الهاتف: ...)
    full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return parse_text_teacher_entries(full_text, is_admin=is_admin)


def parse_uploaded_batch_file(
    filename: str,
    file_bytes: bytes,
    is_admin: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """تحديد نوع الملف وتحليله وإرجاع (قائمة المدرسين, نوع الملف المقروء)."""
    if field_list_placeholder := False:
        pass
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in (filename or "") else ""
    if ext == ".xlsx":
        return parse_excel_bytes(file_bytes, is_admin=is_admin), "Excel (.xlsx)"
    if ext == ".csv":
        return [r for r in parse_csv_bytes(file_bytes, is_admin=is_admin) if not _is_pure_coin_recharge_row(r)], "CSV (.csv)"
    if ext == ".docx":
        return [r for r in parse_word_bytes(file_bytes, is_admin=is_admin) if not _is_pure_coin_recharge_row(r)], "Word (.docx)"
    if ext == ".txt":
        text = file_bytes.decode("utf-8-sig", errors="replace")
        return [r for r in parse_text_teacher_entries(text, is_admin=is_admin) if not _is_pure_coin_recharge_row(r)], "Text (.txt)"
    raise ValueError(f"صيغة الملف غير مدعومة ({ext or 'unknown'}). استخدم .xlsx أو .docx أو .csv")


def parse_coins_recharges_from_file(filename: str, file_bytes: bytes, is_admin: bool = True) -> List[Dict[str, Any]]:
    """استخراج طلبات شحن الكوينز للمستخدمين من ملف Excel أو Word أو CSV أو نص."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in (filename or "") else ""
    rows: List[Dict[str, Any]] = []

    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        try:
            for sheet_name in wb.sheetnames:
                norm_sheet = normalize_arabic_text(sheet_name)
                if any(skip in norm_sheet for skip in ("تعليمات", "instruction", "دليل", "notes")):
                    continue
                is_coin_sh = any(cs in norm_sheet for cs in ("شحن الكوينز", "اضافه كوينز", "شحن رصيد", "coins"))
                ws = wb[sheet_name]
                matrix = [list(row) for row in ws.iter_rows(values_only=True)]
                parsed = _extract_from_matrix(matrix, is_admin=is_admin, is_coin_sheet=is_coin_sh)
                rows.extend(parsed)
        finally:
            wb.close()
    elif ext == ".csv":
        rows = parse_csv_bytes(file_bytes, is_admin=is_admin)
    elif ext == ".docx":
        rows = parse_word_bytes(file_bytes, is_admin=is_admin)
    elif ext == ".txt":
        rows = parse_text_teacher_entries(file_bytes.decode("utf-8-sig", errors="replace"), is_admin=is_admin)

    coin_items: List[Dict[str, Any]] = []
    for r in rows:
        amt = r.get("add_coins")
        if amt is None:
            continue
        try:
            amt_val = float(amt)
        except (TypeError, ValueError):
            continue
        if amt_val <= 0:
            continue
        has_ident = any(r.get(k) for k in ("phone", "user_email", "user_id", "teacher_name"))
        if not has_ident:
            continue
        coin_items.append({
            "phone": r.get("phone"),
            "user_email": r.get("user_email"),
            "user_id": r.get("user_id"),
            "teacher_name": r.get("teacher_name"),
            "add_coins": amt_val,
            "coins_note": r.get("coins_note"),
        })
    return coin_items


def parse_coins_recharges_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """استخراج طلبات شحن الكوينز من النص المباشر."""
    rows = parse_text_teacher_entries(raw_text, is_admin=True)
    return [
        {
            "phone": r.get("phone"),
            "user_email": r.get("user_email"),
            "user_id": r.get("user_id"),
            "teacher_name": r.get("teacher_name"),
            "add_coins": float(r["add_coins"]),
            "coins_note": r.get("coins_note"),
        }
        for r in rows
        if r.get("add_coins") is not None and float(r.get("add_coins") or 0) > 0
        and any(r.get(k) for k in ("phone", "user_email", "user_id", "teacher_name"))
    ]


_UPDATE_TAG_PATTERNS = (
    "اخر تعديل", "اخر تحديث", "قريبا", "جديد", "جديده", "تحديث", "محدث", "تعديل جديد", "قيد الانتظار",
)

_GRADE_WORDS = {
    "اول": 1, "الاول": 1, "اولي": 1, "الاولي": 1,
    "ثاني": 2, "الثاني": 2, "تاني": 2, "التاني": 2, "ثانيه": 2, "تانيه": 2, "الثانيه": 2, "التانيه": 2,
    "ثالث": 3, "الثالث": 3, "تالت": 3, "التالت": 3, "ثالثه": 3, "تالته": 3, "الثالثه": 3, "التالته": 3,
    "رابع": 4, "الرابع": 4, "رابعه": 4, "الرابعه": 4,
    "خامس": 5, "الخامس": 5, "خامسه": 5, "الخامسه": 5,
    "سادس": 6, "السادس": 6, "سادسه": 6, "السادسه": 6,
}
_STAGE_WORDS = {
    "ابتدايي": "primary", "الابتدايي": "primary", "ابتدائي": "primary", "الابتدائي": "primary", "ابتداءي": "primary",
    "اعدادي": "prep", "الاعدادي": "prep", "اعداديه": "prep",
    "ثانوي": "secondary", "الثانوي": "secondary", "ثانويه": "secondary", "الثانويه": "secondary",
    "بكالوريا": "bacc", "البكالوريا": "bacc",
}
# أنواع المذكرات: عبارات (بعد التطبيع) -> نوع
_TYPE_PHRASES = (
    ("مراجعه نهائيه", "final_review"),
    ("المراجعه النهائيه", "final_review"),
    ("مراجعه نوفمبر", "nov_review"),
    ("كراسه تقييم", "eval_booklet"),
    ("اوائل الطلبه", "top_students"),
    ("اوايل الطلبه", "top_students"),
    ("امتحانات", "exams"),
    ("امتحان", "exams"),
    ("تقييمات", "evals"),
    ("تقييم", "evals"),
    ("توجيه", "guidance"),
    ("شرح", "explain"),
    ("بدون نظري", "no_theory"),
    ("بدون اجابات", "no_answers"),
    ("بالاجابات", "with_answers"),
)
_STOP_TOKENS = {"الصف", "صف", "ال", "في", "من", "و", "مذكره", "مذكرة", "للصف"}


def has_update_or_waiting_tag(text: Any) -> bool:
    """هل الاسم يحتوي على وسم تحديث/انتظار مثل ( اخر تعديل ) أو (قريباً) أو (جديد)؟"""
    norm = f" {normalize_arabic_text(str(text or ''))} "
    return any(f" {pat} " in norm for pat in _UPDATE_TAG_PATTERNS)


def material_title_signature(text: Any) -> Dict[str, Any]:
    """يستخرج (الصف، المرحلة، نوع المذكرة، باقي الكلمات المميزة) من اسم المذكرة."""
    norm = normalize_arabic_text(str(text or ""))
    types: set = set()
    work = f" {norm} "
    for phrase, kind in sorted(_TYPE_PHRASES, key=lambda x: -len(x[0])):
        if phrase and f" {phrase} " in work:
            types.add(kind)
            work = work.replace(f" {phrase} ", " ")
    grades: set = set()
    stages: set = set()
    residual: List[str] = []
    for tok in work.split():
        if tok in _STAGE_WORDS:
            stages.add(_STAGE_WORDS[tok])
        elif tok in _GRADE_WORDS:
            grades.add(_GRADE_WORDS[tok])
        elif tok in _STOP_TOKENS:
            continue
        else:
            residual.append(tok)
    return {
        "grades": frozenset(grades),
        "stages": frozenset(stages),
        "types": frozenset(types),
        "residual": tuple(sorted(residual)),
    }


def match_material_by_ref(
    ref: Any,
    materials: List[Dict[str, Any]],
    allow_unique_partial: bool = False,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    مطابقة صارمة للمذكرة (بدون أي تشابه تقريبي Fuzzy):
      1) رقم ID مباشر.
      2) تطابق تام للاسم بعد تطبيع بسيط (أ/إ/آ→ا، ة→ه، ى→ي، تنظيف المسافات).
      3) تطابق بنفس البصمة (الصف + المرحلة + النوع + باقي الكلمات) — فقط إذا خلا الاسم من وسوم
         التحديث/الانتظار مثل ( اخر تعديل ) وكانت البصمة كاملة ومحددة بمذكرة واحدة فقط.
    أي اسم لا يطابق يُرجع (None, "not_found") ليذهب لمسار قائمة الانتظار / التشويق.
    `allow_unique_partial` للبحث اليدوي بالأدمن فقط (احتواء نصي بشرط وجود نتيجة وحيدة).
    """
    if ref is None or not str(ref).strip() or not materials:
        return None, "missing"

    raw = str(ref).strip().translate(ARABIC_DIGITS_MAP)
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]

    # 1. مطابقة برقم المعرف ID
    if raw.isdigit():
        mid = int(raw)
        for m in materials:
            if int(m.get("id", -1)) == mid:
                return m, "exact_id"
        return None, "not_found"

    norm_ref = normalize_arabic_text(raw)
    if not norm_ref:
        return None, "missing"

    # 2. تطابق تام بعد التطبيع (يفضّل المذكرة الجاهزة إن تكررت)
    exact = [m for m in materials if normalize_arabic_text(m.get("title", "")) == norm_ref]
    if exact:
        exact.sort(key=lambda m: (not bool(m.get("has_pdf", m.get("file_exists", True))), int(m.get("id", 0))))
        return exact[0], "exact_title"

    # الأسماء الموسومة بـ ( اخر تعديل ) / (قريباً) / (جديد) لا تُربط بأي مذكرة قديمة
    if has_update_or_waiting_tag(raw):
        return None, "update_tag"

    # 3. تطابق البصمة الكاملة (صف + مرحلة + نوع + كلمات مميزة)
    sig = material_title_signature(raw)
    if len(sig["grades"]) == 1 and len(sig["stages"]) == 1 and sig["types"]:
        sig_matches = [
            m for m in materials
            if not has_update_or_waiting_tag(m.get("title", ""))
            and material_title_signature(m.get("title", "")) == sig
        ]
        if len(sig_matches) == 1:
            return sig_matches[0], "signature_title"
        if len(sig_matches) > 1:
            sig_matches.sort(key=lambda m: (not bool(m.get("has_pdf", m.get("file_exists", True))), int(m.get("id", 0))))
            return sig_matches[0], "signature_title"

    # 4. للبحث اليدوي فقط: احتواء نصي بنتيجة وحيدة وبنفس الصف/المرحلة
    if allow_unique_partial:
        cands = []
        for m in materials:
            nt = normalize_arabic_text(m.get("title", ""))
            if nt and (norm_ref in nt or nt in norm_ref):
                msig = material_title_signature(m.get("title", ""))
                if msig["grades"] == sig["grades"] and msig["stages"] == sig["stages"] and msig["types"] == sig["types"]:
                    cands.append(m)
        if len(cands) == 1:
            return cands[0], "partial_title"

    return None, "not_found"


def build_excel_template_bytes(materials: Optional[List[Dict[str, Any]]] = None) -> bytes:
    """إنشاء ملف نموذج Excel جاهز للتنزيل وإدخال بيانات المدرسين بسهولة من الموبايل أو اللاب."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "المدرسين"
    ws.views.sheetView[0].rightToLeft = True

    headers = [
        "اسم المدرس",         # متاح/اختياري للأدمن
        "رقم الهاتف",         # متاح/اختياري للأدمن
        "المذكرة",            # اسم المذكرة (إن لم تكن مرفوعة تُضاف كـ قريباً/تشويق)
        "وصف المذكرة",        # اختياري — يكفي كتابته مع أول مدرس فقط
        "السعر",              # اختياري — إذا تُرك فارغاً يأخذ سعر المنصة الحالي
        "سعر الكوينز",        # اختياري — سعر المذكرة بالكوينز
        "اسم السلسلة",        # متاح/اختياري
        "العلامة المائية",    # اختياري
        "هاتف 2",             # اختياري
        "الخط",               # اختياري
        "حجم الخط",           # اختياري
    ]

    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(name="Cairo", size=11, bold=True, color="38BDF8")
    req_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")

    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = req_fill if col_idx <= 3 else header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    first_mat_title = materials[0]["title"] if materials else "اسم المذكرة أو رقمها"
    sample_rows = [
        ["د. أحمد محمد علي", "01012345678", first_mat_title, "", "", "", "سلسلة التفوق", "نسخة خاصة بالمدرس", "", "cairo", ""],
        ["أ. سارة محمود", "01198765432", first_mat_title, "", "", "", "", "", "01200001111", "", ""],
    ]

    for r_offset, row_vals in enumerate(sample_rows, start=2):
        for c_idx, val in enumerate(row_vals, start=1):
            c = ws.cell(row=r_offset, column=c_idx, value=val)
            # تنسيق أعمدة الهواتف كنص للحفاظ على الصفر البادئ في الإكسيل
            if c_idx in (2, 9):
                c.number_format = "@"
            c.alignment = Alignment(horizontal="center", vertical="center")

    widths = [24, 18, 28, 32, 14, 15, 22, 24, 18, 12, 12]
    for idx, w in enumerate(widths, start=1):
        col_letter = openpyxl.utils.get_column_letter(idx)
        ws.column_dimensions[col_letter].width = w

    # ورقة ثانية: شحن الكوينز الجماعي للمستخدمين
    ws_coins = wb.create_sheet("شحن الكوينز")
    ws_coins.views.sheetView[0].rightToLeft = True
    coin_headers = ["رقم الهاتف", "الإيميل", "الاسم (اختياري)", "شحن كوينز", "سبب الشحن"]
    for col_idx, h in enumerate(coin_headers, start=1):
        cell = ws_coins.cell(row=1, column=col_idx, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for idx, w in enumerate([18, 28, 24, 16, 30], start=1):
        ws_coins.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = w

    # ورقة ثالثة بالتعليمات والمذكرات المتوفرة حالياً
    ws_info = wb.create_sheet("تعليمات")
    ws_info.views.sheetView[0].rightToLeft = True
    info_lines = [
        ("مرونة البيانات", "يكفي إدخال أي بيانات متوفرة (الاسم + الرقم، الاسم + السلسلة، الاسم فقط، المذكرة/السلسلة فقط، أو الرقم فقط) — وللأدمن كل الحقول اختيارية بالكامل"),
        ("حجز مذكرة قادمة (تشويق)", "لو كتبت اسم مذكرة جديدة لم تُرفع بعد، تُنشأ تلقائياً كـ (قريباً / تشويق) على الموقع، ويُحفظ المدرسون في قائمة الانتظار حتى ترفع الـ PDF وتضبط الأختام"),
        ("الوصف والسعر لمرة واحدة", "اكتب وصف المذكرة والسعر/سعر الكوينز في صف أول مدرس فقط ولا يلزم تكرارها لباقي المدرسين، ولو تركت السعر فارغاً يستخدم سعر المنصة الحالي"),
        ("شحن الكوينز من الإكسيل", "استخدم ورقة (شحن الكوينز) لشحن رصيد عدة مستخدمين دفعة واحدة برقم الهاتف أو الإيميل أو الاسم"),
        ("رقم الهاتف", "يُفضّل وضع خلية الهاتف كنص Text؛ وإن كُتب رقم بدون 0 يعالجه البوت تلقائياً (اختياري للأدمن)"),
        ("العلامة المائية", "اكتب نص العلامة المائية، أو اكتب كلمة (لوجو) أو (صورة) لإرفاق صورة لوجو خاص بالمدرس من البوت، أو اتركه فارغاً"),
        ("هاتف 2", "رقم إضافي يظهر بالتبادل في الصفحات الداخلية"),
        ("الخط وحجم الخط", "الخط مثل: cairo / tahoma / arial / naskh — وحجم الخط رقم مثل 14 أو 16"),
    ]
    for r_idx, (k, v) in enumerate(info_lines, start=1):
        ws_info.cell(row=r_idx, column=1, value=k)
        ws_info.cell(row=r_idx, column=2, value=v)

    if materials:
        active_mats = [m for m in materials if m.get("file_exists", True)] or materials
        start_mat_row = len(info_lines) + 3
        ws_info.cell(row=start_mat_row - 1, column=1, value="معرف المذكرة (ID)")
        ws_info.cell(row=start_mat_row - 1, column=2, value="اسم المذكرة الحالي في المنصة")
        for idx, m in enumerate(active_mats, start=start_mat_row):
            ws_info.cell(row=idx, column=1, value=m.get("id"))
            ws_info.cell(row=idx, column=2, value=m.get("title"))

    ws_info.column_dimensions["A"].width = 22
    ws_info.column_dimensions["B"].width = 72

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filter_usable_materials(materials: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """تصفية المذكرات الحقيقية المتوفرة فعلياً على القرص واستبعاد سجلات التجارب المفقودة."""
    raw_list = list(materials or [])
    usable = [
        m for m in raw_list
        if m.get("file_exists", True) and not str(m.get("title", "")).startswith("Test Material")
    ]
    return usable or raw_list


def filter_admin_materials(materials: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """تصفية مذكرات الإدارة مع الإبقاء على المذكرات القادمة (قريباً/تشويق) بجانب الجاهزة."""
    raw_list = list(materials or [])
    usable = [
        m for m in raw_list
        if (m.get("file_exists", True) or m.get("is_coming_soon", False))
        and not str(m.get("title", "")).startswith("Test Material")
    ]
    return usable or raw_list


def build_materials_catalog_excel_bytes(
    materials: Optional[List[Dict[str, Any]]] = None,
    source_names_by_id: Optional[Dict[int, str]] = None,
) -> bytes:
    """إنشاء ملف Excel منظم بقائمة المذكرات الخام وأسمائها المضبوطة في قاعدة البيانات."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "المذكرات والخامات"
    ws.views.sheetView[0].rightToLeft = True
    ws.freeze_panes = "A2"

    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(name="Cairo", size=11, bold=True, color="38BDF8")
    row_even_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    row_odd_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    body_font = Font(name="Cairo", size=11, color="0F172A")
    bold_font = Font(name="Cairo", size=11, bold=True, color="0F172A")
    thin_side = Side(style="thin", color="CBD5E1")
    border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    headers = [
        "رقم المذكرة (ID)",
        "الاسم المضبوط في النظام",
        "المادة / القسم",
        "عدد الصفحات",
        "السعر (ج.م)",
        "حالة النشر والتنسيق",
        "اسم الملف الأصلي / ملاحظة",
    ]
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.fill = header_fill
        c.font = header_font
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    raw_list = list(materials or [])
    usable = filter_usable_materials(raw_list)

    usable_sorted = sorted(usable, key=lambda x: int(x.get("id") or 0))
    src_map = source_names_by_id or {}

    for row_idx, m in enumerate(usable_sorted, start=2):
        mid = int(m.get("id") or 0)
        is_pub = bool(m.get("is_published", True))
        pages = int(m.get("total_pages") or 0)
        price = float(m.get("price") or 0)
        is_coming = bool(m.get("is_coming_soon", False))
        layout_ok = bool(m.get("layout_confirmed", True))
        if is_coming:
            status_label = "⏳ قريباً (تشويق — بانتظار الـ PDF)"
        elif not layout_ok:
            status_label = "⏳ بانتظار ضبط وتأكيد الأختام"
        else:
            status_label = "منشورة ومضبوطة الأختام" if is_pub else "مسودة مخفية"
        orig_note = src_map.get(mid) or (m.get("description") or "خامة مسجلة في النظام")

        vals = [
            mid,
            str(m.get("title") or ""),
            str(m.get("tag") or "الرياضيات"),
            pages,
            int(price) if price.is_integer() else price,
            status_label,
            str(orig_note),
        ]
        fill = row_even_fill if row_idx % 2 == 0 else row_odd_fill
        for col_idx, v in enumerate(vals, start=1):
            c = ws.cell(row=row_idx, column=col_idx, value=v)
            c.fill = fill
            c.font = bold_font if col_idx in (1, 2) else body_font
            c.border = border
            c.alignment = Alignment(
                horizontal="right" if col_idx in (2, 7) else "center",
                vertical="center",
            )
        ws.row_dimensions[row_idx].height = 22

    widths = [18, 42, 16, 15, 14, 24, 48]
    for col_idx, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = w

    # ورقة ثانية جاهزة للنسخ السريع في الدفعات
    ws_batch = wb.create_sheet("دفعات جاهزة بهذه الخامات")
    ws_batch.views.sheetView[0].rightToLeft = True
    ws_batch.freeze_panes = "A2"
    batch_headers = ["اسم المدرس", "رقم الهاتف", "المذكرة", "اسم السلسلة", "العلامة المائية", "هاتف 2", "الخط", "حجم الخط"]
    for col_idx, h in enumerate(batch_headers, start=1):
        c = ws_batch.cell(row=1, column=col_idx, value=h)
        c.fill = header_fill
        c.font = header_font
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws_batch.row_dimensions[1].height = 28

    for row_idx, m in enumerate(usable_sorted, start=2):
        row_vals = ["", "", str(m.get("title") or ""), "", "", "", "cairo", ""]
        fill = row_even_fill if row_idx % 2 == 0 else row_odd_fill
        for col_idx, v in enumerate(row_vals, start=1):
            c = ws_batch.cell(row=row_idx, column=col_idx, value=v)
            c.fill = fill
            c.font = body_font
            c.border = border
            if col_idx in (2, 6):
                c.number_format = "@"
            c.alignment = Alignment(horizontal="center", vertical="center")
    for col_idx, w in enumerate([24, 18, 42, 24, 22, 16, 12, 12], start=1):
        ws_batch.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_word_template_bytes(materials: Optional[List[Dict[str, Any]]] = None) -> bytes:
    """إنشاء ملف نموذج Word (.docx) جاهز لملء بيانات المدرسين بالجدول أو النص المنظم."""
    from docx import Document

    doc = Document()
    doc.add_heading("نموذج Z-PACT — بيانات ختم المذكرات للمدرسين", level=1)
    doc.add_paragraph(
        "أدخل بيانات المدرسين المتوفرة في الجدول أدناه (الاسم، الرقم، المذكرة، أو السلسلة — أي بيان متوفر يكفي، وللأدمن كل الأعمدة اختيارية). "
        "كما يمكنك كتابة كلمة (لوجو) أو (صورة) في عمود العلامة المائية ليطلب منك البوت إرسال صورة شعار المدرس. "
        "عند الانتهاء أرسل هذا الملف مباشرة إلى بوت الأدمن وسيقوم بختم وإرسال الملفات واحداً تلو الآخر بهدوء بدون ضغط على المعالج."
    )

    first_mat_title = materials[0]["title"] if materials else "اسم المذكرة أو رقمها"
    cols = ["اسم المدرس", "رقم الهاتف", "المذكرة", "اسم السلسلة", "العلامة المائية", "هاتف 2"]
    table = doc.add_table(rows=3, cols=len(cols))
    table.style = "Table Grid"

    for i, c_name in enumerate(cols):
        table.rows[0].cells[i].text = c_name

    samples = [
        ["د. أحمد محمد علي", "01012345678", first_mat_title, "سلسلة التفوق", "نسخة خاصة بالمدرس", ""],
        ["أ. محمد إبراهيم", "01122334455", first_mat_title, "", "", "01234567890"],
    ]
    for r_i, s_vals in enumerate(samples, start=1):
        for c_i, val in enumerate(s_vals):
            table.rows[r_i].cells[c_i].text = val

    doc.add_paragraph("")
    doc.add_paragraph("ملاحظة: يمكنك أيضاً كتابة المدرسين (أو حجز مذكرة قادمة بسعرها ووصفها من أول مدرس، أو شحن الكوينز) بدون جدول:")
    doc.add_paragraph(
        "الاسم: أ. خالد حسن\n"
        "الهاتف: 01099887766\n"
        f"المذكرة: {first_mat_title}\n"
        "وصف المذكرة: مراجعة شاملة (اختياري — يكفي مع أول مدرس)\n"
        "السعر: 100 (اختياري — لو فارغ يستخدم سعر المنصة)\n"
        "السلسلة: سلسلة الرواد\n"
        "العلامة المائية: لا للنشر"
    )

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

