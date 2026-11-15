# ================================================================
# app/worker.py — عامل النظافة والصيانة وإدارة المساحة (Cleanup Worker)
# ================================================================

import asyncio
import os
import sqlite3
import time
import shutil
from datetime import datetime
from typing import Callable, Optional, Tuple
from sqlalchemy.orm import Session
from app import models, database
from app.config import settings

from app.logger import logger

# مسار تخزين الملفات المختومة المرفوعة (المسار المطلق)
STAMPED_DIR = os.path.join(settings.STORAGE_DIR, "stamped")

# فترات الاحتفاظ القابلة للضبط عبر متغيرات البيئة (افتراضيات متوازنة للسحابة)
STAMPED_RETENTION_SECONDS = max(300, int(os.getenv("STAMPED_RETENTION_HOURS", "12")) * 3600)
BATCH_STAMPED_RETENTION_SECONDS = max(120, int(os.getenv("BATCH_STAMPED_RETENTION_MINUTES", "30")) * 60)
EPHEMERAL_TEMP_RETENTION_SECONDS = max(120, int(os.getenv("EPHEMERAL_TEMP_RETENTION_MINUTES", "60")) * 60)
CLEANUP_INTERVAL_SECONDS = max(60, int(os.getenv("ZPACT_CLEANUP_INTERVAL_SECONDS", "600")))
DEFAULT_MIN_FREE_MB = float(os.getenv("ZPACT_MIN_FREE_MB", "120"))
MAX_DB_BACKUPS = max(1, int(os.getenv("ZPACT_MAX_BACKUPS", "3")))


def get_disk_free_mb(path: Optional[str] = None) -> float:
    """إرجاع المساحة الحرة بالميجابايت على القسم الذي يضم مجلد التخزين."""
    target = path or settings.STORAGE_DIR
    try:
        if not os.path.exists(target):
            target = os.path.dirname(target) or "."
        usage = shutil.disk_usage(target)
        return usage.free / (1024.0 * 1024.0)
    except Exception:
        return 9999.0


def safe_remove_file(path: Optional[str]) -> int:
    """حذف ملف بأمان وإرجاع عدد البايتات المحررة دون رمي استثناءات."""
    if not path or not os.path.isfile(path):
        return 0
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    try:
        os.remove(path)
        return size
    except OSError:
        return 0


def _is_within_dir(parent_dir: str, candidate_path: str) -> bool:
    """التحقق الأمني أن المسار يقع فعلياً داخل المجلد المستهدف فقط."""
    try:
        parent_abs = os.path.abspath(parent_dir)
        cand_abs = os.path.abspath(candidate_path)
        return os.path.commonpath([parent_abs, cand_abs]) == parent_abs and cand_abs != parent_abs
    except Exception:
        return False


def _get_stamped_dir() -> str:
    return STAMPED_DIR


def _prune_dir_by_age(
    directory: str,
    max_age_seconds: float,
    *,
    now: Optional[float] = None,
    name_filter: Optional[Callable[[str, str], bool]] = None,
) -> Tuple[int, int]:
    """حذف الملفات داخل مجلد واحد أقدم من المدة المحددة دون المساس بالمجلدات أو .gitkeep."""
    if not directory or not os.path.isdir(directory):
        return 0, 0
    ts_now = now if now is not None else time.time()
    deleted = 0
    freed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0, 0

    for entry in entries:
        if entry.name in (".gitkeep", ".DS_Store"):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if name_filter and not name_filter(entry.name, entry.path):
                continue
            stat = entry.stat(follow_symlinks=False)
            if (ts_now - stat.st_mtime) >= max_age_seconds:
                size = safe_remove_file(entry.path)
                if size or not os.path.exists(entry.path):
                    deleted += 1
                    freed += size
        except OSError:
            continue
    return deleted, freed


