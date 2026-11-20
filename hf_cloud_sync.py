# ================================================================
# hf_cloud_sync.py — نظام الحفظ السحابي المجاني 100% عبر Private Hugging Face Dataset
# يضمن عدم فقدان قاعدة البيانات أو المذكرات أو الإطارات أو اللوجوهات عند إعادة تشغيل
# Hugging Face Spaces المجاني، كما يُستخدم لنقل كامل بيانات Railway إلى HF بدون تكلفة.
# ================================================================

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("zpact.hf_sync")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.join(ROOT_DIR, "Z-PACT-main")

# المجلدات الدائمة فقط (لا نرفع المخرجات المؤقتة مثل stamped/previews/bot_tmp)
PERSISTENT_SUBDIRS = (
    "originals",
    "covers",
    "frames",
    "logos",
    "materials",
    "receipts",
    "watermarks",
)
PERSISTENT_ROOT_FILES = (
    "bot_recent_batches.json",
    "bot_admins.json",
)

_SYNC_STATE_FILE_NAME = ".hf_sync_manifest.json"


def _detect_active_dirs() -> Tuple[str, str, Optional[str]]:
    """
    تحديد المسارات الفعلية الحية لقاعدة البيانات ومجلد storage ومجلد الـ Volume (إن وُجد).
    يُرجع: (db_path, storage_dir, volume_root)
    """
    volume_root: Optional[str] = None
    if os.name != "nt":
        for cand in (os.getenv("RAILWAY_VOLUME_MOUNT_PATH"), "/.data", "/data"):
            if cand and os.path.isdir(cand) and os.access(cand, os.W_OK):
                volume_root = os.path.abspath(cand)
                break

    if volume_root:
        db_path = os.path.join(volume_root, "webpdf_elite.db")
        storage_dir = os.path.join(volume_root, "storage")
    else:
        storage_dir = os.path.join(CORE_DIR, "storage")
        db_path = os.path.join(storage_dir, "webpdf_elite.db")

    os.makedirs(storage_dir, exist_ok=True)
    return db_path, storage_dir, volume_root


def get_hf_sync_config() -> Dict[str, Any]:
    token = (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or "").strip()
    repo_id = (os.getenv("HF_BACKUP_REPO") or os.getenv("HF_DATASET_REPO") or "").strip()
    mode = (os.getenv("HF_SYNC_MODE") or "auto").strip().lower()  # auto | push_only | pull_only | off
    try:
        interval_sec = max(60, int(os.getenv("HF_SYNC_INTERVAL_SEC", "300")))
    except ValueError:
        interval_sec = 300
    enabled = bool(token and repo_id and mode != "off")
    return {
        "enabled": enabled,
        "token": token,
        "repo_id": repo_id,
        "mode": mode,
        "interval_sec": interval_sec,
    }


def is_hf_sync_enabled() -> bool:
    return bool(get_hf_sync_config()["enabled"])


def _safe_sqlite_backup(src_db: str, dst_db: str) -> bool:
    """إنشاء نسخة متماسكة وآمنة من SQLite حتى أثناء الكتابة النشطة (WAL-safe)."""
    if not os.path.isfile(src_db):
        return False
    os.makedirs(os.path.dirname(dst_db), exist_ok=True)
    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(src_db, timeout=30.0)
        dst_conn = sqlite3.connect(dst_db)
        src_conn.backup(dst_conn)
        dst_conn.commit()
        return True
    except Exception as e:
        logger.warning(f"⚠️ [HF Sync] تعذر النسخ الآمن لـ SQLite عبر backup API ({e}) — محاولة نسخ مباشر")
        try:
            shutil.copy2(src_db, dst_db)
            return True
        except Exception:
            return False
    finally:
        for conn in (dst_conn, src_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_local_fingerprint(db_path: str, storage_dir: str, volume_root: Optional[str]) -> Dict[str, str]:
    """بصمة سريعة (الحجم + وقت التعديل) لاكتشاف أي تغيير فعلي قبل الرفع."""
    fp: Dict[str, str] = {}
    for p in (db_path, f"{db_path}-wal"):
        if os.path.isfile(p):
            st = os.stat(p)
            fp[os.path.basename(p)] = f"{st.st_size}:{st.st_mtime_ns}"

    for secret_name, cand_paths in (
        (".jwt_secret", [os.path.join(volume_root, ".jwt_secret")] if volume_root else [os.path.join(CORE_DIR, ".jwt_secret")]),
        (".zpact_runtime.env", [os.path.join(volume_root, ".zpact_runtime.env")] if volume_root else [os.path.join(ROOT_DIR, ".zpact_runtime.env")]),
    ):
        for cp in cand_paths:
            if os.path.isfile(cp):
                st = os.stat(cp)
                fp[f"meta/{secret_name}"] = f"{st.st_size}:{st.st_mtime_ns}"
                break

    for fname in PERSISTENT_ROOT_FILES:
        fpath = os.path.join(storage_dir, fname)
        if os.path.isfile(fpath):
            st = os.stat(fpath)
            fp[f"storage/{fname}"] = f"{st.st_size}:{st.st_mtime_ns}"

    for sub in PERSISTENT_SUBDIRS:
        sub_dir = os.path.join(storage_dir, sub)
        if not os.path.isdir(sub_dir):
            continue
        for root, _, files in os.walk(sub_dir):
            for fn in files:
                if fn.startswith(".") or fn.endswith((".tmp", ".part")):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, storage_dir).replace("\\", "/")
                try:
                    st = os.stat(full)
                    fp[f"storage/{rel}"] = f"{st.st_size}:{st.st_mtime_ns}"
                except OSError:
                    continue
    return fp


