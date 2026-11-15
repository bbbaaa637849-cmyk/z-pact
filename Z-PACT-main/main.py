import sys
import asyncio
import re
import os
import shutil
import logging
import time
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session

from fastapi import FastAPI, Request, Response, Depends
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if sys.version_info < (3, 14):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

from app.config import settings
from app.database import create_tables, get_db
from app.models import WalletTransaction
from app.routers import (
    auth, materials, layouts, purchases, presets, jobs, coins,
    users, chat, admin_tools, stats, internal_bridge, ecosystem_v3
)
from app.logger import logger

# مدير الجدولة في الخلفية
app_scheduler = None

# ================================================================
# Lifespan — بيتنفذ مرة واحدة عند بدء وإيقاف التطبيق
# ================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_scheduler
    # عند البدء: إنشاء الجداول وتحديث الهيكلية (Patching)
    create_tables()
    from app.database import patch_database_schema
    patch_database_schema()
    
    logger.info("✓ قاعدة البيانات جاهزة ومحدثة وفهارس السرعة مفعلة")
    
    # تنظيف الشات تلقائياً عند بدء التشغيل
    try:
        from clean_chat import clean_all_chat_messages
        clean_all_chat_messages()
        logger.info("✓ تم تنظيف رسائل الشات تلقائياً من الأكواد الخبيثة")
    except Exception as e:
        logger.error(f"⚠️ خطأ أثناء تنظيف الشات: {e}")
        
    # تشغيل عامل النظافة في الخلفية
    from app.worker import start_cleanup_worker
    asyncio.create_task(start_cleanup_worker())

    # الاستشفاء التلقائي للمهام المنقطعة أثناء الختم عند إعادة التشغيل
    try:
        from app.recovery import recover_interrupted_stamps
        asyncio.create_task(recover_interrupted_stamps(delay_seconds=2.0))
        logger.info("✓ تم تفعيل مراقب الاستشفاء التلقائي للمهام المنقطعة")
    except Exception as e:
        logger.warning(f"⚠️ تعذر جدولة الاستشفاء التلقائي: {e}")

    # تشغيل الجدولة للنسخ الاحتياطي بأمان
    try:
        if app_scheduler is None:
            app_scheduler = BackgroundScheduler(daemon=True)
            app_scheduler.add_job(run_database_backup, 'interval', hours=24)
            app_scheduler.start()
            logger.info("✓ تم تشغيل جدول النسخ الاحتياطي التلقائي")
    except Exception as e:
        logger.warning(f"⚠️ تحذير أثناء تشغيل الجدولة: {e}")
        
    yield
    # عند الإيقاف: إيقاف الجدولة ودمج سجلات SQLite WAL بأمان على القرص
    if app_scheduler and app_scheduler.running:
        try:
            app_scheduler.shutdown(wait=False)
        except Exception:
            pass
    try:
        from app.database import engine
        if "sqlite" in settings.DATABASE_URL and os.path.exists(settings.DATABASE_PATH):
            import sqlite3
            with sqlite3.connect(settings.DATABASE_PATH, timeout=10.0) as _ck:
                _ck.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        engine.dispose()
    except Exception:
        pass


# ================================================================
# إنشاء التطبيق
# ================================================================

app = FastAPI(
    title       = "WebPDF Elite API",
    description = "منصة بيع وحماية ملفات PDF بالأختام الذكية",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/api/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url   = "/api/redoc" if settings.ENVIRONMENT != "production" else None,
)

# ---------------------------------------------------------------
# ضغط GZip التلقائي لتسريع نقل البيانات 5 إلى 10 أضعاف وتقليل استهلاك الباندويث 80%
# ---------------------------------------------------------------
app.add_middleware(GZipMiddleware, minimum_size=500)

# ---------------------------------------------------------------
# كاش المتصفح الفائق للملفات الثابتة (CSS, JS, خطوط، صور) لتقليل الحمل على السيرفر 90%
# ---------------------------------------------------------------
@app.middleware("http")
async def static_cache_control_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    path = request.url.path
    if (path.startswith("/frontend/") or path.startswith("/storage/")) and not path.startswith("/storage/originals/"):
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
    return response

