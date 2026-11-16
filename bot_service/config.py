# ================================================================
# bot_service/config.py — إعدادات ومتغيرات بوت التيليجرام
# ================================================================

import os
import sys
import time
from typing import Set
from dotenv import load_dotenv

# تحميل ملف .env من المجلد الرئيسي والمحلي وضمان رؤية stamp_settings.py
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import stamp_settings  # noqa: E402,F401 — الملف المركزي لإعدادات بيانات الختم (الإجباري والاختياري)
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "Z-PACT-main", ".env"))


def _derive_railway_miniapp_url() -> str:
    """اشتقاق رابط الميني آب تلقائياً من متغيرات Railway المدمجة دون الحاجة لأي إعداد يدوي."""
    raw_domain = (
        os.getenv("RAILWAY_PUBLIC_DOMAIN")
        or os.getenv("RAILWAY_STATIC_URL")
        or os.getenv("RAILWAY_SERVICE_PUBLIC_DOMAIN")
        or ""
    ).strip()
    if not raw_domain:
        return ""
    if not raw_domain.startswith(("http://", "https://")):
        raw_domain = f"https://{raw_domain}"
    raw_domain = raw_domain.rstrip("/")
    if raw_domain.endswith("/miniapp"):
        return f"{raw_domain}/"
    return f"{raw_domain}/miniapp/"


def _detect_bot_volume_root() -> str:
    if os.name != "nt":
        for cand in (os.getenv("RAILWAY_VOLUME_MOUNT_PATH"), "/.data", "/data"):
            if cand and os.path.isdir(cand) and os.access(cand, os.W_OK):
                return os.path.abspath(cand)
    return ""


def _resolve_bot_storage_dir() -> str:
    """استخدام Volume السحابة إن كان مفعلاً وقابلاً للكتابة، وإلا المجلد المرفق بالمشروع."""
    vol_root = _detect_bot_volume_root()
    if vol_root:
        return os.path.join(vol_root, "storage")
    return os.path.join(BASE_DIR, "Z-PACT-main", "storage")


# تحميل الإعدادات الحية الدائمة من الـ Volume (مثل أرقام المحافظ وقائمة المشرفين) فوق الإعدادات الافتراضية
_VOLUME_RUNTIME_ENV = (
    os.path.join(_detect_bot_volume_root(), ".zpact_runtime.env")
    if _detect_bot_volume_root()
    else ""
)
if _VOLUME_RUNTIME_ENV and os.path.isfile(_VOLUME_RUNTIME_ENV):
    load_dotenv(_VOLUME_RUNTIME_ENV, override=True)


_RUNTIME_PORT = (os.getenv("PORT") or "8000").strip()
_DEFAULT_CORE_BASE = (os.getenv("CORE_BASE_URL") or f"http://127.0.0.1:{_RUNTIME_PORT}").strip().rstrip("/")
_DEFAULT_CORE_API = (os.getenv("CORE_API_URL") or f"{_DEFAULT_CORE_BASE}/api").strip().rstrip("/")
_RAILWAY_MINIAPP_URL = _derive_railway_miniapp_url()
_DEFAULT_MINIAPP_URL = (_RAILWAY_MINIAPP_URL or os.getenv("MINIAPP_URL") or f"{_DEFAULT_CORE_BASE}/miniapp/").strip()