def _load_manifest(storage_dir: str) -> Dict[str, str]:
    mpath = os.path.join(storage_dir, _SYNC_STATE_FILE_NAME)
    if not os.path.isfile(mpath):
        return {}
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_manifest(storage_dir: str, fp: Dict[str, str]) -> None:
    mpath = os.path.join(storage_dir, _SYNC_STATE_FILE_NAME)
    try:
        tmp = f"{mpath}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(fp, f, ensure_ascii=False)
        os.replace(tmp, mpath)
    except Exception:
        pass


def restore_from_hf_dataset() -> Dict[str, Any]:
    """
    استعادة أحدث نسخة من قاعدة البيانات والملفات من Private Hugging Face Dataset عند بدء التشغيل.
    """
    cfg = get_hf_sync_config()
    if not cfg["enabled"]:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if cfg["mode"] == "push_only":
        return {"ok": True, "skipped": True, "reason": "push_only"}

    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError:
        logger.warning("⚠️ [HF Sync] مكتبة huggingface_hub غير مثبتة — تم تخطي الاستعادة السحابية.")
        return {"ok": False, "error": "huggingface_hub_not_installed"}

    db_path, storage_dir, volume_root = _detect_active_dirs()
    api = HfApi(token=cfg["token"])

    try:
        api.create_repo(
            repo_id=cfg["repo_id"],
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )
        remote_files = api.list_repo_files(repo_id=cfg["repo_id"], repo_type="dataset")
    except Exception as e:
        logger.error(f"❌ [HF Sync] تعذر الاتصال بمستودع البيانات {cfg['repo_id']}: {e}")
        return {"ok": False, "error": str(e)}

    if "webpdf_elite.db" not in remote_files and not any(f.startswith("storage/") for f in remote_files):
        logger.info(f"ℹ️ [HF Sync] مستودع البيانات {cfg['repo_id']} فارغ حالياً — سيتم تهيئته من البيانات المحلية.")
        return {"ok": True, "restored_files": 0, "empty_remote": True}

    restored_count = 0
    with tempfile.TemporaryDirectory(prefix="zpact_hf_pull_") as tmp_dir:
        snapshot_download(
            repo_id=cfg["repo_id"],
            repo_type="dataset",
            token=cfg["token"],
            local_dir=tmp_dir,
        )

        # 1. استعادة قاعدة البيانات بأمان وحذف أي ملفات WAL قديمة غير متوافقة
        remote_db = os.path.join(tmp_dir, "webpdf_elite.db")
        if os.path.isfile(remote_db) and os.path.getsize(remote_db) > 0:
            for sidecar in (f"{db_path}-wal", f"{db_path}-shm", f"{db_path}-journal"):
                if os.path.exists(sidecar):
                    try:
                        os.remove(sidecar)
                    except OSError:
                        pass
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            tmp_db = f"{db_path}.restore.tmp"
            shutil.copy2(remote_db, tmp_db)
            os.replace(tmp_db, db_path)
            restored_count += 1

        # 2. استعادة ملفات الإعدادات السرية إن وُجدت
        meta_dir = os.path.join(tmp_dir, "meta")
        if os.path.isdir(meta_dir):
            jwt_src = os.path.join(meta_dir, ".jwt_secret")
            if os.path.isfile(jwt_src):
                jwt_dst = os.path.join(volume_root, ".jwt_secret") if volume_root else os.path.join(CORE_DIR, ".jwt_secret")
                shutil.copy2(jwt_src, jwt_dst)
                restored_count += 1
            env_src = os.path.join(meta_dir, ".zpact_runtime.env")
            if os.path.isfile(env_src):
                env_dst = os.path.join(volume_root, ".zpact_runtime.env") if volume_root else os.path.join(ROOT_DIR, ".zpact_runtime.env")
                shutil.copy2(env_src, env_dst)
                restored_count += 1

        # 3. استعادة جميع ملفات التخزين الدائمة (originals, covers, frames, logos, ...)
        remote_storage = os.path.join(tmp_dir, "storage")
        if os.path.isdir(remote_storage):
            for root, _, files in os.walk(remote_storage):
                for fn in files:
                    if fn.startswith("."):
                        continue
                    src_f = os.path.join(root, fn)
                    rel = os.path.relpath(src_f, remote_storage)
                    dst_f = os.path.join(storage_dir, rel)
                    os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                    shutil.copy2(src_f, dst_f)
                    restored_count += 1

    # حفظ البصمة الحالية بعد الاستعادة حتى لا نعيد رفع نفس الملفات فوراً بلا داعي
    _save_manifest(storage_dir, _collect_local_fingerprint(db_path, storage_dir, volume_root))
    logger.info(f"✅ [HF Sync] تمت استعادة {restored_count} ملف من {cfg['repo_id']} بنجاح.")
    return {"ok": True, "restored_files": restored_count, "repo_id": cfg["repo_id"]}


