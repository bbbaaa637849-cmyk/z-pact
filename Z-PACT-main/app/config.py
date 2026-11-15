# ================================================================
# app/config.py — إعدادات التطبيق والمتغيرات البيئية
# ================================================================

import os
import re
import shutil
import sys
from typing import Optional
from dotenv import load_dotenv

# تحديد المسار الأساسي للمشروع وجذر المنظومة لضمان استيراد ملف الإعدادات الموحد stamp_settings.py
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WORKSPACE_ROOT_DIR = os.path.dirname(BASE_DIR)
if WORKSPACE_ROOT_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT_DIR)

import stamp_settings  # noqa: E402,F401 — الملف المركزي لإعدادات بيانات الختم (الإجباري والاختياري)

# مسار ملف .env بشكل مطلق لضمان قراءته دائماً
env_path = os.path.join(BASE_DIR, '.env')
load_dotenv(dotenv_path=env_path)

def _detect_volume_root() -> Optional[str]:
    """اكتشاف مجلد الـ Volume الدائم على السحابة (Railway) عند توفره وقابليته للكتابة."""
    if os.name == "nt":
        return None
    for candidate in (os.getenv("RAILWAY_VOLUME_MOUNT_PATH"), "/.data", "/data"):
        if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK):
            return os.path.abspath(candidate)
    return None


def _get_or_create_jwt_secret():
    env_secret = os.getenv("JWT_SECRET_KEY")
    if env_secret and env_secret != "change-me-in-production-please":
        return env_secret

    volume_root = _detect_volume_root()
    local_secret = os.path.join(BASE_DIR, ".jwt_secret")
    vol_secret = os.path.join(volume_root, ".jwt_secret") if volume_root else None

    for candidate in (vol_secret, local_secret):
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    s = f.read().strip()
                    if len(s) >= 32:
                        if vol_secret and candidate != vol_secret:
                            try:
                                with open(vol_secret, "w", encoding="utf-8") as vf:
                                    vf.write(s)
                            except Exception:
                                pass
                        return s
            except Exception:
                pass

    import secrets
    new_secret = secrets.token_hex(32)
    target_file = vol_secret or local_secret
    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(new_secret)
    except Exception:
        pass
    return new_secret