def _prune_backups(max_keep: int = MAX_DB_BACKUPS) -> Tuple[int, int]:
    """الاحتفاظ بآخر نسخ احتياطية فقط لقاعدة البيانات لتفادي امتلاء القرص."""
    backup_dir = os.path.join(settings.STORAGE_DIR, "backups")
    if not os.path.isdir(backup_dir):
        return 0, 0
    try:
        backups = sorted(
            f for f in os.listdir(backup_dir)
            if f.endswith(".db") and os.path.isfile(os.path.join(backup_dir, f))
        )
    except OSError:
        return 0, 0
    if len(backups) <= max_keep:
        return 0, 0
    deleted = 0
    freed = 0
    for old_name in backups[:-max_keep]:
        fpath = os.path.join(backup_dir, old_name)
        sz = safe_remove_file(fpath)
        if not os.path.exists(fpath):
            deleted += 1
            freed += sz
    return deleted, freed


def _checkpoint_sqlite_wal() -> None:
    """دمج وتقليص ملفات WAL لقاعدتي البيانات الأساسية والبوت عند التنظيف أو ضغط القرص."""
    candidates = []
    if "sqlite" in settings.DATABASE_URL:
        candidates.append(settings.DATABASE_PATH)
    candidates.append(os.path.join(settings.STORAGE_DIR, "bot_fsm_state.db"))
    for db_path in candidates:
        if db_path and os.path.isfile(db_path):
            try:
                with sqlite3.connect(db_path, timeout=5.0) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass


def _prune_batch_delivered_stamped(now: float, max_age_seconds: float) -> Tuple[int, int]:
    """تنظيف ملفات الـ PDF الخاصة بالدفعات أو الطلبات المسلّمة التي انتهت مهلتها المؤقتة."""
    stamped_dir = _get_stamped_dir()
    if not os.path.isdir(stamped_dir):
        return 0, 0
    deleted = 0
    freed = 0
    db = database.SessionLocal()
    try:
        rows = (
            db.query(models.BotOrder)
            .filter(
                models.BotOrder.stamped_pdf_path.isnot(None),
                models.BotOrder.status.in_([
                    models.OrderFulfillmentStatus.DELIVERED,
                    models.OrderFulfillmentStatus.FAILED,
                    models.OrderFulfillmentStatus.CANCELLED,
                ]),
            )
            .all()
        )
        dirty = False
        for order in rows:
            is_ephemeral_batch = (
                (order.order_code or "").startswith("BAT-")
                or (order.payment_method or "") == "admin_batch"
            )
            ttl = max_age_seconds if is_ephemeral_batch else STAMPED_RETENTION_SECONDS
            paths_to_check = [order.stamped_pdf_path]
            if order.purchase and order.purchase.output_pdf_path:
                paths_to_check.append(order.purchase.output_pdf_path)

            for p in paths_to_check:
                if not p or not os.path.isfile(p):
                    continue
                if not _is_within_dir(stamped_dir, p):
                    continue
                try:
                    if (now - os.path.getmtime(p)) >= ttl:
                        sz = safe_remove_file(p)
                        if not os.path.exists(p):
                            deleted += 1
                            freed += sz
                            order.stamped_pdf_path = None
                            dirty = True
                except OSError:
                    continue
        if dirty:
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
    return deleted, freed


def _evict_oldest_stamped(target_free_mb: float, min_recent_protect_sec: float = 120.0) -> Tuple[int, int]:
    """تنظيف طارئ لأقدم الملفات المختومة في stamped عند انخفاض مساحة القرص عن الحد الآمن."""
    stamped_dir = _get_stamped_dir()
    if not os.path.isdir(stamped_dir):
        return 0, 0
    now = time.time()
    candidates = []
    try:
        for entry in os.scandir(stamped_dir):
            if entry.name in (".gitkeep", ".DS_Store"):
                continue
            try:
                if entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    if (now - st.st_mtime) >= min_recent_protect_sec:
                        candidates.append((st.st_mtime, entry.path))
            except OSError:
                continue
    except OSError:
        return 0, 0

    candidates.sort(key=lambda x: x[0])
    deleted = 0
    freed = 0
    for _, fpath in candidates:
        if get_disk_free_mb(stamped_dir) >= target_free_mb:
            break
        sz = safe_remove_file(fpath)
        if not os.path.exists(fpath):
            deleted += 1
            freed += sz
    return deleted, freed