def backup_to_hf_dataset(force: bool = False) -> Dict[str, Any]:
    """
    رفع التغييرات الجديدة فقط (قاعدة البيانات + الأصول الدائمة) إلى Private Hugging Face Dataset.
    """
    cfg = get_hf_sync_config()
    if not cfg["enabled"]:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if cfg["mode"] == "pull_only":
        return {"ok": True, "skipped": True, "reason": "pull_only"}

    try:
        from huggingface_hub import CommitOperationAdd, HfApi
    except ImportError:
        return {"ok": False, "error": "huggingface_hub_not_installed"}

    db_path, storage_dir, volume_root = _detect_active_dirs()
    current_fp = _collect_local_fingerprint(db_path, storage_dir, volume_root)
    prev_fp = {} if force else _load_manifest(storage_dir)

    changed_keys = [k for k, v in current_fp.items() if prev_fp.get(k) != v]
    if not changed_keys and not force:
        return {"ok": True, "uploaded_files": 0, "unchanged": True}

    api = HfApi(token=cfg["token"])
    try:
        api.create_repo(
            repo_id=cfg["repo_id"],
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )
    except Exception as e:
        logger.error(f"❌ [HF Sync] تعذر تجهيز المستودع {cfg['repo_id']}: {e}")
        return {"ok": False, "error": str(e)}

    operations: List[Any] = []
    with tempfile.TemporaryDirectory(prefix="zpact_hf_push_") as tmp_dir:
        db_changed = force or any(k in changed_keys for k in ("webpdf_elite.db", "webpdf_elite.db-wal"))
        if db_changed and os.path.isfile(db_path):
            snap_db = os.path.join(tmp_dir, "webpdf_elite.db")
            if _safe_sqlite_backup(db_path, snap_db):
                operations.append(CommitOperationAdd(path_in_repo="webpdf_elite.db", path_or_fileobj=snap_db))

        for k in changed_keys:
            if k in ("webpdf_elite.db", "webpdf_elite.db-wal"):
                continue
            if k == "meta/.jwt_secret":
                src = os.path.join(volume_root, ".jwt_secret") if volume_root else os.path.join(CORE_DIR, ".jwt_secret")
                if os.path.isfile(src):
                    operations.append(CommitOperationAdd(path_in_repo="meta/.jwt_secret", path_or_fileobj=src))
            elif k == "meta/.zpact_runtime.env":
                src = os.path.join(volume_root, ".zpact_runtime.env") if volume_root else os.path.join(ROOT_DIR, ".zpact_runtime.env")
                if os.path.isfile(src):
                    operations.append(CommitOperationAdd(path_in_repo="meta/.zpact_runtime.env", path_or_fileobj=src))
            elif k.startswith("storage/"):
                rel = k[len("storage/"):]
                src = os.path.join(storage_dir, rel.replace("/", os.sep))
                if os.path.isfile(src):
                    operations.append(CommitOperationAdd(path_in_repo=k, path_or_fileobj=src))

        if not operations:
            _save_manifest(storage_dir, current_fp)
            return {"ok": True, "uploaded_files": 0, "unchanged": True}

        try:
            api.create_commit(
                repo_id=cfg["repo_id"],
                repo_type="dataset",
                operations=operations,
                commit_message=f"Z-PACT auto-sync ({len(operations)} file(s)) @ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            )
            _save_manifest(storage_dir, current_fp)
            logger.info(f"☁️ [HF Sync] تم حفظ {len(operations)} ملف بأمان في {cfg['repo_id']}.")
            return {"ok": True, "uploaded_files": len(operations), "repo_id": cfg["repo_id"]}
        except Exception as e:
            logger.error(f"❌ [HF Sync] فشل رفع النسخة الاحتياطية إلى {cfg['repo_id']}: {e}")
            return {"ok": False, "error": str(e)}


def export_full_migration_zip(out_zip_path: str) -> Dict[str, Any]:
    """
    تصدير حزمة هجرة كاملة (.zip) تحتوي على نسخة SQLite آمنة + كل المذكرات والإطارات واللوجوهات والإعدادات.
    """
    import zipfile

    db_path, storage_dir, volume_root = _detect_active_dirs()
    file_count = 0
    with tempfile.TemporaryDirectory(prefix="zpact_zip_") as tmp_dir:
        with zipfile.ZipFile(out_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if os.path.isfile(db_path):
                snap_db = os.path.join(tmp_dir, "webpdf_elite.db")
                if _safe_sqlite_backup(db_path, snap_db):
                    zf.write(snap_db, arcname="webpdf_elite.db")
                    file_count += 1

            jwt_src = os.path.join(volume_root, ".jwt_secret") if volume_root else os.path.join(CORE_DIR, ".jwt_secret")
            if os.path.isfile(jwt_src):
                zf.write(jwt_src, arcname="meta/.jwt_secret")
                file_count += 1

            env_src = os.path.join(volume_root, ".zpact_runtime.env") if volume_root else os.path.join(ROOT_DIR, ".zpact_runtime.env")
            if os.path.isfile(env_src):
                zf.write(env_src, arcname="meta/.zpact_runtime.env")
                file_count += 1

            for fname in PERSISTENT_ROOT_FILES:
                fpath = os.path.join(storage_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f"storage/{fname}")
                    file_count += 1

            for sub in PERSISTENT_SUBDIRS:
                sub_dir = os.path.join(storage_dir, sub)
                if not os.path.isdir(sub_dir):
                    continue
                for root, _, files in os.walk(sub_dir):
                    for fn in files:
                        if fn.startswith(".") or fn.endswith((".tmp", ".part")):
                            continue
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, storage_dir).replace("\\", "/")
                        zf.write(full, arcname=f"storage/{rel}")
                        file_count += 1

    return {"ok": True, "zip_path": out_zip_path, "file_count": file_count}


def import_full_migration_zip(zip_path: str) -> Dict[str, Any]:
    """
    استيراد حزمة هجرة كاملة (.zip) لاستعادة قاعدة البيانات وكل الأصول الدائمة دفعة واحدة بأمان.
    """
    import zipfile

    db_path, storage_dir, volume_root = _detect_active_dirs()
    restored = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").lstrip("/")
            if ".." in name.split("/"):
                continue

            if name == "webpdf_elite.db":
                for sidecar in (f"{db_path}-wal", f"{db_path}-shm", f"{db_path}-journal"):
                    if os.path.exists(sidecar):
                        try:
                            os.remove(sidecar)
                        except OSError:
                            pass
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                tmp_db = f"{db_path}.import.tmp"
                with zf.open(info, "r") as src, open(tmp_db, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(tmp_db, db_path)
                restored += 1
            elif name == "meta/.jwt_secret":
                dst = os.path.join(volume_root, ".jwt_secret") if volume_root else os.path.join(CORE_DIR, ".jwt_secret")
                with zf.open(info, "r") as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                restored += 1
            elif name == "meta/.zpact_runtime.env":
                dst = os.path.join(volume_root, ".zpact_runtime.env") if volume_root else os.path.join(ROOT_DIR, ".zpact_runtime.env")
                with zf.open(info, "r") as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                restored += 1
            elif name.startswith("storage/"):
                rel = name[len("storage/"):]
                top = rel.split("/")[0]
                if top in PERSISTENT_SUBDIRS or rel in PERSISTENT_ROOT_FILES:
                    dst = os.path.join(storage_dir, rel.replace("/", os.sep))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with zf.open(info, "r") as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
                    restored += 1

    return {"ok": True, "restored_files": restored}
