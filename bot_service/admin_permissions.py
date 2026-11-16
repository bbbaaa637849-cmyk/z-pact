# ================================================================
# bot_service/admin_permissions.py — نظام صلاحيات المشرفين وإدارتها
# ================================================================

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union


# الصلاحيات التفصيلية المتاحة لمشرفي لوحة تحكم البوت
ADMIN_PERMISSIONS: Tuple[Tuple[str, str, str], ...] = (
    (
        "batch_vip",
        "📊 الدفعات والختم VIP",
        "دفعات إكسيل/وورد، الختم المباشر VIP، قوالب Excel/Word، وإكسيل الخامات",
    ),
    (
        "studio_fonts",
        "🔤 الخطوط والإطارات",
        "تعديل الخطوط والأحجام والإحداثيات، المعاينة الفورية، وتركيب/حذف الإطارات",
    ),
    (
        "materials",
        "📚 المذكرات والأسعار",
        "رفع مذكرات جديدة، تعديل الأسعار، النشر/الإخفاء، استبدال PDF، والحذف",
    ),
    (
        "orders_finance",
        "⏳ الطلبات والمالية والـ SMS",
        "الإحصائيات المالية، الطلبات المعلقة، القبول/الرفض، سحب النسخة، وسجل الـ SMS",
    ),
    (
        "support_broadcast",
        "📢 الدعم الفني والإذاعة",
        "استقبال استفسارات الدعم الفني، الرد على العملاء، وإرسال الإذاعة للمدرسين",
    ),
    (
        "senders_ota",
        "🚀 المرسلون والتحديث الهوائي OTA",
        "تقارير فريق المرسلين والعمولات والسيريال، وبث التحديثات الهوائية OTA",
    ),
    (
        "apks_settings",
        "⚙️ المحافظ وتطبيقات APK",
        "تعديل أرقام المحافظ وإنستاباي والميني آب وتحميل تطبيقات الأدمن والمدرس والمرسل",
    ),
    (
        "manage_admins",
        "👥 إدارة المشرفين والصلاحيات",
        "إضافة وتعديل وسحب صلاحيات المشرفين المساعدين واعتماد مشرفي التطبيق",
    ),
)

PERM_KEYS: Tuple[str, ...] = tuple(k for k, _, _ in ADMIN_PERMISSIONS)
PERM_LABELS: Dict[str, str] = {k: lbl for k, lbl, _ in ADMIN_PERMISSIONS}
PERM_DESCS: Dict[str, str] = {k: desc for k, _, desc in ADMIN_PERMISSIONS}


