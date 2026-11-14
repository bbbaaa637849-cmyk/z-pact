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


def _resolve_bot_storage_dir() -> str:
    """استخدام Volume السحابة إن كان مفعلاً وقابلاً للكتابة، وإلا المجلد المرفق بالمشروع."""
    if os.name != "nt":
        for cand in (os.getenv("RAILWAY_VOLUME_MOUNT_PATH"), "/.data"):
            if cand and os.path.isdir(cand) and os.access(cand, os.W_OK):
                return os.path.join(os.path.abspath(cand), "storage")
    return os.path.join(BASE_DIR, "Z-PACT-main", "storage")


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
    
    # 6. مسارات التخزين
    STORAGE_DIR: str = _resolve_bot_storage_dir()
    RECEIPTS_DIR: str = os.path.join(STORAGE_DIR, "receipts")

    def add_admin(self, telegram_id: int):
        """إضافة مدير جديد ديناميكياً أثناء تشغيل البوت وحفظه في التكوين"""
        tid = int(telegram_id)
        self.ADMIN_TELEGRAM_IDS.add(tid)
        stamp_settings.register_admin_telegram_id(tid)
        try:
            ids_csv = ",".join(str(x) for x in sorted(self.ADMIN_TELEGRAM_IDS))
            self._persist_env("ADMIN_TELEGRAM_IDS", ids_csv)
        except Exception:
            pass

    def is_admin(self, telegram_id: int) -> bool:
        """التحقق هل المستخدم مدير"""
        return telegram_id in self.ADMIN_TELEGRAM_IDS or stamp_settings.is_admin_telegram_id(telegram_id)

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
        """تحديث القيمة داخل ملفات .env للحفاظ عليها بعد إعادة التشغيل"""
        import re
        targets = [os.path.join(BASE_DIR, ".env"), os.path.join(BASE_DIR, "Z-PACT-main", ".env")]
        existing_targets = [p for p in targets if os.path.exists(p)]
        if not existing_targets:
            existing_targets = [os.path.join(BASE_DIR, "Z-PACT-main", ".env")]

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