# ---------------------------------------------------------------
# Middleware مخصص للسماح بالوصول للشبكة الخاصة (PNA) والـ CORS القوي
# ---------------------------------------------------------------
@app.middleware("http")
async def universal_cors_middleware(request: Request, call_next):
    # نحدد الـ Origin القادم من الـ Request
    origin = request.headers.get("origin")
    req_headers = request.headers.get("access-control-request-headers", "*")
    
    # تحديد النطاقات المسموحة من المتغيرات البيئية
    allowed_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    
    allow_origin = ""
    if "*" in allowed_origins:
        allow_origin = origin or "*"
    elif origin in allowed_origins:
        allow_origin = origin
    elif allowed_origins:
        # كإجراء افتراضي لو كان النطاق غير مسموح
        allow_origin = allowed_origins[0]
    
    # لو كان طلب Preflight (OPTIONS)
    if request.method == "OPTIONS":
        response = Response()
        if allow_origin:
            response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = req_headers
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    response: Response = await call_next(request)
    
    # نضمن وجود الهيدرز في كل رد مهما كان نوعه (حتى FileResponse)
    if allow_origin:
        response.headers["Access-Control-Allow-Origin"] = allow_origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = req_headers
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    
    # 🔒 ترويسات الأمان السيبراني مع دعم فتح الميني آب والتطبيق الأصيل في كافة منصات تيليجرام (Web K / Web A / Android / Desktop)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/miniapp"):
        response.headers["Content-Security-Policy"] = "frame-ancestors *;"
    else:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    
    return response

# -----------------------------------------------------------
# Rate Limiting & Performance Middleware مع حماية استهلاك الرام
# -----------------------------------------------------------
RATE_LIMIT_STORE = {} # {ip: [timestamps]}
_LAST_RATE_CLEANUP = time.time()

@app.middleware("http")
async def rate_limit_and_stats_middleware(request: Request, call_next):
    global _LAST_RATE_CLEANUP
    start_time = time.time()

    # ===== Whitelist: استثناء SMS Webhook من قيود الـ Rate Limit =====
    if request.url.path == "/api/webhook/sms":
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        return response

    # استخراج الـ IP
    ip = request.client.host if request.client else "unknown"
    now = time.time()

    # تنظيف الذاكرة تلقائياً كل دقيقة لمنع تسريب الرام عند تدفق 100,000 مستخدم
    if now - _LAST_RATE_CLEANUP > 60:
        _LAST_RATE_CLEANUP = now
        cutoff = now - 60
        stale_keys = [k for k, v in RATE_LIMIT_STORE.items() if not v or v[-1] < cutoff]
        for k in stale_keys:
            RATE_LIMIT_STORE.pop(k, None)
    
    # تطبيق الـ Rate Limit فقط على العمليات الحساسة (POST, PUT, DELETE)
    if request.method in ["POST", "PUT", "DELETE"]:
        timestamps = [t for t in RATE_LIMIT_STORE.get(ip, []) if t > now - 60]
        
        # السماح بـ 60 طلب في الدقيقة لكل IP لمنع حظر المستخدمين
        if len(timestamps) >= 60:
            return JSONResponse(
                status_code=429,
                content={"detail": "طلبات كثيرة جداً. يرجى المحاولة بعد دقيقة."}
            )
        
        timestamps.append(now)
        RATE_LIMIT_STORE[ip] = timestamps

    response = await call_next(request)
    
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    
    return response

# ملاحظة: تم استبدال CORSMiddleware بالـ Middleware المخصص أعلاه لضمان السيطرة الكاملة