class BotConfig:
    # 1. توكن البوت الأساسي المباشر
    BOT_TOKEN: str = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "9095089179:AAFiTxEzAYn_poUUGXuRf_l0NnsL2W1twkM"
    ).strip()
    
    # 2. الجسر الداخلي للمنصة الأساسية
    CORE_API_URL: str = _DEFAULT_CORE_API
    CORE_BASE_URL: str = _DEFAULT_CORE_BASE
    INTERNAL_SERVICE_KEY: str = os.getenv(
        "INTERNAL_SERVICE_KEY",
        "zpact_internal_bridge_secret_key_2026"
    )
    
    # 3. إعدادات الإدارة والصلاحيات (المعرف الحقيقي المباشر)
    ADMIN_PASSCODE: str = os.getenv("ADMIN_PASSCODE", "zpact_admin_super_secret_2026")
    _admin_ids_str: str = os.getenv("ADMIN_TELEGRAM_IDS", "8287744705")
    ADMIN_TELEGRAM_IDS: Set[int] = {
        int(x.strip()) for x in _admin_ids_str.split(",") if x.strip().isdigit()
    }
    # ضمان وجود معرف المشرف الرئيسي دائماً
    if not ADMIN_TELEGRAM_IDS:
        ADMIN_TELEGRAM_IDS = {8287744705}
    else:
        ADMIN_TELEGRAM_IDS.add(8287744705)
    
    # 4. بيانات تحويل الأموال والمحافظ (الرقم الحقيقي)
    PAYMENT_WALLET_NUMBER: str = os.getenv("PAYMENT_WALLET_NUMBER", "01097671808")
    PAYMENT_WALLET_NUMBER_2: str = os.getenv("PAYMENT_WALLET_NUMBER_2", "01032881402")
    PAYMENT_WALLET_DISPLAY: str = os.getenv("PAYMENT_WALLET_DISPLAY", "+201097671808 / +201032881402")
    INSTAPAY_ADDRESS: str = os.getenv("INSTAPAY_ADDRESS", "01097671808")
    PAYMENT_INSTRUCTIONS_TITLE: str = "طرق الدفع المعتمدة (تأكيد لحظي تلقائي ⚡)"
    
    # 5. رابط التطبيق المصغر (Telegram Mini App)
    MINIAPP_URL: str = _DEFAULT_MINIAPP_URL
    MINIAPP_VERSION: str = str(int(time.time()))
    
    # 6. مسارات التخزين وسجل المشرفين وصلاحياتهم
    STORAGE_DIR: str = _resolve_bot_storage_dir()
    RECEIPTS_DIR: str = os.path.join(STORAGE_DIR, "receipts")
    ADMINS_JSON_PATH: str = os.path.join(STORAGE_DIR, "bot_admins.json")

    def _registry(self):
        from bot_service.admin_permissions import AdminRegistry
        reg = getattr(self, "_admin_registry_cache", None)
        if reg is None or reg.json_path != self.ADMINS_JSON_PATH:
            reg = AdminRegistry(self.ADMINS_JSON_PATH)
            self._admin_registry_cache = reg
        return reg

    def is_owner_admin(self, telegram_id: int) -> bool:
        """هل المستخدم من المشرفين الملاك المحميين."""
        return self._registry().is_owner(telegram_id)

    def _sync_admin_ids_env(self) -> None:
        reg = self._registry()
        merged = set(self.ADMIN_TELEGRAM_IDS) | reg.all_ids()
        self.ADMIN_TELEGRAM_IDS = merged
        for x in merged:
            stamp_settings.register_admin_telegram_id(x)
        try:
            ids_csv = ",".join(str(x) for x in sorted(self.ADMIN_TELEGRAM_IDS))
            self._persist_env("ADMIN_TELEGRAM_IDS", ids_csv)
        except Exception:
            pass

    def add_admin(self, telegram_id: int, label: str = None, added_by: int = None):
        """إضافة مشرف بكامل الصلاحيات (أو حفظ صلاحياته الحالية إن كان مسجلاً)."""
        from bot_service.admin_permissions import full_permissions_dict
        tid = int(telegram_id)
        existing = self._registry().get_record(tid)
        perms = existing["permissions"] if existing else full_permissions_dict()
        self.set_admin_with_permissions(
            tid,
            perms,
            label=label or (existing["label"] if existing else None),
            added_by=added_by,
        )

    def set_admin_with_permissions(
        self,
        telegram_id: int,
        permissions: dict,
        label: str = None,
        added_by: int = None,
    ) -> dict:
        """إضافة أو تحديث صلاحيات مشرف وحفظها في bot_admins.json و.env."""
        tid = int(telegram_id)
        rec = self._registry().set_admin(tid, permissions, label=label, added_by=added_by)
        self.ADMIN_TELEGRAM_IDS.add(tid)
        stamp_settings.register_admin_telegram_id(tid)
        self._sync_admin_ids_env()
        return rec

    def remove_admin(self, telegram_id: int) -> bool:
        """سحب صلاحيات مشرف مضاف وإزالته نهائياً (مع حماية الملاك الرئيسيين)."""
        tid = int(telegram_id)
        if self.is_owner_admin(tid):
            return False
        removed_from_reg = self._registry().remove_admin(tid)
        was_in_set = tid in self.ADMIN_TELEGRAM_IDS
        if not removed_from_reg and not was_in_set:
            return False
        self.ADMIN_TELEGRAM_IDS.discard(tid)
        stamp_settings.unregister_admin_telegram_id(tid)
        self._sync_admin_ids_env()
        return True

    def list_managed_admins(self) -> list:
        """إرجاع قائمة المشرفين المضافين وصلاحياتهم."""
        return self._registry().list_records()

    def get_admin_record(self, telegram_id: int):
        return self._registry().get_record(telegram_id)

    def get_admin_permissions(self, telegram_id: int) -> dict:
        """إرجاع قاموس الصلاحيات الفعلية للمشرف."""
        from bot_service.admin_permissions import PERM_KEYS, full_permissions_dict
        try:
            tid = int(telegram_id)
        except (TypeError, ValueError):
            return {k: False for k in PERM_KEYS}
        if self.is_owner_admin(tid):
            return full_permissions_dict()
        rec = self._registry().get_record(tid)
        if rec is not None:
            return rec["permissions"]
        if tid in self.ADMIN_TELEGRAM_IDS or stamp_settings.is_admin_telegram_id(tid):
            return full_permissions_dict()
        return {k: False for k in PERM_KEYS}

    def has_permission(self, telegram_id: int, permission: str) -> bool:
        """التحقق من امتلاك المشرف لصلاحية معينة."""
        if not self.is_admin(telegram_id):
            return False
        return bool(self.get_admin_permissions(telegram_id).get(permission, False))

    def get_admins_for_permission(self, permission: str) -> list:
        """إرجاع معرفات المشرفين المصرح لهم باستلام/تنفيذ صلاحية معينة."""
        candidates = set(self.ADMIN_TELEGRAM_IDS) | self._registry().all_ids()
        return sorted(uid for uid in candidates if self.has_permission(uid, permission))

    def is_admin(self, telegram_id: int) -> bool:
        """التحقق هل المستخدم مدير (مالك رئيسي أو مشرف مضاف بصلاحيات)."""
        try:
            tid = int(telegram_id)
        except (TypeError, ValueError):
            return False
        if self.is_owner_admin(tid):
            return True
        if self._registry().get_record(tid) is not None:
            return True
        return tid in self.ADMIN_TELEGRAM_IDS or stamp_settings.is_admin_telegram_id(tid)

    def update_wallet(self, new_wallet: str):
        """تحديث رقم المحفظة وحفظه في الإعدادات وملف .env"""
        clean_num = "".join(filter(str.isdigit, new_wallet))
        if clean_num.startswith("20") and len(clean_num) > 10:
            clean_num = "0" + clean_num[2:]
        elif not clean_num.startswith("0") and len(clean_num) == 10:
            clean_num = "0" + clean_num
        self.PAYMENT_WALLET_NUMBER = clean_num
        self.PAYMENT_WALLET_DISPLAY = f"+2{clean_num}"
        self._persist_env("PAYMENT_WALLET_NUMBER", self.PAYMENT_WALLET_NUMBER)
        self._persist_env("PAYMENT_WALLET_DISPLAY", self.PAYMENT_WALLET_DISPLAY)

    def update_instapay(self, new_address: str):
        """تحديث عنوان إنستاباي وحفظه في الإعدادات وملف .env"""
        self.INSTAPAY_ADDRESS = new_address.strip()
        self._persist_env("INSTAPAY_ADDRESS", self.INSTAPAY_ADDRESS)

    def update_miniapp_url(self, new_url: str):
        """تحديث رابط الميني آب وحفظه في الإعدادات وملف .env"""
        url = new_url.strip().split("?")[0]
        if url:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = f"https://{url}"
            if not url.endswith("/"):
                if not url.endswith("/miniapp"):
                    url = f"{url.rstrip('/')}/miniapp/"
                else:
                    url = f"{url}/"
        self.MINIAPP_URL = url
        self.MINIAPP_VERSION = str(int(time.time()))
        self._persist_env("MINIAPP_URL", self.MINIAPP_URL)

    def get_live_miniapp_url(self, cache_bust: bool = False) -> str:
        """جلب أحدث رابط حي للميني آب مع أولوية لبيئة Railway ثم المتغيرات وملفات .env"""
        import re
        railway_url = _derive_railway_miniapp_url()
        if railway_url:
            if railway_url != self.MINIAPP_URL:
                self.MINIAPP_URL = railway_url
                self.MINIAPP_VERSION = str(int(time.time()))
            live_url = railway_url
        else:
            live_url = self.MINIAPP_URL or f"http://127.0.0.1:{(os.getenv('PORT') or '8000').strip()}/miniapp/"
            is_cloud_env = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
            if not is_cloud_env:
                for env_path in [os.path.join(BASE_DIR, "Z-PACT-main", ".env"), os.path.join(BASE_DIR, ".env")]:
                    if os.path.isfile(env_path):
                        try:
                            with open(env_path, "r", encoding="utf-8") as f:
                                m = re.search(r"^MINIAPP_URL=(.+)$", f.read(), flags=re.MULTILINE)
                                if m and m.group(1).strip():
                                    candidate = m.group(1).strip()
                                    if candidate != self.MINIAPP_URL and candidate.startswith("https://"):
                                        self.MINIAPP_URL = candidate
                                        self.MINIAPP_VERSION = str(int(time.time()))
                                        live_url = candidate
                                    break
                        except Exception:
                            pass
        if cache_bust and live_url.lower().startswith("https://"):
            sep = "&" if "?" in live_url else "?"
            return f"{live_url}{sep}v={self.MINIAPP_VERSION}"
        return live_url

    def _persist_env(self, key: str, value: str):
        """تحديث القيمة داخل ملفات .env + ملف الـ Volume الدائم للحفاظ على الإعدادات بعد إعادة التشغيل"""
        import re
        targets = [os.path.join(BASE_DIR, ".env"), os.path.join(BASE_DIR, "Z-PACT-main", ".env")]
        existing_targets = [p for p in targets if os.path.exists(p)]
        if not existing_targets:
            existing_targets = [os.path.join(BASE_DIR, "Z-PACT-main", ".env")]

        vol_root = _detect_bot_volume_root()
        if vol_root and key != "MINIAPP_URL":
            existing_targets.append(os.path.join(vol_root, ".zpact_runtime.env"))

        for env_path in existing_targets:
            try:
                content = ""
                if os.path.exists(env_path):
                    with open(env_path, "r", encoding="utf-8") as f:
                        content = f.read()
                pattern = rf"^{key}=.*$"
                if re.search(pattern, content, flags=re.MULTILINE):
                    new_content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
                else:
                    suffix = "" if (not content or content.endswith("\n")) else "\n"
                    new_content = f"{content}{suffix}{key}={value}\n"
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception:
                pass

bot_config = BotConfig()
os.makedirs(bot_config.RECEIPTS_DIR, exist_ok=True)
