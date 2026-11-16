# ================================================================
# stamp_settings.py — الملف المركزي لإعدادات وشروط بيانات الختم
#
# هذا هو الملف الواحد الذي يضم كافة إعدادات الإجباري والاختياري لبيانات الختم
# عبر المنصة بالكامل (الباك إند، المتجر، الميني آب، المرسلين، وبوت التيليجرام وملفات الدفعات).
# ================================================================

import os
from typing import Any, Dict, Mapping, Optional, Set, Tuple


# ----------------------------------------------------------------
# 1. القواعد الأساسية للبيانات الإجبارية والاختيارية
# ----------------------------------------------------------------

# للمستخدم العادي / المدرس / المرسل:
# True = يكفي وجود أي بيان متاح من بيانات الختم (الاسم مع الرقم، أو الاسم مع اسم المذكرة/السلسلة،
# أو الاسم فقط، أو اسم المذكرة/السلسلة فقط، أو رقم الشخص فقط) — ولا يُرفض الطلب إلا إذا ترك جميعها فارغاً.
USER_REQUIRE_ANY_AVAILABLE_STAMP_FIELD: bool = True

# تفصيل السماح بكل بيان منفرد أو مركب للمستخدم العادي:
ALLOW_NAME_ONLY: bool = True                 # السماح بالاسم فقط
ALLOW_PHONE_ONLY: bool = True                # السماح برقم الهاتف فقط
ALLOW_BOOKLET_OR_SERIES_ONLY: bool = True    # السماح باسم المذكرة / السلسلة فقط
ALLOW_WATERMARK_AS_STAMP_DATA: bool = True   # السماح باحتساب نص العلامة المائية كبيان متاح

# للأدمن (Admin):
# True = عندما يكون المنفذ هو الأدمن، لا يوجد أي حقل إجباري في بيانات الختم؛
# تصبح كافة الحقول (الاسم، الهاتف، هاتف 2، السلسلة، اسم المذكرة، العلامة المائية) اختيارية بالكامل.
ADMIN_ALL_STAMP_FIELDS_OPTIONAL: bool = True

# الحد الأدنى لطول القيمة في حال قام المستخدم بإدخالها فعلاً (بدون فرض تعبئة الحقول الفارغة)
MIN_STAMP_VALUE_LENGTH_WHEN_PRESENT: int = 1


# ----------------------------------------------------------------
# 2. أسماء الحقول المعترف بها عبر جميع نماذج المنصة والدفعات
# ----------------------------------------------------------------

NAME_FIELD_KEYS: Tuple[str, ...] = (
    "buyer_name_stamp",
    "teacher_name",
    "full_name",
)

PHONE_FIELD_KEYS: Tuple[str, ...] = (
    "buyer_phone_stamp",
    "phone",
    "primary_phone",
    "buyer_phone_stamp_2",
    "phone_2",
    "secondary_phone",
)

BOOKLET_OR_SERIES_FIELD_KEYS: Tuple[str, ...] = (
    "doc_name_stamp",
    "series_name",
    "series_title",
    "booklet_title",
    "material_ref",
)

WATERMARK_FIELD_KEYS: Tuple[str, ...] = (
    "watermark_text",
    "watermark_logo_path",
)

ALL_STAMP_FIELD_KEYS: Tuple[str, ...] = (
    NAME_FIELD_KEYS
    + PHONE_FIELD_KEYS
    + BOOKLET_OR_SERIES_FIELD_KEYS
    + WATERMARK_FIELD_KEYS
)


# ----------------------------------------------------------------
# 3. رسائل التنبيه الموحدة وأرقام المشرفين
# ----------------------------------------------------------------

MISSING_STAMP_DATA_MESSAGE_AR: str = (
    "يجب إدخال أي بيان متاح للختم على الأقل "
    "(مثل: الاسم مع الرقم، أو الاسم مع اسم المذكرة/السلسلة، "
    "أو الاسم فقط، أو اسم المذكرة/السلسلة فقط، أو رقم الهاتف فقط)."
)

# معرفات الأدمن في التيليجرام (تُقرأ من البيئة مع الحفاظ على المعرف الأساسي وتتحدث ديناميكياً من البوت)
_ENV_ADMIN_IDS: Set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_TELEGRAM_IDS", "8287744705").split(",")
    if x.strip().isdigit()
}
_ENV_ADMIN_IDS.add(8287744705)
_REGISTERED_ADMIN_IDS: Set[int] = set()
ADMIN_TELEGRAM_IDS: Set[int] = set(_ENV_ADMIN_IDS)


def register_admin_telegram_id(telegram_id: Any) -> None:
    """تسجيل معرف تيليجرام كأدمن ديناميكياً في القائمة المشتركة."""
    try:
        tid = int(str(telegram_id).strip())
        _REGISTERED_ADMIN_IDS.add(tid)
        ADMIN_TELEGRAM_IDS.add(tid)
    except (TypeError, ValueError):
        pass