class Settings:
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    SECRET_KEY = _get_or_create_jwt_secret()
    INTERNAL_SERVICE_KEY = os.getenv("INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    ALGORITHM = "HS256"
    TOKEN_EXPIRE_H = 24
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
    BASE_DIR = BASE_DIR

    def _active_volume_root(self) -> Optional[str]:
        """تحديد مجلد Volume المستمر (إن وُجد وكان قابلاً للكتابة) مع تهيئته بالبيانات المرفقة عند أول تشغيل."""
        return _detect_volume_root()

    def _seed_volume_once(self, volume_root: str) -> None:
        """نسخ قاعدة البيانات والأصول المرفقة إلى الـ Volume مرة واحدة فقط إذا كان فارغاً."""
        bundled_storage = os.path.join(self.BASE_DIR, "storage")
        bundled_db = os.path.join(bundled_storage, "webpdf_elite.db")
        vol_db = os.path.join(volume_root, "webpdf_elite.db")
        vol_storage = os.path.join(volume_root, "storage")

        if os.path.exists(bundled_db) and not os.path.exists(vol_db):
            try:
                tmp_db = f"{vol_db}.seed.tmp"
                shutil.copy2(bundled_db, tmp_db)
                os.replace(tmp_db, vol_db)
            except Exception:
                pass

        for sub in ("originals", "covers", "frames", "logos", "materials", "receipts", "watermarks"):
            src_sub = os.path.join(bundled_storage, sub)
            dst_sub = os.path.join(vol_storage, sub)
            if os.path.isdir(src_sub) and not os.path.exists(dst_sub):
                try:
                    shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)
                except Exception:
                    pass

    @property
    def DATABASE_URL(self) -> str:
        env_url = os.getenv("DATABASE_URL")
        if env_url:
            if env_url.startswith("postgres://"):
                env_url = env_url.replace("postgres://", "postgresql://", 1)
            return env_url

        volume_root = self._active_volume_root()
        if volume_root:
            self._seed_volume_once(volume_root)
            vol_db = os.path.join(volume_root, "webpdf_elite.db")
            if os.path.exists(vol_db) or self.ENVIRONMENT == "production":
                return f"sqlite:///{vol_db}"

        # في بيئة التطوير المحلية أو السحابة بدون Volume خارجي، نستخدم القاعدة المرفقة داخل storage
        local_db = os.path.join(self.BASE_DIR, "storage", "webpdf_elite.db")
        if os.path.exists(local_db):
            return f"sqlite:///{local_db}"
        if os.name == "nt" and os.path.exists("C:/.data/webpdf_elite.db"):
            return "sqlite:////.data/webpdf_elite.db"
        return f"sqlite:///{local_db}"

    @property
    def DATABASE_PATH(self) -> str:
        """تحويل DATABASE_URL إلى مسار ملف مطلق (لأغراض SQLite)"""
        url = self.DATABASE_URL
        if not url.startswith("sqlite:///"):
            return url  # مش SQLite أصلاً

        # إزالة البادئة "sqlite:///" (أول 10 حروف)
        path = url[10:]

        # 1. إذا كان مساراً مطلقاً (يبدأ بـ / في لينكس أو C:\ في ويندوز)
        if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            return os.path.abspath(path)

        # 2. إذا كان يبدأ بـ ./ نعتبره نسبياً لـ BASE_DIR
        if path.startswith("./"):
            return os.path.abspath(os.path.join(self.BASE_DIR, path[2:]))

        # 3. أي مسار آخر نعتبره نسبياً لـ BASE_DIR
        return os.path.abspath(os.path.join(self.BASE_DIR, path))

    @property
    def STORAGE_DIR(self) -> str:
        """مسار تخزين الملفات (Originals, Stamped, Previews)"""
        volume_root = self._active_volume_root()
        if volume_root:
            self._seed_volume_once(volume_root)
            path = os.path.join(volume_root, "storage")
        else:
            path = os.path.join(self.BASE_DIR, "storage")

        os.makedirs(path, exist_ok=True)
        return path

    def resolve_storage_path(self, raw_path: Optional[str], default_subdir: str = "originals") -> Optional[str]:
        """تحويل المسارات المخزنة (سواء ويندوز مطلقة أو نسبية) إلى مسار فعلي صالح على أي نظام."""
        if not raw_path:
            return raw_path

        text = str(raw_path).strip()
        if os.path.exists(text):
            return os.path.abspath(text)

        normalized = text.replace("\\", "/")
        basename = normalized.rstrip("/").split("/")[-1]

        rel_tail = None
        match = re.search(r"(?:^|/)storage/(.+)$", normalized, flags=re.IGNORECASE)
        if match:
            rel_tail = match.group(1).lstrip("/")

        candidates = []
        if rel_tail:
            rel_os = rel_tail.replace("/", os.sep)
            candidates.append(os.path.join(self.STORAGE_DIR, rel_os))
            candidates.append(os.path.join(self.BASE_DIR, "storage", rel_os))

        if not normalized.startswith("/") and not (len(normalized) > 1 and normalized[1] == ":"):
            norm_os = normalized.replace("/", os.sep)
            candidates.append(os.path.join(self.BASE_DIR, norm_os))
            candidates.append(os.path.join(self.STORAGE_DIR, norm_os))

        if basename:
            candidates.append(os.path.join(self.STORAGE_DIR, default_subdir, basename))
            candidates.append(os.path.join(self.BASE_DIR, "storage", default_subdir, basename))

        seen = set()
        for cand in candidates:
            abs_cand = os.path.abspath(cand)
            if abs_cand in seen:
                continue
            seen.add(abs_cand)
            if os.path.exists(abs_cand):
                return abs_cand

        return os.path.abspath(candidates[0]) if candidates else text

    def to_portable_storage_path(self, path: str, default_subdir: str = "originals") -> str:
        """توحيد صيغة المسار قبل حفظه في قاعدة البيانات (storage/<subdir>/<file>)."""
        normalized = str(path).strip().replace("\\", "/")
        match = re.search(r"(?:^|/)storage/(.+)$", normalized, flags=re.IGNORECASE)
        if match:
            return f"storage/{match.group(1).lstrip('/')}"
        basename = normalized.rstrip("/").split("/")[-1]
        return f"storage/{default_subdir}/{basename}" if basename else normalized

settings = Settings()