def normalize_permissions(raw: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """توحيد قاموس الصلاحيات مع التأكد من وجود كافة المفاتيح بقيم منطقية."""
    data = raw or {}
    return {k: bool(data.get(k, False)) for k in PERM_KEYS}


def full_permissions_dict() -> Dict[str, bool]:
    """صلاحيات كاملة مطابقة لتحكمات المالك."""
    return {k: True for k in PERM_KEYS}


def preset_permissions_dict(preset_key: str) -> Dict[str, bool]:
    """إرجاع باقة صلاحيات جاهزة."""
    key = str(preset_key or "").strip().lower()
    if key == "full":
        return full_permissions_dict()
    if key == "no_admin_mgr":
        p = full_permissions_dict()
        p["manage_admins"] = False
        return p
    if key == "operations":
        return {
            "batch_vip": True,
            "studio_fonts": True,
            "materials": False,
            "orders_finance": True,
            "support_broadcast": True,
            "senders_ota": False,
            "apks_settings": False,
            "manage_admins": False,
        }
    return {k: False for k in PERM_KEYS}


def parse_owner_ids_from_env() -> Set[int]:
    """قراءة معرفات الملاك المحميين من متغيرات البيئة مع الحفاظ على المالك الأساسي."""
    owners: Set[int] = {8287744705}
    raw_primary = (os.getenv("PRIMARY_ADMIN_TELEGRAM_IDS") or os.getenv("PRIMARY_ADMIN_TELEGRAM_ID") or "").strip()
    for tok in raw_primary.split(","):
        tok_c = tok.strip()
        if tok_c.lstrip("-").isdigit():
            owners.add(int(tok_c))
    return owners


class AdminRegistry:
    """مستودع دائم لإدارة المشرفين وصلاحياتهم في ملف JSON مع كاش متزامن."""

    def __init__(self, json_path: str, owner_ids: Optional[Set[int]] = None):
        self.json_path = json_path
        self.owner_ids: Set[int] = set(owner_ids) if owner_ids is not None else parse_owner_ids_from_env()
        self._cached_mtime: Optional[float] = None
        self._admins: Dict[int, Dict[str, Any]] = {}
        self.reload(force=True)

    def _read_disk(self) -> Dict[int, Dict[str, Any]]:
        if not self.json_path or not os.path.isfile(self.json_path):
            return {}
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return {}

        admins_raw = raw.get("admins", {}) if isinstance(raw, dict) else {}
        parsed: Dict[int, Dict[str, Any]] = {}
        for k, rec in admins_raw.items():
            try:
                tid = int(rec.get("telegram_id", k) if isinstance(rec, dict) else k)
            except (TypeError, ValueError):
                continue
            if tid in self.owner_ids:
                continue
            if not isinstance(rec, dict):
                continue
            perms = normalize_permissions(rec.get("permissions"))
            parsed[tid] = {
                "telegram_id": tid,
                "label": str(rec.get("label") or f"مشرف {tid}").strip(),
                "permissions": perms,
                "added_by": rec.get("added_by"),
                "added_at": rec.get("added_at"),
                "updated_at": rec.get("updated_at"),
            }
        return parsed

    def reload(self, force: bool = False) -> None:
        if not self.json_path or not os.path.isfile(self.json_path):
            if force or self._cached_mtime is not None:
                self._cached_mtime = None
                self._admins = {}
            return
        try:
            mtime = os.path.getmtime(self.json_path)
        except OSError:
            return
        if not force and self._cached_mtime == mtime:
            return
        self._admins = self._read_disk()
        self._cached_mtime = mtime

    def _write_disk(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.json_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "admins": {
                str(tid): rec
                for tid, rec in sorted(self._admins.items(), key=lambda x: x[0])
            }
        }
        tmp_path = f"{self.json_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.json_path)
        try:
            self._cached_mtime = os.path.getmtime(self.json_path)
        except OSError:
            self._cached_mtime = None

    def is_owner(self, telegram_id: Any) -> bool:
        try:
            return int(telegram_id) in self.owner_ids
        except (TypeError, ValueError):
            return False

    def get_record(self, telegram_id: Any) -> Optional[Dict[str, Any]]:
        self.reload()
        try:
            tid = int(telegram_id)
        except (TypeError, ValueError):
            return None
        rec = self._admins.get(tid)
        if not rec:
            return None
        return {**rec, "permissions": dict(rec["permissions"])}

    def list_records(self) -> List[Dict[str, Any]]:
        self.reload()
        return [
            {**rec, "permissions": dict(rec["permissions"])}
            for _, rec in sorted(self._admins.items(), key=lambda x: x[0])
        ]

    def set_admin(
        self,
        telegram_id: Any,
        permissions: Dict[str, Any],
        label: Optional[str] = None,
        added_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.reload()
        tid = int(telegram_id)
        if tid in self.owner_ids:
            return {
                "telegram_id": tid,
                "label": label or "المالك الرئيسي",
                "permissions": full_permissions_dict(),
                "is_owner": True,
            }

        existing = self._admins.get(tid, {})
        now_ts = int(time.time())
        clean_label = str(label or existing.get("label") or f"مشرف {tid}").strip()[:64]
        rec = {
            "telegram_id": tid,
            "label": clean_label,
            "permissions": normalize_permissions(permissions),
            "added_by": added_by if added_by is not None else existing.get("added_by"),
            "added_at": existing.get("added_at") or now_ts,
            "updated_at": now_ts,
        }
        self._admins[tid] = rec
        self._write_disk()
        return {**rec, "permissions": dict(rec["permissions"])}

    def remove_admin(self, telegram_id: Any) -> bool:
        self.reload()
        try:
            tid = int(telegram_id)
        except (TypeError, ValueError):
            return False
        if tid in self.owner_ids:
            return False
        if tid not in self._admins:
            return False
        self._admins.pop(tid, None)
        self._write_disk()
        return True

    def all_ids(self) -> Set[int]:
        self.reload()
        return set(self.owner_ids) | set(self._admins.keys())


async def check_admin_perm(
    event: Any,
    bot_cfg: Any,
    permission: Optional[Union[str, Iterable[str]]] = None,
    *,
    state: Any = None,
) -> bool:
    """
    التحقق الموحد من صلاحية المشرف للأمر أو الزر.
    يرد برسالة رفض واضحة إذا لم يملك الصلاحية المطلوبة ويمسح حالة FSM إن وجدت.
    """
    user = getattr(event, "from_user", None)
    user_id = getattr(user, "id", None)
    allowed = False
    if user_id is not None and bot_cfg.is_admin(user_id):
        if permission is None:
            allowed = True
        elif isinstance(permission, str):
            allowed = bot_cfg.has_permission(user_id, permission)
        else:
            allowed = any(bot_cfg.has_permission(user_id, p) for p in permission)

    if allowed:
        return True

    if state is not None:
        try:
            await state.clear()
        except Exception:
            pass

    if hasattr(event, "message") and hasattr(event, "answer"):
        # CallbackQuery
        try:
            await event.answer("⛔ ليست لديك صلاحية الوصول لهذا القسم.", show_alert=True)
        except Exception:
            pass
    elif hasattr(event, "answer"):
        # Message
        try:
            await event.answer("⛔ ليست لديك صلاحية الوصول لهذا القسم.")
        except Exception:
            pass
    return False