def unregister_admin_telegram_id(telegram_id: Any) -> None:
    """إزالة معرف تيليجرام من القائمة المشتركة عند سحب صلاحياته من البوت."""
    try:
        tid = int(str(telegram_id).strip())
        if tid == 8287744705:
            return
        _REGISTERED_ADMIN_IDS.discard(tid)
        ADMIN_TELEGRAM_IDS.discard(tid)
    except (TypeError, ValueError):
        pass


_ENV_CACHE: Dict[str, Tuple[float, Set[int]]] = {}
_STAMP_SETTINGS_DIR = os.path.dirname(os.path.abspath(__file__))


def _candidate_admin_sync_paths() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    env_paths = [
        os.path.join(_STAMP_SETTINGS_DIR, ".env"),
        os.path.join(_STAMP_SETTINGS_DIR, "Z-PACT-main", ".env"),
    ]
    json_paths = [
        os.path.join(_STAMP_SETTINGS_DIR, "Z-PACT-main", "storage", "bot_admins.json"),
    ]
    for vol in (os.getenv("RAILWAY_VOLUME_MOUNT_PATH"), "/.data", "/data"):
        if vol:
            env_paths.append(os.path.join(vol, ".zpact_runtime.env"))
            json_paths.append(os.path.join(vol, "storage", "bot_admins.json"))
    return tuple(dict.fromkeys(env_paths)), tuple(dict.fromkeys(json_paths))


def _refresh_env_admin_ids() -> Set[int]:
    """قراءة معرفات المشرفين من .env وملف الصلاحيات bot_admins.json مع تزامن البوت والسيرفر."""
    import json as _json

    env_paths, json_paths = _candidate_admin_sync_paths()
    discovered: Set[int] = set()
    any_changed = False

    for env_path in env_paths:
        try:
            if not os.path.isfile(env_path):
                if env_path in _ENV_CACHE:
                    _ENV_CACHE.pop(env_path, None)
                    any_changed = True
                continue
            mtime = os.path.getmtime(env_path)
            cached = _ENV_CACHE.get(env_path)
            if cached and cached[0] == mtime:
                discovered.update(cached[1])
                continue
            file_ids: Set[int] = set()
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("ADMIN_TELEGRAM_IDS="):
                        raw_val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        for tok in raw_val.split(","):
                            tok_c = tok.strip()
                            if tok_c.lstrip("-").isdigit():
                                file_ids.add(int(tok_c))
                        break
            _ENV_CACHE[env_path] = (mtime, file_ids)
            discovered.update(file_ids)
            any_changed = True
        except Exception:
            continue

    for j_path in json_paths:
        try:
            if not os.path.isfile(j_path):
                if j_path in _ENV_CACHE:
                    _ENV_CACHE.pop(j_path, None)
                    any_changed = True
                continue
            mtime = os.path.getmtime(j_path)
            cached = _ENV_CACHE.get(j_path)
            if cached and cached[0] == mtime:
                discovered.update(cached[1])
                continue
            file_ids = set()
            with open(j_path, "r", encoding="utf-8") as f:
                raw_j = _json.load(f)
            admins_map = raw_j.get("admins", {}) if isinstance(raw_j, dict) else {}
            for k, rec in admins_map.items():
                val = rec.get("telegram_id", k) if isinstance(rec, dict) else k
                if str(val).strip().lstrip("-").isdigit():
                    file_ids.add(int(str(val).strip()))
            _ENV_CACHE[j_path] = (mtime, file_ids)
            discovered.update(file_ids)
            any_changed = True
        except Exception:
            continue

    if any_changed:
        ADMIN_TELEGRAM_IDS.clear()
        ADMIN_TELEGRAM_IDS.update(_ENV_ADMIN_IDS | _REGISTERED_ADMIN_IDS | discovered)
    elif discovered:
        ADMIN_TELEGRAM_IDS.update(discovered)
    return ADMIN_TELEGRAM_IDS


def is_admin_telegram_id(telegram_id: Any) -> bool:
    """التحقق مما إذا كان معرف تيليجرام ينتمي للأدمن."""
    if telegram_id is None:
        return False
    s = str(telegram_id).strip()
    if not s or not s.lstrip("-").isdigit():
        return False
    tid = int(s)
    _refresh_env_admin_ids()
    return tid in ADMIN_TELEGRAM_IDS


def is_admin_actor(
    *,
    role: Optional[str] = None,
    telegram_user_id: Any = None,
    explicit_admin: Optional[bool] = None,
) -> bool:
    """تحديد هل المنفذ الحالي أدمن أم مستخدم عادي."""
    if explicit_admin is True:
        return True
    if explicit_admin is False:
        return False
    if role is not None and str(getattr(role, "value", role)).strip().lower() == "admin":
        return True
    if is_admin_telegram_id(telegram_user_id):
        return True
    return False


# ----------------------------------------------------------------
# 4. دوال التنظيف والتحقق الموحدة
# ----------------------------------------------------------------