@app.post("/api/webhook/sms")
async def receive_sms(request: Request, db: Session = Depends(get_db)):
    """
    استقبال رسائل SMS الخاصة بتحويلات فودافون كاش / InstaPay / البنوك
    وحفظها تلقائياً في قاعدة البيانات.
    """
    from app.logger import logger

    # 1. محاولة استلام الطلب بأمان (JSON أو Raw Body)
    try:
        data = await request.json()
        sms_text = data.get("message", "") or data.get("text", "") or data.get("body", "")
    except Exception:
        body_bytes = await request.body()
        sms_text = body_bytes.decode("utf-8").strip()

    if not sms_text:
        return {"status": "ignored", "reason": "empty"}

    # ✅ تجاهل placeholder الـ SMS app لو لم يُضبط بعد
    if sms_text.strip().lower() in ("[clipboard_text]", "clipboard_text", ""):
        return {"status": "ignored", "reason": "placeholder"}

    # DEBUG: طباعة النص الخام الواصل للسيرفر قبل أي معالجة
    logger.info(f"📥 [رسالة جديدة وصلت للسيرفر] النص هو: {sms_text}")

    amount = None
    sender_phone = None
    source = "VodafoneCash"
    matched_pattern = None

    # ─────────────────────────────────────────────
    # 2A. فودافون كاش — العربي
    # مثال: "تم استلام مبلغ 200 جنيه من رقم 01027585126"
    # ─────────────────────────────────────────────
    m = re.search(
        r"تم استلام (?:مبلغ )?([0-9.]+)\s*(?:جنيه|ج\.م).*?(?:من (?:رقم )?|من )([0-9]{11})",
        sms_text
    )
    if m:
        amount, sender_phone = float(m.group(1)), m.group(2)
        source, matched_pattern = "VodafoneCash", "ar_vodafone"

    # ─────────────────────────────────────────────
    # 2B. فودافون كاش — الإنجليزي
    # مثال: "You have received 200 EGP from 01027585126"
    # ─────────────────────────────────────────────
    if not matched_pattern:
        m = re.search(
            r"You have received ([0-9.]+)\s*(?:EGP|LE).*?from ([0-9]{11})",
            sms_text, re.IGNORECASE
        )
        if m:
            amount, sender_phone = float(m.group(1)), m.group(2)
            source, matched_pattern = "VodafoneCash", "en_vodafone"

    # ─────────────────────────────────────────────
    # 2C. InstaPay — العربي (تم استلام مبلغ X ج.م من Y إلى حسابكم)
    # ─────────────────────────────────────────────
    if not matched_pattern:
        m = re.search(
            r"تم استلام مبلغ ([0-9.]+)\s*ج\.م من (.+?) إلى حسابكم",
            sms_text
        )
        if m:
            amount, sender_phone = float(m.group(1)), m.group(2).strip()
            source, matched_pattern = "InstaPay", "ar_instapay"

    # ─────────────────────────────────────────────
    # 2D. InstaPay — الإنجليزي
    # مثال: "You have received EGP 200 from John to your account"
    # ─────────────────────────────────────────────
    if not matched_pattern:
        m = re.search(
            r"You have received EGP ([0-9.]+) from (.+?) to your account",
            sms_text, re.IGNORECASE
        )
        if m:
            amount, sender_phone = float(m.group(1)), m.group(2).strip()
            source, matched_pattern = "InstaPay", "en_instapay"

    # ─────────────────────────────────────────────
    # 2E. تحويل فوري بنكي (CIB / بنوك أخرى)
    # مثال: "تم إضافة تحويل لحظي الي بطاقة رقم 507803******7783 بمبلغ 80 من شريف محمد فرج"
    #        رقم مرجعي 357018762882 يوم 2026-05-13 الساعه 16:00
    # ─────────────────────────────────────────────
    if not matched_pattern:
        m = re.search(
            r"تم إضافة تحويل (?:لحظي|فوري).*?بمبلغ ([0-9.]+).*?من (.+?)(?:\s+على\s+|\s+رقم مرجعي|$)",
            sms_text
        )
        if m:
            amount, sender_phone = float(m.group(1)), m.group(2).strip()
            source, matched_pattern = "InstaPay", "bank_instant"

    # ─────────────────────────────────────────────
    # 2F. Fallback عام — أي ذكر لمبلغ + رقم 11 رقم
    # يُستخدم كآخر محاولة لأي رسالة مالية غير معروفة الشكل
    # ─────────────────────────────────────────────
    if not matched_pattern:
        # ابحث عن مبلغ ورقم هاتف في أي ترتيب
        m_amount = re.search(r"(?:بمبلغ|مبلغ|EGP|LE)[\s:]*([0-9]+(?:\.[0-9]+)?)", sms_text)
        m_phone  = re.search(r"\b(01[0-9]{9})\b", sms_text)
        if m_amount and m_phone:
            amount, sender_phone = float(m_amount.group(1)), m_phone.group(1)
            source, matched_pattern = "InstaPay", "fallback_generic"

    if not matched_pattern:
        logger.info(f"⚠️ [SMS Webhook] رسالة تجاهلت (لا تطابق أي نمط): {sms_text[:80]}")
        return {"status": "ignored", "reason": "no_pattern_match"}

    logger.info(f"🔍 [SMS Webhook] نمط '{matched_pattern}' | المبلغ: {amount} | من: {sender_phone}")

    # ─────────────────────────────────────────────
    # 3. فحص التكرار (Duplicate Check)
    # نرفض لو وجدنا نفس الرقم + نفس المبلغ خلال آخر 5 دقائق
    # ─────────────────────────────────────────────
    five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
    existing = db.query(WalletTransaction).filter(
        WalletTransaction.sender_phone == str(sender_phone),
        WalletTransaction.amount == amount,
        WalletTransaction.transaction_date >= five_minutes_ago
    ).first()

    if existing:
        logger.info(f"⚠️ [SMS Webhook] تكرار مُتجاهَل: {amount} جنيه من {sender_phone}")
        return {"status": "success", "message": "Duplicate ignored"}

    # ─────────────────────────────────────────────
    # 4. حفظ التحويل في قاعدة البيانات
    # ─────────────────────────────────────────────
    new_transaction = WalletTransaction(
        sender_phone=str(sender_phone),
        amount=amount,
        source=source
    )
    db.add(new_transaction)
    db.commit()

    logger.info(f"✅ تم حفظ تحويل {source} جديد: {amount} جنيه من {sender_phone}")

    return {
        "status": "success",
        "amount": amount,
        "phone": sender_phone,
        "source": source
    }