def cleanup_order_stamped_file(db: Session, order_code: str) -> bool:
    """حذف ملف الـ PDF المختوم المؤقت لطلب من مجلد stamped فقط بعد تأكيد تسليمه للتيليجرام."""
    order = db.query(models.BotOrder).filter(models.BotOrder.order_code == order_code).first()
    if not order:
        return False

    stamped_roots = []
    for root in (_get_stamped_dir(), os.path.join(settings.STORAGE_DIR, "stamped")):
        abs_root = os.path.abspath(root)
        if abs_root not in stamped_roots:
            stamped_roots.append(abs_root)

    candidates = []
    for p in (order.stamped_pdf_path, order.purchase.output_pdf_path if order.purchase else None):
        if p and p not in candidates:
            candidates.append(p)

    removed_any = False
    for p in candidates:
        if not os.path.isfile(p):
            continue
        if not any(_is_within_dir(root, p) for root in stamped_roots):
            continue
        freed = safe_remove_file(p)
        if not os.path.exists(p):
            removed_any = True
            logger.debug(f"🧹 [Cleanup] Removed delivered temporary stamped PDF for {order_code} ({freed // 1024}KB)")

    if order.stamped_pdf_path and not os.path.exists(order.stamped_pdf_path):
        try:
            order.stamped_pdf_path = None
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
    return removed_any


def ensure_disk_headroom(min_free_mb: float = 80.0, hard_min_mb: float = 20.0) -> Tuple[bool, float]:
    """
    التأكد من توفر مساحة كافية على القرص قبل بدء الختم أو النسخ الاحتياطي.
    إذا كانت المساحة منخفضة ينفذ تنظيفاً فورياً ويعيد (هل_المساحة_كافية, المساحة_الحرة_MB).
    """
    free_before = get_disk_free_mb()
    if free_before >= min_free_mb:
        return True, free_before
    clean_stamped_files(force_min_free_mb=min_free_mb)
    free_after = get_disk_free_mb()
    return (free_after >= hard_min_mb), free_after