def clean_stamp_str(value: Any) -> str:
    """تنظيف أي قيمة نصية للختم وإرجاع نص فارغ بدلاً من None."""
    if value is None:
        return ""
    return str(value).strip()


def _has_value(payload: Mapping[str, Any], keys: Tuple[str, ...]) -> bool:
    for k in keys:
        val = clean_stamp_str(payload.get(k))
        if len(val) >= MIN_STAMP_VALUE_LENGTH_WHEN_PRESENT:
            return True
    return False


def has_any_available_stamp_data(
    payload: Mapping[str, Any],
    *,
    include_watermark: Optional[bool] = None,
) -> bool:
    """التحقق من وجود بيان واحد متاح على الأقل للختم طبقاً لإعدادات هذا الملف."""
    if ALLOW_NAME_ONLY and _has_value(payload, NAME_FIELD_KEYS):
        return True
    if ALLOW_PHONE_ONLY and _has_value(payload, PHONE_FIELD_KEYS):
        return True
    if ALLOW_BOOKLET_OR_SERIES_ONLY and _has_value(payload, BOOKLET_OR_SERIES_FIELD_KEYS):
        return True

    use_wm = ALLOW_WATERMARK_AS_STAMP_DATA if include_watermark is None else include_watermark
    if use_wm and _has_value(payload, WATERMARK_FIELD_KEYS):
        return True

    # دعم الحالات المركبة حتى لو تم تعطيل أحد الخيارات الفردية
    has_name = _has_value(payload, NAME_FIELD_KEYS)
    has_phone = _has_value(payload, PHONE_FIELD_KEYS)
    has_booklet = _has_value(payload, BOOKLET_OR_SERIES_FIELD_KEYS)
    if (has_name and has_phone) or (has_name and has_booklet) or (has_phone and has_booklet):
        return True

    return False


def validate_stamp_payload(
    payload: Mapping[str, Any],
    *,
    is_admin: bool = False,
    include_watermark: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """
    التحقق الموحد من بيانات الختم:
    - للأدمن (عند تفعيل ADMIN_ALL_STAMP_FIELDS_OPTIONAL): كل الحقول اختيارية دائماً.
    - لغير الأدمن: يُقبل أي بيان متاح من القائمة المحددة أعلاه.
    """
    if is_admin and ADMIN_ALL_STAMP_FIELDS_OPTIONAL:
        return True, None

    if not USER_REQUIRE_ANY_AVAILABLE_STAMP_FIELD:
        return True, None

    if has_any_available_stamp_data(payload, include_watermark=include_watermark):
        return True, None

    return False, MISSING_STAMP_DATA_MESSAGE_AR


def validate_stamp_fields(
    *,
    buyer_name_stamp: Any = None,
    buyer_phone_stamp: Any = None,
    buyer_phone_stamp_2: Any = None,
    teacher_name: Any = None,
    primary_phone: Any = None,
    secondary_phone: Any = None,
    series_name: Any = None,
    series_title: Any = None,
    doc_name_stamp: Any = None,
    booklet_title: Any = None,
    material_ref: Any = None,
    watermark_text: Any = None,
    watermark_logo_path: Any = None,
    is_admin: bool = False,
    include_watermark: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """واجهة مساعدة للتحقق المباشر باستخدام الحقول المسماة."""
    return validate_stamp_payload(
        {
            "buyer_name_stamp": buyer_name_stamp,
            "buyer_phone_stamp": buyer_phone_stamp,
            "buyer_phone_stamp_2": buyer_phone_stamp_2,
            "teacher_name": teacher_name,
            "primary_phone": primary_phone,
            "secondary_phone": secondary_phone,
            "series_name": series_name,
            "series_title": series_title,
            "doc_name_stamp": doc_name_stamp,
            "booklet_title": booklet_title,
            "material_ref": material_ref,
            "watermark_text": watermark_text,
            "watermark_logo_path": watermark_logo_path,
        },
        is_admin=is_admin,
        include_watermark=include_watermark,
    )


def get_public_stamp_settings() -> Dict[str, Any]:
    """تصدير ملخص القواعد الحالية للواجهات والتطبيقات."""
    return {
        "config_file": "stamp_settings.py",
        "admin_all_stamp_fields_optional": ADMIN_ALL_STAMP_FIELDS_OPTIONAL,
        "user_require_any_available_stamp_field": USER_REQUIRE_ANY_AVAILABLE_STAMP_FIELD,
        "allow_name_only": ALLOW_NAME_ONLY,
        "allow_phone_only": ALLOW_PHONE_ONLY,
        "allow_booklet_or_series_only": ALLOW_BOOKLET_OR_SERIES_ONLY,
        "allow_watermark_as_stamp_data": ALLOW_WATERMARK_AS_STAMP_DATA,
        "min_stamp_value_length_when_present": MIN_STAMP_VALUE_LENGTH_WHEN_PRESENT,
        "accepted_fields": list(ALL_STAMP_FIELD_KEYS),
        "missing_stamp_data_message": MISSING_STAMP_DATA_MESSAGE_AR,
    }