# ───────────────────────────────────────────────────────────────
# 🔄 Automated Daily Backups (Phase 4)
# ───────────────────────────────────────────────────────────────
def run_database_backup():
    """وظيفة لأخذ نسخة احتياطية من قاعدة البيانات"""
    from app.logger import logger
    from app.worker import MAX_DB_BACKUPS, ensure_disk_headroom, safe_remove_file
    try:
        db_path = settings.DATABASE_PATH
        if not os.path.exists(db_path):
            logger.error(f"❌ Backup failed: Database not found at {db_path}")
            return

        db_size_mb = os.path.getsize(db_path) / (1024.0 * 1024.0)
        ok_disk, free_mb = ensure_disk_headroom(
            min_free_mb=max(45.0, db_size_mb * 2.0 + 20.0),
            hard_min_mb=max(20.0, db_size_mb + 10.0),
        )
        if not ok_disk:
            logger.warning(f"⚠️ Backup skipped cleanly: low disk space ({free_mb:.1f}MB free)")
            return

        backup_dir = os.path.join(settings.STORAGE_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_filename)

        import sqlite3
        try:
            with sqlite3.connect(db_path, timeout=30.0) as src_conn, sqlite3.connect(backup_path) as dst_conn:
                src_conn.backup(dst_conn)
        except Exception:
            safe_remove_file(backup_path)
            raise
        logger.info(f"✅ Automated consistent SQLite backup created: {backup_filename}")
        
        # الاحتفاظ بآخر 3 نسخ احتياطية فقط لتوفير مساحة القرص السحابية
        backups = sorted([f for f in os.listdir(backup_dir) if f.endswith(".db")])
        if len(backups) > MAX_DB_BACKUPS:
            for old_backup in backups[:-MAX_DB_BACKUPS]:
                safe_remove_file(os.path.join(backup_dir, old_backup))
                logger.info(f"🗑️ Deleted old backup: {old_backup}")
                
    except Exception as e:
        logger.error(f"❌ Backup Job Error: {str(e)}")