async def start_cleanup_worker():
    """مهمة خلفية تعمل دورياً لتنظيف الملفات المؤقتة وحماية مساحة القرص."""
    storage_free = get_disk_free_mb()
    logger.info(
        f"🧹 [Worker] Cleanup worker started | STORAGE_DIR={settings.STORAGE_DIR} | Free={storage_free:.1f}MB"
    )

    while True:
        try:
            await asyncio.to_thread(clean_stamped_files)
            await asyncio.to_thread(clean_expired_tokens)
            from app.routers.purchases import _download_tokens
            clean_memory_tokens(_download_tokens)
        except Exception as e:
            logger.error(f"⚠️ [Worker Error] {e}")

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def clean_stamped_files(force_min_free_mb: Optional[float] = None) -> dict:
    """
    تنظيف الملفات المؤقتة فقط (stamped / previews / bot_tmp / excess backups) مع عدم المساس
    أبداً بالمذكرات الأصلية أو الأغلفة أو الإطارات أو اللوجوهات أو قاعدة البيانات.
    """
    now = time.time()
    stamped_dir = _get_stamped_dir()
    free_before = get_disk_free_mb(stamped_dir if os.path.exists(stamped_dir) else None)
    total_deleted = 0
    total_freed = 0

    def _add(res: Tuple[int, int]) -> None:
        nonlocal total_deleted, total_freed
        total_deleted += res[0]
        total_freed += res[1]

    # 1. ملفات الختم الوسيطة أو المؤقتة المعطوبة (.tmp / *_stamped.pdf / 0-byte) أقدم من 15 دقيقة
    _add(_prune_dir_by_age(
        stamped_dir,
        900.0,
        now=now,
        name_filter=lambda name, path: (
            name.endswith("_stamped.pdf")
            or ".tmp" in name
            or os.path.getsize(path) == 0
        ),
    ))

    # 2. ملفات الدفعات المسلمة via Bot + بقية الملفات المختومة الأقدم من فترة الاحتفاظ
    _add(_prune_batch_delivered_stamped(now, BATCH_STAMPED_RETENTION_SECONDS))
    _add(_prune_dir_by_age(stamped_dir, float(STAMPED_RETENTION_SECONDS), now=now))

    # 3. المعاينات والملفات المؤقتة للبوت والنسخ الاحتياطية الزائدة
    previews_dir = os.path.join(settings.STORAGE_DIR, "previews")
    _add(_prune_dir_by_age(previews_dir, float(EPHEMERAL_TEMP_RETENTION_SECONDS), now=now))

    for tmp_dir in {
        os.path.join(settings.STORAGE_DIR, "bot_tmp"),
        os.path.join(settings.BASE_DIR, "storage", "bot_tmp"),
    }:
        _add(_prune_dir_by_age(tmp_dir, float(EPHEMERAL_TEMP_RETENTION_SECONDS), now=now))

    _add(_prune_backups(MAX_DB_BACKUPS))

    # 4. وضع الطوارئ عند انخفاض المساحة الحرة على القرص عن الحد الآمن
    target_mb = float(force_min_free_mb if force_min_free_mb is not None else DEFAULT_MIN_FREE_MB)
    if get_disk_free_mb(stamped_dir if os.path.exists(stamped_dir) else None) < target_mb:
        _checkpoint_sqlite_wal()
        _add(_prune_dir_by_age(previews_dir, 300.0, now=now))
        for tmp_dir in {
            os.path.join(settings.STORAGE_DIR, "bot_tmp"),
            os.path.join(settings.BASE_DIR, "storage", "bot_tmp"),
        }:
            _add(_prune_dir_by_age(tmp_dir, 300.0, now=now))
        _add(_prune_backups(1))
        _add(_evict_oldest_stamped(target_mb, min_recent_protect_sec=120.0))

    free_after = get_disk_free_mb(stamped_dir if os.path.exists(stamped_dir) else None)
    if total_deleted > 0 or free_after < target_mb:
        logger.info(
            f"🧹 [Worker] Cleaned {total_deleted} ephemeral file(s) "
            f"({total_freed / (1024.0 * 1024.0):.1f}MB freed) | "
            f"Disk Free: {free_before:.1f}MB -> {free_after:.1f}MB"
        )
    return {
        "deleted_files": total_deleted,
        "freed_bytes": total_freed,
        "free_mb_before": round(free_before, 2),
        "free_mb_after": round(free_after, 2),
    }


def clean_expired_tokens():
    """حذف توكنات المعاينة المنتهية من قاعدة البيانات"""
    db = database.SessionLocal()
    try:
        now = datetime.utcnow()
        deleted = db.query(models.PreviewToken).filter(models.PreviewToken.expires_at < now).delete()
        db.commit()
        if deleted > 0:
            logger.info(f"🧹 [Worker] Removed {deleted} expired preview tokens from DB.")
    finally:
        db.close()


def clean_memory_tokens(tokens_dict: dict):
    """تنظيف توكنات التحميل المخزنة في الذاكرة بأمان"""
    now = datetime.utcnow()
    to_delete = []
    for token, data in list(tokens_dict.items()):
        if data.get("expires_at") and data.get("expires_at") < now:
            to_delete.append(token)

    for token in to_delete:
        tokens_dict.pop(token, None)

    if len(to_delete) > 0:
        logger.info(f"🧹 [Worker] Cleared {len(to_delete)} expired download tokens from memory.")