# يتم إدارة الجدولة عبر lifespan بأمان لمنع التكرار عند تشغيل عمال متعددين

# ================================================================
# تسجيل الـ Routers
# ================================================================

app.include_router(auth.router,      prefix="/api")
app.include_router(users.router,     prefix="/api")
app.include_router(materials.router, prefix="/api")
app.include_router(purchases.router, prefix="/api")
app.include_router(layouts.router,   prefix="/api")
app.include_router(coins.router,     prefix="/api")
app.include_router(presets.router,   prefix="/api")
app.include_router(jobs.router,      prefix="/api")
app.include_router(chat.router,        prefix="/api")
app.include_router(admin_tools.router, prefix="/api")
app.include_router(stats.router,       prefix="/api")
app.include_router(internal_bridge.router, prefix="/api")
app.include_router(ecosystem_v3.router, prefix="/api")
app.include_router(ecosystem_v3.ota_router, prefix="/api")

# دمج موجه الـ SMS Webhook مباشرة على البورت 8000 لضمان النشر السحابي الموحد (Unified Cloud Single-Port)
try:
    from payment_webhook.router import router as webhook_sms_router
    app.include_router(webhook_sms_router)
    logger.info("✓ [Unified Engine] تم دمج محرك الـ SMS Webhook بنجاح داخل المنصة الأساسية على البورت 8000")
except Exception as _e:
    logger.warning(f"⚠️ تحذير: تعذر دمج موجه الويب هوك في المنصة الأساسية: {_e}")



# ================================================================
# Static Files — واجهة المستخدم
# ================================================================

app.mount("/frontend", StaticFiles(directory=os.path.join(settings.BASE_DIR, "frontend")), name="frontend")

# تخديم ملفات التخزين (الصور الشخصية، الـ PDF المختومة، إلخ)
_storage_dir = settings.STORAGE_DIR
os.makedirs(_storage_dir, exist_ok=True)
app.mount("/storage", StaticFiles(directory=_storage_dir), name="storage")

# تخديم تطبيق التيليجرام المصغر (Telegram Mini App & PWA)
_webapp_dir = os.path.join(os.path.dirname(settings.BASE_DIR), "webapp_ui")
if not os.path.exists(_webapp_dir):
    _webapp_dir = os.path.join(settings.BASE_DIR, "webapp_ui")

@app.get("/miniapp", include_in_schema=False)
def serve_miniapp_index(request: Request):
    """تخديم مباشر لصفحة الميني آب لمنع أي أخطاء توجيه أو 404"""
    idx = os.path.join(_webapp_dir, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx, media_type="text/html")
    return RedirectResponse(url="/miniapp/")

if os.path.exists(_webapp_dir):
    app.mount("/miniapp", StaticFiles(directory=_webapp_dir, html=True), name="miniapp")



# ================================================================
# Health Check
# ================================================================

@app.get("/api/health", tags=["النظام"])
def health_check():
    """التحقق من أن التطبيق شغال"""
    return {"status": "ok", "app": "WebPDF Elite"}


@app.get("/api/public/server-info", tags=["النظام"])
def public_server_info(request: Request):
    """نقطة اكتشاف تلقائية لتطبيقات الأندرويد الثلاثة (المدرسين، المرسلين، والأدمن) بدون أي إعدادات يدوية"""
    from app.config import stamp_settings
    base_origin = str(request.base_url).rstrip("/")
    return {
        "status": "ok",
        "app": "WebPDF Elite",
        "platform": "Z-PACT",
        "origin": base_origin,
        "store_url": f"{base_origin}/store",
        "sender_url": f"{base_origin}/sender",
        "miniapp_url": f"{base_origin}/miniapp/",
        "webhook_url": f"{base_origin}/webhook/sms",
        "stamp_settings": stamp_settings.get_public_stamp_settings(),
    }


@app.get("/api/public/stamp-settings", tags=["النظام"])
def public_stamp_settings():
    """تصدير إعدادات الإجباري والاختياري لبيانات الختم من الملف المركزي stamp_settings.py"""
    from app.config import stamp_settings
    return stamp_settings.get_public_stamp_settings()


@app.get("/", include_in_schema=False)
def root_redirect():
    """التوجيه للصفحة الرئيسية"""
    return RedirectResponse(url="/login")


# ================================================================
# Page Routes — مسارات الصفحات (بديل Live Server)
# ================================================================

@app.get("/login", include_in_schema=False)
@app.get("/login.html", include_in_schema=False)
def login_page():
    """صفحة تسجيل الدخول"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "login.html"))

@app.get("/store", include_in_schema=False)
@app.get("/store.html", include_in_schema=False)
def store_page():
    """صفحة المتجر"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "store.html"))

@app.get("/admin", include_in_schema=False)
@app.get("/admin.html", include_in_schema=False)
def admin_page():
    """صفحة الإدارة"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "admin.html"))

@app.get("/my-purchases", include_in_schema=False)
@app.get("/my_purchases.html", include_in_schema=False)
def my_purchases_page():
    """صفحة مشترياتي — إعادة توجيه إلى تبويب المشتريات في المتجر"""
    return RedirectResponse(url="/store?tab=purchases")

@app.get("/settings", include_in_schema=False)
@app.get("/settings.html", include_in_schema=False)
def settings_page():
    """صفحة الإعدادات والمحفظة"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "settings.html"))

@app.get("/preview", include_in_schema=False)
@app.get("/preview.html", include_in_schema=False)
def preview_page():
    """صفحة معاينة الملف"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "preview.html"))

@app.get("/creator", include_in_schema=False)
@app.get("/creator.html", include_in_schema=False)
def creator_page():
    """صفحة قصة الصانع (Abdo Saber) ثلاثية الأبعاد"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "creator.html"))


@app.get("/sender", include_in_schema=False)
@app.get("/sender.html", include_in_schema=False)
def sender_portal_page():
    """بوابة وتطبيق فريق المرسلين المعتمدين (Z-PACT Sender Team)"""
    return FileResponse(os.path.join(settings.BASE_DIR, "frontend", "pages", "sender.html"))


@app.get("/download/apk", include_in_schema=False)
@app.get("/download/apk/admin", include_in_schema=False)
@app.get("/Z-PACT-Admin-Radar.apk", include_in_schema=False)
@app.get("/Z-PACT-Enterprise.apk", include_in_schema=False)
def download_admin_android_apk():
    """تحميل نسخة الأدمن والرادار الأسطورية (Z-PACT-Admin-Radar.apk)"""
    root_workspace = os.path.dirname(settings.BASE_DIR)
    apk_candidates = [
        os.path.join(root_workspace, "Z-PACT-Admin-Radar.apk"),
        os.path.join(root_workspace, "Z-PACT-Enterprise.apk"),
        os.path.join(root_workspace, "android_app", "Z-PACT-Admin-Radar.apk"),
        os.path.join(root_workspace, "android_app", "app", "build", "outputs", "apk", "admin", "debug", "app-admin-debug.apk"),
    ]
    for apk_path in apk_candidates:
        if os.path.isfile(apk_path):
            return FileResponse(
                apk_path,
                media_type="application/vnd.android.package-archive",
                filename="Z-PACT-Admin-Radar.apk",
            )
    return Response("ملف تطبيق الأدمن غير مبني بعد — شغّل بناء_تطبيق_الاندرويد.bat أولاً.", status_code=404)


@app.get("/download/apk/teacher", include_in_schema=False)
@app.get("/Z-PACT-Teacher-Store.apk", include_in_schema=False)
def download_teacher_android_apk():
    """تحميل نسخة المدرسين الآمنة بدون أذونات SMS (Z-PACT-Teacher-Store.apk)"""
    root_workspace = os.path.dirname(settings.BASE_DIR)
    apk_candidates = [
        os.path.join(root_workspace, "Z-PACT-Teacher-Store.apk"),
        os.path.join(root_workspace, "android_app", "Z-PACT-Teacher-Store.apk"),
        os.path.join(root_workspace, "android_app", "app", "build", "outputs", "apk", "teacher", "debug", "app-teacher-debug.apk"),
    ]
    for apk_path in apk_candidates:
        if os.path.isfile(apk_path):
            return FileResponse(
                apk_path,
                media_type="application/vnd.android.package-archive",
                filename="Z-PACT-Teacher-Store.apk",
            )
    return Response("ملف تطبيق المدرسين غير مبني بعد — شغّل بناء_تطبيق_الاندرويد.bat أولاً.", status_code=404)


@app.get("/download/apk/sender", include_in_schema=False)
@app.get("/Z-PACT-Sender-Team.apk", include_in_schema=False)
def download_sender_android_apk():
    """تحميل نسخة فريق المرسلين الآمنة بدون أي أذونات (Z-PACT-Sender-Team.apk)"""
    root_workspace = os.path.dirname(settings.BASE_DIR)
    apk_candidates = [
        os.path.join(root_workspace, "Z-PACT-Sender-Team.apk"),
        os.path.join(root_workspace, "android_app", "Z-PACT-Sender-Team.apk"),
        os.path.join(root_workspace, "android_app", "app", "build", "outputs", "apk", "sender", "debug", "app-sender-debug.apk"),
    ]
    for apk_path in apk_candidates:
        if os.path.isfile(apk_path):
            return FileResponse(
                apk_path,
                media_type="application/vnd.android.package-archive",
                filename="Z-PACT-Sender-Team.apk",
            )
    return Response("ملف تطبيق المرسلين غير مبني بعد — شغّل بناء_تطبيق_الاندرويد.bat أولاً.", status_code=404)


if __name__ == "__main__":
    import uvicorn
    import multiprocessing

    # التحقق من عدد الأنوية المتاحة (لتقسيم الحمل على المعالج بأعلى كفاءة)
    cpu_cores = multiprocessing.cpu_count() or 4
    workers_env = os.getenv("SERVER_WORKERS")
    # نحدد عدد العمال المتوازن (بين 2 و 8) لاستغلال أنوية المعالج
    workers = int(workers_env) if workers_env else min(max(2, cpu_cores // 2), 8)

    reload_mode = os.getenv("SERVER_RELOAD", "false").lower() in ("true", "1", "yes")

    print("==============================================================")
    print(">> Z-PACT Ultra-Fast Production Engine Started")
    print(f">> Active Workers: {1 if reload_mode else workers} (Multi-Core Processing)")
    print(">> Optimizations: GZip Compression + In-Memory Cache + SQLite WAL 64MB")
    print(">> Server URL:    http://127.0.0.1:8000")
    print(">> Login Page:    http://127.0.0.1:8000/login")
    print(">> Store Page:    http://127.0.0.1:8000/store")
    print("==============================================================")

    if reload_mode:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
    else:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            workers=workers,
            backlog=8192,             # طابور اتصالات ضخم لمنع رفض أي طلب أثناء الضغط
            timeout_keep_alive=30,    # إعادة استخدام اتصالات HTTP لتفادي تكرار المصافحة
            limit_concurrency=100000, # قدرة استيعاب هائلة للمتصلين المتزامنين
            access_log=False,         # إيقاف كتابة لوج كل طلب لتفريغ المعالج من عنق زجاجة القرص
            log_level="info",
        )

