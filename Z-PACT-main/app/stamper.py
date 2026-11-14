# ================================================================
# app/stamper.py — محرك الختم (PDF Stamping Engine) v3.0
#
# ده قلب المشروع كله.
# بياخد الـ PDF الأصلي + بيانات المشتري + تنسيق الأختام
# ويرجع PDF مختوم، محمي بكلمة سر، فيه علامات مخفية.
#
# المحرك الجديد (v3): يستخدم PyMuPDF فقط لإضافة الأختام والعلامات
# المائية مباشرة على الـ PDF الأصلي بدون أي تحويل لصور (Vector-Preserving).
# ================================================================

import asyncio
import gc
import io
import math
import os
import random
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

import fitz  # PyMuPDF
fitz.TOOLS.mupdf_display_errors(False)

from sqlalchemy.orm import Session
from app.formatting import format_instructor_name, format_contact_phone, clean_phone_number

from app import crud
from app.database import SessionLocal
from app.models import MarkType, PurchaseStatus
from app.config import settings
from app.logger import logger

# مسار مجلد تخزين الملفات المختومة
OUTPUT_DIR = os.path.join(settings.STORAGE_DIR, "stamped")
PREVIEW_DIR = os.path.join(settings.STORAGE_DIR, "previews")
FRAMES_DIR = os.path.join(settings.STORAGE_DIR, "frames")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)
os.makedirs(FRAMES_DIR, exist_ok=True)

# وضع التشغيل المحلي الخفيف لتقليل استهلاك المعالج والذاكرة أثناء العمل المحلي
LOW_CPU_MODE = os.getenv("ZPACT_LOW_CPU_MODE", "1").strip().lower() in ("1", "true", "yes", "on")

# سيمفور لتحديد عدد العمليات المتزامنة (عملية واحدة في المرة في الوضع المحلي الخفيف)
STAMP_SEMAPHORE = asyncio.Semaphore(1 if LOW_CPU_MODE else 2)

# مسار خط Cairo الافتراضي لاستخدامه في الأختام العربية
_CAIRO_FONT_PATH = os.path.abspath(os.path.join(settings.BASE_DIR, "frontend", "assets", "Cairo-Bold.ttf"))
if not os.path.exists(_CAIRO_FONT_PATH):
    _CAIRO_FONT_PATH = None

# سجل الخطوط العربية المحلية المعتمدة (بدون تحميل زائد على الذاكرة)
_FONT_CANDIDATES: dict[str, tuple[str, list[str]]] = {
    "cairo": (
        "Cairo Bold (كايرو عريض - الافتراضي)",
        [_CAIRO_FONT_PATH or "", r"C:\Windows\Fonts\tahomabd.ttf"],
    ),
    "tahoma": (
        "Tahoma Bold (تاهوما عريض)",
        [r"C:\Windows\Fonts\tahomabd.ttf", r"C:\Windows\Fonts\tahoma.ttf", _CAIRO_FONT_PATH or ""],
    ),
    "arial": (
        "Arial Bold (أريال عريض)",
        [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf", _CAIRO_FONT_PATH or ""],
    ),
    "simplified": (
        "Simplified Arabic (عربي مبسط)",
        [r"C:\Windows\Fonts\simpbdo.ttf", r"C:\Windows\Fonts\simpo.ttf", _CAIRO_FONT_PATH or ""],
    ),
    "traditional": (
        "Traditional Arabic (خط عربي تقليدي)",
        [r"C:\Windows\Fonts\tradbdo.ttf", r"C:\Windows\Fonts\trado.ttf", _CAIRO_FONT_PATH or ""],
    ),
    "arabtype": (
        "Arabic Typesetting (خط عربي مزخرف)",
        [r"C:\Windows\Fonts\arabtype.ttf", _CAIRO_FONT_PATH or ""],
    ),
    "naskh": (
        "DecoType Naskh (خط النسخ)",
        [r"C:\Windows\Fonts\DTNASKH1.TTF", r"C:\Windows\Fonts\DTNASKH0.TTF", _CAIRO_FONT_PATH or ""],
    ),
    "times": (
        "Times New Roman Bold (رسمي)",
        [r"C:\Windows\Fonts\timesbd.ttf", r"C:\Windows\Fonts\times.ttf", _CAIRO_FONT_PATH or ""],
    ),
}

_FONT_CACHE: dict[str, tuple[fitz.Font, Optional[str]]] = {}


def _resolve_font_entry(font_family: Optional[str] = "cairo") -> tuple[fitz.Font, Optional[str]]:
    """تحميل وتخزين كائن الخط المختار مؤقتاً لدعم الخطوط العربية المتنوعة بأقل استهلاك."""
    key = (font_family or "cairo").strip().lower()
    if key not in _FONT_CANDIDATES:
        key = "cairo"
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    _, paths = _FONT_CANDIDATES[key]
    for candidate in paths:
        if candidate and os.path.exists(candidate):
            try:
                fobj = fitz.Font(fontfile=candidate)
                _FONT_CACHE[key] = (fobj, candidate)
                return fobj, candidate
            except Exception as e:
                logger.warning(f"Could not load font '{key}' from {candidate}: {e}")

    fallback = (fitz.Font("helv"), None)
    _FONT_CACHE[key] = fallback
    return fallback


def _get_font(font_family: Optional[str] = "cairo") -> fitz.Font:
    """إرجاع كائن خط مخزن مسبقاً لتسريع المعالجة وجودة الرسم."""
    return _resolve_font_entry(font_family)[0]


def _get_font_path(font_family: Optional[str] = "cairo") -> Optional[str]:
    return _resolve_font_entry(font_family)[1]


def list_available_fonts() -> list[dict]:
    """إرجاع قائمة الخطوط المتوفرة فعلياً على الجهاز لاختيارها من البوت والموبايل."""
    out = []
    for key, (label, paths) in _FONT_CANDIDATES.items():
        has_file = any(p and os.path.exists(p) for p in paths)
        if has_file:
            out.append({"key": key, "label": label})
    if not out:
        out.append({"key": "cairo", "label": "Cairo Bold (الافتراضي)"})
    return out


def get_default_stamp_elements(font_family: str = "cairo") -> list[dict]:
    """التنسيق الافتراضي المعتمد لأي مذكرة جديدة (اسم، هاتف، سلسلة، علامة مائية)."""
    return [
        {
            "type": "name",
            "page": 0,
            "x_pct": 50.0,
            "y_pct": 93.5,
            "font_size": 15.0,
            "font_family": font_family,
            "color": "#0D47A1",
            "opacity": 0.92,
            "rotation": 0.0,
            "excluded_pages": [],
            "pattern": "single",
            "z_index_mode": "foreground",
        },
        {
            "type": "phone",
            "page": 0,
            "x_pct": 50.0,
            "y_pct": 96.5,
            "font_size": 13.0,
            "font_family": font_family,
            "color": "#1E293B",
            "opacity": 0.92,
            "rotation": 0.0,
            "excluded_pages": [],
            "pattern": "single",
            "z_index_mode": "foreground",
        },
        {
            "type": "doc_name",
            "page": 0,
            "x_pct": 50.0,
            "y_pct": 4.2,
            "font_size": 14.0,
            "font_family": font_family,
            "color": "#0F172A",
            "opacity": 0.88,
            "rotation": 0.0,
            "excluded_pages": [],
            "pattern": "single",
            "z_index_mode": "foreground",
        },
        {
            "type": "watermark",
            "page": 0,
            "x_pct": 50.0,
            "y_pct": 50.0,
            "font_size": 36.0,
            "font_family": font_family,
            "color": "#64748B",
            "opacity": 0.14,
            "rotation": -45.0,
            "excluded_pages": [1],
            "pattern": "diagonal",
            "z_index_mode": "background",
        },
    ]


def process_frame_image_to_transparent_png(
    image_bytes: bytes,
    out_path: str,
    white_threshold: int = 235,
    max_dim: int = 2200,
) -> str:
    """
    معالجة صورة الإطار/البرواز لإزالة الخلفية البيضاء تلقائياً وتحويلها إلى PNG شفاف (RGBA)
    حتى لا تحجب نصوص المذكرة عند دمجها على الصفحات، مع تحجيم ذكي لتوفير المعالج والذاكرة.
    """
    from PIL import Image, ImageChops

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    w, h = img.size
    if max(w, h) > max_dim and max_dim > 0:
        scale = max_dim / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        w, h = img.size

    r, g, b, a = img.split()

    # فحص منطقة المنتصف: لو الإطار شفاف أصلاً في المنتصف نحافظ على قناة الألفا الأصلية
    cx0, cy0 = max(0, w // 4), max(0, h // 4)
    cx1, cy1 = min(w, (3 * w) // 4), min(h, (3 * h) // 4)
    center_alpha = a.crop((cx0, cy0, max(cx0 + 1, cx1), max(cy0 + 1, cy1)))
    alpha_min, _ = center_alpha.getextrema()
    if alpha_min < 180:
        img.save(out_path, format="PNG", optimize=True)
        return out_path

    # إزالة الخلفية البيضاء/الفاتحة جداً بدون حلقات بطيئة باستخدام PIL C LUT
    min_rgb = ImageChops.darker(ImageChops.darker(r, g), b)
    soft_band = max(white_threshold - 20, 180)
    lut = []
    for v in range(256):
        if v >= white_threshold:
            lut.append(0)
        elif v > soft_band:
            lut.append(int((white_threshold - v) * 255 / (white_threshold - soft_band)))
        else:
            lut.append(255)
    white_alpha = min_rgb.point(lut)
    merged_alpha = ImageChops.darker(a, white_alpha)
    clean_img = Image.merge("RGBA", (r, g, b, merged_alpha))
    clean_img.save(out_path, format="PNG", optimize=True)
    return out_path


def save_optimized_pdf(
    doc: fitz.Document,
    output_path: str,
    compress: bool = True,
    is_encrypted: bool = False,
    user_pw: Optional[str] = None,
    owner_pw: Optional[str] = None,
    fast_batch: bool = False,
) -> None:
    """
    حفظ وضغط الـ PDF بأقصى درجة كفاءة دون التأثير على جودة الخطوط المتجهية والعلامات الأمنية.
    في وضع المعالجة المحلية الخفيفة (fast_batch / LOW_CPU_MODE) يتم تقليل عمليات إعادة فحص الكائنات المرهقة للمعالج.
    """
    save_options = {}
    if compress and (fast_batch or LOW_CPU_MODE):
        save_options.update({
            "garbage": 1,
            "deflate": True,
            "deflate_images": True,
            "clean": False,
        })
    elif compress:
        save_options.update({
            "garbage": 4,
            "deflate": True,
            "deflate_images": True,
            "deflate_fonts": True,
            "clean": True,
        })
    else:
        save_options.update({
            "garbage": 1,
            "deflate": False,
        })

    if is_encrypted and user_pw:
        save_options["encryption"] = fitz.PDF_ENCRYPT_AES_256
        save_options["user_pw"] = str(user_pw)
        save_options["owner_pw"] = str(owner_pw or user_pw)

    try:
        doc.save(output_path, **save_options)
    except TypeError:
        fallback_options = {
            "garbage": 1 if (fast_batch or LOW_CPU_MODE) else (4 if compress else 0),
            "deflate": compress,
            "clean": False if (fast_batch or LOW_CPU_MODE) else True,
        }
        if is_encrypted and user_pw:
            fallback_options["encryption"] = fitz.PDF_ENCRYPT_AES_256
            fallback_options["user_pw"] = str(user_pw)
            fallback_options["owner_pw"] = str(owner_pw or user_pw)
        doc.save(output_path, **fallback_options)


def _resolve_asset_path(rel_or_abs_path: Optional[str], subfolder: str = "frames") -> Optional[str]:
    """حل مسار الصور والإطارات والشعارات عبر كل جذور التخزين المحتملة."""
    resolved = settings.resolve_storage_path(rel_or_abs_path, default_subdir=subfolder)
    return resolved if resolved and os.path.exists(resolved) else None


def _resolve_material_path(path: str) -> str:
    """إيجاد المسار الصحيح للملف حتى لو كان المسار المخزن في قاعدة البيانات مطلقاً لبيئة أخرى"""
    return settings.resolve_storage_path(path, default_subdir="originals") or path


# ================================================================
# دالة المدخل الرئيسية — بتُستدعى من الـ Background Task
# ================================================================

async def stamp_pdf(
    purchase_id:    int,
    material_path:  str,
    stamp_layout:   list[dict],
    buyer_name:     str,
    buyer_phone:    str,
    watermark_text: Optional[str] = None,
    doc_name_stamp: Optional[str] = None,
    compress:       bool = True,
    is_encrypted:   bool = False,
    material_tag:   Optional[str] = None,
    watermark_type: Optional[str] = "text",
    watermark_logo_path: Optional[str] = None,
    fast_batch:     bool = False,
    override_frame_path: Optional[str] = None,
) -> None:
    """
    الدالة الرئيسية لختم الـ PDF.
    بتُنفَّذ في الـ background بعد تسجيل الشراء مباشرة.
    """
    from app.routers.chat import manager
    db = SessionLocal()

    async with STAMP_SEMAPHORE:
        try:
            logger.info(f"🚀 [Stamper] Starting process for Purchase {purchase_id}")
            crud.update_purchase_status(db, purchase_id, PurchaseStatus.PROCESSING)

            resolved_material_path = _resolve_material_path(material_path)
            if not os.path.exists(resolved_material_path):
                raise FileNotFoundError(f"الملف الأصلي للمذكرة غير موجود في مسارات السيرفر: {material_path}")

            purchase = crud.get_purchase(db, purchase_id)
            raw_frame_ref = override_frame_path or (purchase.material.page_frame_path if (purchase and purchase.material) else None)
            page_frame_path = _resolve_asset_path(raw_frame_ref, "frames")

            buyer_phone_2 = purchase.buyer_phone_stamp_2 if purchase else None
            series_name = getattr(purchase, "series_name", None) if purchase else None
            effective_doc_name = series_name or doc_name_stamp
            effective_wm_logo = _resolve_asset_path(
                getattr(purchase, "watermark_logo_path", None) or watermark_logo_path,
                "logos",
            )
            effective_wm_type = getattr(purchase, "watermark_type", None) or watermark_type or "none"
            effective_wm_text = getattr(purchase, "watermark_text", None) or watermark_text

            # --- الخطوة 1: ختم بـ PyMuPDF (Vector-Preserving) ---
            try:
                stamped_path = await asyncio.wait_for(
                    asyncio.to_thread(
                        _stamp_native,
                        purchase_id,
                        resolved_material_path,
                        stamp_layout,
                        buyer_name,
                        buyer_phone,
                        effective_wm_text,
                        effective_doc_name,
                        material_tag,
                        page_frame_path,
                        effective_wm_type,
                        effective_wm_logo,
                        buyer_phone_2,
                        fast_batch or LOW_CPU_MODE,
                    ),
                    timeout=600.0  # 10 دقائق
                )
            except asyncio.TimeoutError:
                raise Exception("تخطى محرك الختم المهلة الزمنية (10 دقائق). الملف كبير جداً أو السيرفر مشغول.")

            logger.info(f"✓ [Stamper] Native stamping finished for {purchase_id}")

            # --- التحقق الأمني ---
            if not os.path.exists(stamped_path) or os.path.getsize(stamped_path) < 100:
                raise Exception(f"فشل توليد الملف: الملف الناتج غير صالح (size={os.path.getsize(stamped_path) if os.path.exists(stamped_path) else 0})")

            check_doc = fitz.open(stamped_path)
            check_pages = len(check_doc)
            check_doc.close()
            original_doc = fitz.open(resolved_material_path)
            original_pages = len(original_doc)
            original_doc.close()
            logger.info(f"[Stamper] Verification for {purchase_id}: stamped_pages={check_pages}, original_pages={original_pages}")

            if check_pages != original_pages:
                raise Exception(f"خرق أمني: الملف الناتج به {check_pages} صفحة بدلاً من {original_pages}.")

            # --- الخطوة 2: إضافة علامات مخفية ---
            logger.info(f"⏳ [Stamper] Adding hidden marks for {purchase_id}...")
            final_path = _add_marks_and_finalize(
                db           = db,
                input_path   = stamped_path,
                purchase_id  = purchase_id,
                buyer_name   = buyer_name,
                buyer_phone  = buyer_phone,
                compress     = compress,
                is_encrypted = is_encrypted,
            )

            if not os.path.exists(final_path) or os.path.getsize(final_path) < 100:
                raise Exception("فشل في المرحلة النهائية من معالجة الملف")

            # --- الخطوة 3: تحديث قاعدة البيانات ---
            purchase = crud.update_purchase_status(
                db          = db,
                purchase_id = purchase_id,
                status      = PurchaseStatus.COMPLETED,
                output_path = final_path,
            )
            logger.info(f"✅ [Stamper] Purchase {purchase_id} COMPLETED ({os.path.getsize(final_path)//1024}KB)")

            # --- تحديث حالة BotOrder المرتبط وإرسال حدث READY للبوت ---
            try:
                from app.models import BotOrder, OrderFulfillmentStatus
                from app.routers.internal_bridge import internal_event_bus
                bot_order = db.query(BotOrder).filter(BotOrder.purchase_id == purchase_id).first()
                if bot_order:
                    bot_order.status = OrderFulfillmentStatus.READY_FOR_DELIVERY
                    bot_order.stamped_pdf_path = final_path
                    bot_order.completed_at = datetime.utcnow()
                    db.commit()
                    internal_event_bus.fire_event({
                        "event": "ORDER_READY",
                        "order_code": bot_order.order_code,
                        "telegram_chat_id": bot_order.telegram_chat_id,
                        "telegram_user_id": bot_order.telegram_user_id,
                        "material_id": bot_order.material_id,
                        "material_title": purchase.material.title if purchase and purchase.material else "المذكرة المطلوبة",
                        "stamped_pdf_path": final_path,
                        "buyer_name": bot_order.buyer_name_stamp,
                    })
                    logger.info(f"🚀 [Stamper Hook] تم تحديث BotOrder {bot_order.order_code} إلى READY_FOR_DELIVERY وبث الحدث بنجاح")
            except Exception as bot_err:
                logger.warning(f"⚠️ [Stamper Hook Warning] فشل تحديث BotOrder: {bot_err}")

            if purchase and purchase.buyer_id:
                await manager.send_personal_message(purchase.buyer_id, {
                    "action": "job_update",
                    "purchase_id": purchase_id,
                    "status": "completed",
                    "title": purchase.material.title if purchase.material else "ملف جاهز"
                })

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"❌ [Stamper Error] Purchase {purchase_id}: {e}\n--- Traceback ---\n{tb}")
            try:
                purchase = crud.update_purchase_status(db, purchase_id, PurchaseStatus.FAILED)
                try:
                    from app.models import BotOrder, OrderFulfillmentStatus
                    bot_order = db.query(BotOrder).filter(BotOrder.purchase_id == purchase_id).first()
                    if bot_order:
                        bot_order.status = OrderFulfillmentStatus.FAILED
                        bot_order.error_message = str(e)
                        db.commit()
                except Exception:
                    pass

                if purchase and purchase.buyer_id:
                    await manager.send_personal_message(purchase.buyer_id, {
                        "action": "job_update",
                        "purchase_id": purchase_id,
                        "status": "failed",
                        "error": str(e)
                    })
            except:
                pass

        finally:
            db.close()
            try:
                fitz.TOOLS.store_shrink(100)
            except Exception:
                pass
            gc.collect()


async def run_test_stamp(
    material_path: str,
    stamp_layout:  list[dict],
    material_tag:  Optional[str] = None,
    page_frame_path: Optional[str] = None,
) -> str:
    """
    ختم تجريبي سريع للمعاينة.
    بدون تشفير وبدون علامات مخفية وبدون تسجيل في الداتا بيز.
    """
    import shutil

    def _do_test():
        tmp_dir = PREVIEW_DIR
        os.makedirs(tmp_dir, exist_ok=True)
        fake_id = "test_" + uuid.uuid4().hex[:8]

        has_logo_elements = any(el.get("type") == "logo_watermark" for el in stamp_layout)
        watermark_type = "logo" if has_logo_elements else "text"

        raw_pdf = _stamp_native(
            fake_id,
            _resolve_material_path(material_path),
            stamp_layout,
            "تجربة مستخدم",
            "01000000000",
            "نسخة معاينة",
            "اسم المذكرة التجريبي",
            material_tag=material_tag,
            page_frame_path=_resolve_asset_path(page_frame_path, "frames"),
            watermark_type=watermark_type,
            watermark_logo_path=None,
            fast_batch=True,
        )
        final_dest = f"{tmp_dir}/test_{fake_id}.pdf"
        shutil.copy(raw_pdf, final_dest)
        return final_dest

    return await asyncio.to_thread(_do_test)


def render_stamped_page_preview_png_sync(
    material_path: str,
    stamp_layout: list[dict],
    page_num: int = 1,
    buyer_name: str = "أحمد محمد علي",
    buyer_phone: str = "01012345678",
    buyer_phone_2: Optional[str] = None,
    doc_name_stamp: str = "سلسلة التفوق",
    watermark_text: str = "نسخة خاصة",
    watermark_type: str = "text",
    watermark_logo_path: Optional[str] = None,
    material_tag: Optional[str] = None,
    page_frame_path: Optional[str] = None,
    zoom: float = 1.35,
    apply_stamps: bool = True,
) -> bytes:
    """
    تصيير صورة PNG خفيفة لصفحة واحدة فقط (بدون لمس بقية الصفحات)،
    تُستخدم للمعاينة الفورية في البوت والموبايل عند تغيير الخط أو الحجم أو الإحداثيات بأقل استهلاك للمعالج.
    """
    resolved_pdf = _resolve_material_path(material_path)
    src = fitz.open(resolved_pdf)
    try:
        total_pages = max(1, len(src))
        target_idx = max(0, min(total_pages - 1, int(page_num) - 1))

        # استخراج صفحة واحدة فقط في مستند مؤقت في الذاكرة لتطبيق الأختام عليها بدقة 100%
        single_doc = fitz.open()
        single_doc.insert_pdf(src, from_page=target_idx, to_page=target_idx)
        page = single_doc[0]

        if apply_stamps:
            resolved_frame = _ensure_transparent_frame_cached(_resolve_asset_path(page_frame_path, "frames"))
            resolved_logo = _resolve_asset_path(watermark_logo_path, "logos") or os.path.abspath(
                os.path.join(settings.BASE_DIR, "frontend", "assets", "logo.png")
            )
            effective_layout = stamp_layout if stamp_layout else get_default_stamp_elements()
            _apply_stamps_to_page(
                page=page,
                page_num=target_idx + 1,
                total_pages=total_pages,
                stamp_layout=effective_layout,
                buyer_name=buyer_name,
                buyer_phone=buyer_phone,
                buyer_phone_2=buyer_phone_2,
                doc_name_stamp=doc_name_stamp,
                watermark_text=watermark_text,
                watermark_type=watermark_type,
                logo_path=resolved_logo,
                material_tag=material_tag,
                page_frame_path=resolved_frame,
            )

        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png_bytes = pix.tobytes("png")
        single_doc.close()
        return png_bytes
    finally:
        src.close()
        try:
            fitz.TOOLS.store_shrink(100)
        except Exception:
            pass


async def render_stamped_page_preview_png(**kwargs) -> bytes:
    """النسخة غير المتزامنة لتوليد معاينة PNG لصفحة واحدة."""
    return await asyncio.to_thread(render_stamped_page_preview_png_sync, **kwargs)


def _ensure_transparent_frame_cached(frame_path: Optional[str]) -> Optional[str]:
    """التأكد من أن ملف الإطار شفاف في منطقة المحتوى مع حفظ نسخة معالجة لتجنب تكرار الحساب."""
    if not frame_path or not os.path.exists(frame_path):
        return None
    stem, ext = os.path.splitext(frame_path)
    if stem.endswith("_alpha") and ext.lower() == ".png":
        return frame_path
    cached_alpha = f"{stem}_alpha.png"
    if os.path.exists(cached_alpha):
        return cached_alpha
    try:
        with open(frame_path, "rb") as f:
            raw = f.read()
        process_frame_image_to_transparent_png(raw, cached_alpha)
        return cached_alpha
    except Exception as e:
        logger.warning(f"⚠️ [Frame Cache] تعذر معالجة الإطار {frame_path}: {e}")
        return frame_path


def _apply_stamps_to_page(
    page,
    page_num: int,
    total_pages: int,
    stamp_layout: list[dict],
    buyer_name: str,
    buyer_phone: str,
    buyer_phone_2: Optional[str] = None,
    doc_name_stamp: Optional[str] = None,
    watermark_text: Optional[str] = None,
    watermark_type: Optional[str] = "text",
    logo_path: Optional[str] = None,
    material_tag: Optional[str] = None,
    page_frame_path: Optional[str] = None,
) -> None:
    """تطبيق الإطار واللوجو والأختام النصية على صفحة واحدة (مشتركة بين الختم الكامل والمعاينة)."""
    # عزل حالة الرسم الأصلية للصفحة (ExtGState/CTM/Clip) قبل إضافة الأختام الفوقية حتى لا تتأثر شفافيتها أو لونها
    if not getattr(page, "is_wrapped", False):
        try:
            page.wrap_contents()
        except Exception:
            pass

    pw = page.rect.width
    ph = page.rect.height

    formatted_name  = format_instructor_name(buyer_name)
    formatted_phone = format_contact_phone(buyer_phone)
    smart_doc_name  = doc_name_stamp.strip() if doc_name_stamp else ""
    smart_wm_text   = watermark_text.strip() if watermark_text else ""
    clean_wm_type   = (watermark_type or "none").strip().lower()

    if clean_wm_type in ("none", "off", "no_watermark", "false", "logo"):
        smart_wm_text = ""

    # === Layer 0: إطار الصفحة (شفاف الوسط) ===
    if page_frame_path and os.path.exists(page_frame_path):
        try:
            page.insert_image(page.rect, filename=page_frame_path, keep_proportion=False, overlay=True)
        except Exception as e:
            logger.error(f"❌ [Frame Error] Page {page_num}: {e}")

    # === Layer 1: اللوجو المائي ===
    if clean_wm_type == "logo" and logo_path and os.path.exists(logo_path):
        logo_elements = [el for el in stamp_layout if el.get("type") == "logo_watermark"]
        if not logo_elements:
            logo_elements = [{"type": "logo_watermark", "x_pct": 50.0, "y_pct": 50.0, "scale": 220, "opacity": 0.14, "page": 0}]
        for el in logo_elements:
            _draw_logo_on_page(page, el, page_num, logo_path)

    # === Layer 2: الأختام النصية والعلامة المائية ===
    page_stamps = _get_stamps_for_page(stamp_layout, page_num, total_pages)
    font_name = "cairo" if _CAIRO_FONT_PATH else "helv"

    for stamp in page_stamps:
        stamp_type = stamp.get("type", "")
        is_single_page = int(stamp.get("page", 0) or 0) > 0
        is_literal = (total_pages == 1) or is_single_page
        render_stamp = dict(stamp)

        if stamp_type == "name":
            display_text = formatted_name
        elif stamp_type == "phone":
            if page_num == 1:
                display_text = clean_phone_number(buyer_phone or buyer_phone_2 or "")
            elif buyer_phone_2 and buyer_phone:
                display_text = format_contact_phone(buyer_phone) if (page_num % 2 == 1) else format_contact_phone(buyer_phone_2)
            elif buyer_phone_2:
                display_text = format_contact_phone(buyer_phone_2)
            else:
                display_text = formatted_phone
        elif stamp_type == "doc_name":
            if is_single_page:
                # الختم المخصص لصفحة معينة (بدون جميع الصفحات) يُعامل كغلاف: اسم مستقل بدون "سلسلة" وبخط عريض (Bold) مع الحفاظ على حجم الديفولت
                display_text = _extract_cover_name(smart_doc_name, material_tag)
                render_stamp["is_bold"] = True
                if not render_stamp.get("font_size"):
                    render_stamp["font_size"] = 44.0
            elif is_literal:
                display_text = smart_doc_name
            else:
                display_text = _get_smart_text(smart_doc_name, "سلسلة", material_tag)
        elif stamp_type == "watermark":
            if clean_wm_type in ("none", "logo", "off", "no_watermark", "false", ""):
                display_text = ""
            elif is_literal:
                display_text = smart_wm_text
            else:
                display_text = _get_smart_text(smart_wm_text, "", material_tag)
        else:
            display_text = stamp.get("custom_text", "")

        if not display_text:
            continue

        _draw_text_stamp_on_page(page, render_stamp, display_text, pw, ph, font_name)


# ================================================================
# المحرك الأساسي: ختم مباشر بـ PyMuPDF (Vector-Preserving)
# ================================================================

def _stamp_native(
    purchase_id,
    material_path:  str,
    stamp_layout:   list[dict],
    buyer_name:     str,
    buyer_phone:    str,
    watermark_text: Optional[str] = None,
    doc_name_stamp: Optional[str] = None,
    material_tag:   Optional[str] = None,
    page_frame_path: Optional[str] = None,
    watermark_type: Optional[str] = "text",
    watermark_logo_path: Optional[str] = None,
    buyer_phone_2:  Optional[str] = None,
    fast_batch:     bool = False,
) -> str:
    """
    يفتح الـ PDF الأصلي ويضيف الأختام والعلامات المائية مباشرة
    على الطبقة الفوقية (overlay) باستخدام PyMuPDF فقط.
    بدون أي تحويل لصور — يحافظ على الجودة المتجهة 100%.
    """
    doc = fitz.open(material_path)
    total_pages = len(doc)

    clean_wm_type = (watermark_type or "none").strip().lower()
    resolved_frame = _ensure_transparent_frame_cached(_resolve_asset_path(page_frame_path, "frames"))

    # --- تجهيز اللوجو ---
    logo_path = None
    if clean_wm_type == "logo":
        logo_path = _resolve_asset_path(watermark_logo_path, "logos")
        if not logo_path:
            logo_path = os.path.abspath(os.path.join(settings.BASE_DIR, "frontend", "assets", "logo.png"))

    effective_layout = stamp_layout if stamp_layout else get_default_stamp_elements()

    # --- معالجة كل صفحة ---
    for i in range(total_pages):
        _apply_stamps_to_page(
            page=doc[i],
            page_num=i + 1,
            total_pages=total_pages,
            stamp_layout=effective_layout,
            buyer_name=buyer_name,
            buyer_phone=buyer_phone,
            buyer_phone_2=buyer_phone_2,
            doc_name_stamp=doc_name_stamp,
            watermark_text=watermark_text,
            watermark_type=watermark_type,
            logo_path=logo_path,
            material_tag=material_tag,
            page_frame_path=resolved_frame,
        )

    # --- حفظ الملف ---
    output_path = f"{OUTPUT_DIR}/{purchase_id}_stamped.pdf"
    save_optimized_pdf(doc, output_path, compress=True, fast_batch=fast_batch)
    doc.close()
    logger.info(f"[Stamper] Saved stamped PDF: {os.path.getsize(output_path)//1024}KB, pages={total_pages}")
    return output_path


# ================================================================
# دوال مساعدة للرسم المباشر على الـ PDF
# ================================================================

def _get_stamps_for_page(stamp_layout: list[dict], page_num: int, total_pages: int) -> list[dict]:
    """تجميع الأختام المخصصة لصفحة معينة."""
    result = []
    for el in stamp_layout:
        if el.get("type") == "logo_watermark":
            continue  # اللوجو يُعالج بشكل منفصل

        try:
            target_page = int(el.get("page", 0))
        except (TypeError, ValueError):
            target_page = 0
        try:
            exclusions = [int(x) for x in el.get("excluded_pages", [])]
        except (TypeError, ValueError):
            exclusions = []

        if target_page == 0:
            if page_num not in exclusions:
                result.append(el)
        elif target_page == page_num:
            result.append(el)
    return result


def _draw_logo_on_page(page, el: dict, page_num: int, logo_path: str):
    """رسم اللوجو المائي على صفحة PyMuPDF."""
    try:
        target_page = int(el.get("page", 0))
    except (TypeError, ValueError):
        target_page = 0
    try:
        exclusions = [int(x) for x in el.get("excluded_pages", [])]
    except (TypeError, ValueError):
        exclusions = []

    if target_page == 0 and page_num in exclusions:
        return
    if target_page > 0 and target_page != page_num:
        return

    if not logo_path or not os.path.exists(logo_path):
        return

    scale = el.get("font_size", 10.0) * 10.0
    if scale <= 0:
        scale = 150.0

    w = page.rect.width
    h = page.rect.height
    cx = (el.get("x_pct", 50.0) / 100.0) * w
    cy = (el.get("y_pct", 50.0) / 100.0) * h
    rect = fitz.Rect(cx - scale/2, cy - scale/2, cx + scale/2, cy + scale/2)

    op = float(el.get("opacity", 0.15))
    # يتم رسم شعار العلامة المائية فوق المحتوى (under_content = False) لضمان ظهوره
    under_content = False

    try:
        from PIL import Image
        import io

        pil_img = Image.open(logo_path).convert("RGBA")
        r, g, b, a = pil_img.split()
        a = a.point(lambda x: int(x * op))
        pil_img = Image.merge("RGBA", (r, g, b, a))

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        page.insert_image(rect, stream=buf.read(), keep_proportion=True, overlay=not under_content)
    except Exception:
        page.insert_image(rect, filename=logo_path, keep_proportion=True, overlay=not under_content)


# خريطة الحروف العربية المعزولة (Presentation Forms-B) إلى حروفها الأساسية للخطوط التي تفتقدها مثل Cairo
_ARABIC_ISOLATED_TO_BASE: dict[int, int] = {
    0xFE80: 0x0621, 0xFE81: 0x0622, 0xFE83: 0x0623, 0xFE85: 0x0624,
    0xFE87: 0x0625, 0xFE89: 0x0626, 0xFE8D: 0x0627, 0xFE8F: 0x0628,
    0xFE93: 0x0629, 0xFE95: 0x062A, 0xFE99: 0x062B, 0xFE9D: 0x062C,
    0xFEA1: 0x062D, 0xFEA5: 0x062E, 0xFEA9: 0x062F, 0xFEAB: 0x0630,
    0xFEAD: 0x0631, 0xFEAF: 0x0632, 0xFEB1: 0x0633, 0xFEB5: 0x0634,
    0xFEB9: 0x0635, 0xFEBD: 0x0636, 0xFEC1: 0x0637, 0xFEC5: 0x0638,
    0xFEC9: 0x0639, 0xFECD: 0x063A, 0xFED1: 0x0641, 0xFED5: 0x0642,
    0xFED9: 0x0643, 0xFEDD: 0x0644, 0xFEE1: 0x0645, 0xFEE5: 0x0646,
    0xFEE9: 0x0647, 0xFEED: 0x0648, 0xFEEF: 0x0649, 0xFEF1: 0x064A,
}


def _normalize_font_glyphs(text: str, font: fitz.Font) -> str:
    """استبدال الحروف المعزولة غير الموجودة في جدول الخط بحرفها الأساسي لمنع تبديل الخط."""
    if not text:
        return text
    chars = []
    for ch in text:
        code = ord(ch)
        if code in _ARABIC_ISOLATED_TO_BASE and font.has_glyph(code) == 0:
            base_code = _ARABIC_ISOLATED_TO_BASE[code]
            if font.has_glyph(base_code) > 0:
                chars.append(chr(base_code))
                continue
        chars.append(ch)
    return "".join(chars)


def _draw_text_stamp_on_page(page, stamp: dict, text: str, pw: float, ph: float, font_name: str):
    """رسم ختم نصي على صفحة PyMuPDF بدقة عالية."""
    # تشكيل وإعادة توجيه النصوص العربية لتعرض بشكل صحيح (متصلة ومن اليمين إلى اليسار مع دعم الروابط الحرفية)
    if text:
        has_arabic = any('\u0600' <= char <= '\u06FF' or '\u0750' <= char <= '\u077F' or '\u08A0' <= char <= '\u08FF' or '\uFB50' <= char <= '\uFDFF' or '\uFE70' <= char <= '\uFEFF' for char in text)
        if has_arabic:
            try:
                import arabic_reshaper
                from bidi.algorithm import get_display
                reshaper = arabic_reshaper.ArabicReshaper(
                    configuration={
                        'delete_harakat': False,
                        'support_ligatures': True,
                        'support_transparent_glyphs': True
                    }
                )
                reshaped = reshaper.reshape(text)
                text = get_display(reshaped)
            except Exception as e:
                logger.error(f"Error reshaping Arabic text: {e}")

    x_pct   = float(stamp.get("x_pct", 50))
    y_pct   = float(stamp.get("y_pct", 50))
    fsize   = float(stamp.get("font_size", 14))
    rotation = float(stamp.get("rotation", 0))
    opacity  = float(stamp.get("opacity", 1.0))
    color_hex = stamp.get("color", "#000000")
    pattern   = stamp.get("pattern", "single")
    stamp_type = stamp.get("type", "")
    font_family = stamp.get("font_family", "cairo") or "cairo"
    wants_bold  = bool(stamp.get("is_bold"))

    # تحويل اللون من hex إلى RGB tuple (0-1)
    color = _hex_to_rgb(color_hex)

    # تحديد Z-index: نبقي الطبقة فوقية مع الشفافية حتى لا تختفي العلامة خلف خلفيات الصفحات البيضاء
    overlay_val = True

    # --- نمط Tiled (علامة مائية مكررة) ---
    if pattern == "tiled" and stamp_type == "watermark":
        _draw_tiled_watermark(page, text, fsize, color, opacity, overlay_val, font_family=font_family)
        return

    # --- نمط Diagonal (علامة مائية قطرية) ---
    if pattern == "diagonal" and stamp_type == "watermark":
        fsize = min(fsize, 100)
        _draw_diagonal_watermark(page, text, fsize, color, opacity, overlay_val, font_family=font_family)
        return

    # --- نمط Standard (ختم عادي) ---
    if stamp_type == "watermark":
        fsize = min(fsize, 120)

    cx = (x_pct / 100.0) * pw
    cy = (y_pct / 100.0) * ph

    # إنشاء TextWriter لدعم النص العربي بالخط المختار
    tw = fitz.TextWriter(page.rect)
    font = _get_font(font_family)
    fallback_fontfile = _get_font_path(font_family) or _CAIRO_FONT_PATH
    text = _normalize_font_glyphs(text, font)
    # تطبيق التثخين المتجهي (render_mode=2 Fill+Stroke) إذا طُلب بولد والخط المحمّل غير بولد أصلاً (مثل Cairo Variable Regular)
    render_mode = 2 if (wants_bold and not font.flags.get("bold")) else 0

    # حساب عرض النص لتوسيطه
    text_width = font.text_length(text, fontsize=fsize)
    text_height = fsize

    # نقطة البداية (مع التوسيط)
    start_x = cx - text_width / 2
    start_y = cy + text_height / 3  # تعديل عمودي لتوسيط بصري

    try:
        tw.append((start_x, start_y), text, font=font, fontsize=fsize)
    except Exception as e:
        logger.warning(f"tw.append error: {e}. Falling back to insert_text with font.")
        try:
            page.insert_text(
                point    = fitz.Point(start_x, start_y),
                text     = text,
                fontfile = fallback_fontfile,
                fontsize = fsize,
                color    = color,
                overlay  = overlay_val,
                render_mode = render_mode,
            )
        except Exception:
            page.insert_text(
                point    = fitz.Point(start_x, start_y),
                text     = text,
                fontsize = fsize,
                color    = color,
                overlay  = overlay_val,
                render_mode = render_mode,
            )
        return

    # تطبيق الدوران إن وجد
    write_kwargs = {"color": color, "opacity": opacity, "overlay": overlay_val}
    if render_mode:
        write_kwargs["render_mode"] = render_mode
    if abs(rotation) > 0.5:
        write_kwargs["morph"] = (fitz.Point(cx, cy), fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(rotation))
    tw.write_text(page, **write_kwargs)


def _draw_tiled_watermark(page, text: str, fsize: float, color: tuple, opacity: float, overlay: bool, font_family: str = "cairo"):
    """رسم علامة مائية مكررة (tiled) على الصفحة."""
    pw = page.rect.width
    ph = page.rect.height

    font = _get_font(font_family)

    tile_fsize = min(fsize, 60)
    text_w = font.text_length(text, fontsize=tile_fsize)
    gap_x = text_w + 80
    gap_y = tile_fsize * 3

    angle = -30  # زاوية الدوران

    tw = fitz.TextWriter(page.rect)
    y = -ph * 0.3
    while y < ph * 1.3:
        x = -pw * 0.3
        while x < pw * 1.3:
            try:
                tw.append((x, y), text, font=font, fontsize=tile_fsize)
            except Exception:
                pass
            x += gap_x
        y += gap_y

    morph = (fitz.Point(pw/2, ph/2), fitz.Matrix(1,0,0,1,0,0).prerotate(angle))
    tw.write_text(page, color=color, opacity=opacity, overlay=overlay, morph=morph)


def _draw_diagonal_watermark(page, text: str, fsize: float, color: tuple, opacity: float, overlay: bool, font_family: str = "cairo"):
    """رسم علامة مائية قطرية واحدة في منتصف الصفحة."""
    pw = page.rect.width
    ph = page.rect.height

    font = _get_font(font_family)
    fallback_fontfile = _get_font_path(font_family) or _CAIRO_FONT_PATH

    text_w = font.text_length(text, fontsize=fsize)
    cx = pw / 2
    cy = ph / 2
    start_x = cx - text_w / 2
    start_y = cy + fsize / 3

    tw = fitz.TextWriter(page.rect)
    try:
        tw.append((start_x, start_y), text, font=font, fontsize=fsize)
    except Exception:
        page.insert_text(fitz.Point(start_x, start_y), text, fontfile=fallback_fontfile, fontsize=fsize, color=color, overlay=overlay)
        return

    morph = (fitz.Point(cx, cy), fitz.Matrix(1,0,0,1,0,0).prerotate(-45))
    tw.write_text(page, color=color, opacity=opacity, overlay=overlay, morph=morph)


def _hex_to_rgb(hex_color: str) -> tuple:
    """تحويل لون hex إلى RGB tuple (0.0 - 1.0)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c*2 for c in hex_color)
    try:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return (r, g, b)
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)


# ================================================================
# استخراج اسم الغلاف الذكي
# ================================================================

import re as _re

def _extract_cover_name(doc_name: str, material_tag: Optional[str] = None) -> str:
    """استخراج الاسم المستقل لختم صفحة الغلاف بدون كلمة 'سلسلة' أو عبارة 'في المادة'."""
    if not doc_name:
        return ""
    name = " ".join(doc_name.strip().split())
    stripped = _re.sub(r'^(?:ال)?سلسل(?:ة|ه)\s+', '', name, flags=_re.UNICODE).strip()
    had_series_prefix = (stripped != name)

    if had_series_prefix:
        stripped = _re.sub(r'\s+في\s+.*$', '', stripped, flags=_re.UNICODE).strip()
    elif material_tag and material_tag.strip():
        tag_clean = material_tag.strip()
        tag_core = tag_clean[2:] if tag_clean.startswith("ال") else tag_clean
        stripped = _re.sub(
            rf'\s+في\s+(?:مادة\s+)?(?:ال)?{_re.escape(tag_core)}.*$',
            '',
            stripped,
            flags=_re.UNICODE,
        ).strip()
    else:
        stripped = _re.sub(
            r'\s+في\s+(?:مادة\s+)?(?:ال)?(?:رياضيات|عربي|لغة\s+عربية|انجليزي|إنجليزي|انكليزي|علوم|فيزياء|كيمياء|أحياء|احياء|تاريخ|جغرافيا|دراسات|فلسفة|منطق|إحصاء|احصاء|هندسة|جبر|حساب|فرنساوي|فرنسي|ألماني|الماني|إيطالي|ايطالي|تربية\s+دينية|دين|حاسب|برمجة)\b.*$',
            '',
            stripped,
            flags=_re.UNICODE,
        ).strip()

    return stripped or name

def _get_smart_text(original_text: str, subject_prefix: str = "سلسلة", subject_tag: str = "") -> str:
    """
    تحويل ذكي للنص:
    - لو النص أكثر من كلمة واحدة (مثل: 'لا للنشر' أو عبارات مخصصة):
      يُعرض النص كما كتبه المستخدم تماماً بدون إضافة 'سلسلة' أو 'في {subject_tag}'.
    - لو النص كلمة واحدة فقط (مثل: 'تفوق' أو 'التفوق'):
      يتم تحويله تلقائياً لـ: [سلسلة] [الكلمة] في [المادة].
    """
    if not original_text:
        return ""
    text = original_text.strip()
    words = text.split()

    # إذا كان النص يتكون من أكثر من كلمة واحدة: يُترك النص تماماً كما هو
    if len(words) > 1:
        return text

    # إذا كان النص كلمة واحدة فقط:
    clean_text = _re.sub(r'^(سلسل(?:ة|ه))\s+', '', text, flags=_re.UNICODE).strip()
    if subject_tag:
        suffix_pat = _re.compile(rf'\s+في\s+{_re.escape(subject_tag)}$', _re.UNICODE)
        clean_text = suffix_pat.sub('', clean_text).strip()

    prefix = f"{subject_prefix} " if subject_prefix else ""
    suffix = f" في {subject_tag}" if subject_tag else ""
    return f"{prefix}{clean_text}{suffix}".strip()


# ================================================================
# تنسيق بيانات المدرس
# ================================================================

def _format_teacher_name(name: str) -> str:
    return format_instructor_name(name)

def _format_phone_number(phone: str) -> str:
    return format_contact_phone(phone)


# ================================================================
# الخطوة النهائية: إضافة علامات مخفية (Steganography)
# بدون أي rasterization — يعمل مباشرة على الـ PDF المتجهي
# ================================================================

def _add_marks_and_finalize(
    db:          Session,
    input_path:  str,
    purchase_id: int,
    buyer_name:  str,
    buyer_phone: str,
    compress:    bool = True,
    is_encrypted: bool = False,
) -> str:
    """
    إضافة العلامات المخفية للتتبع مباشرة على الـ PDF المتجهي.
    بدون rasterization — يحافظ على الجودة الأصلية 100%.
    """
    output_path = f"{OUTPUT_DIR}/{purchase_id}_final.pdf"
    doc = fitz.open(input_path)

    from app import crud
    purchase = crud.get_purchase(db, purchase_id)

    for page_num in range(len(doc)):
        page = doc[page_num]
        random_suffix = uuid.uuid4().hex[:6].upper()
        short_mark = f"E-{purchase_id}-{random_suffix}"

        width  = page.rect.width
        height = page.rect.height

        # --- Steganography: نص مخفي في الزوايا ---
        corners = [
            (width * random.uniform(0.01, 0.15), height * random.uniform(0.01, 0.15)),
            (width * random.uniform(0.85, 0.99), height * random.uniform(0.01, 0.15)),
            (width * random.uniform(0.01, 0.15), height * random.uniform(0.85, 0.99)),
            (width * random.uniform(0.85, 0.99), height * random.uniform(0.85, 0.99)),
        ]

        for (x, y) in corners:
            page.insert_text(
                point    = fitz.Point(x, y),
                text     = short_mark,
                fontsize = 1,
                color    = (1, 1, 1),  # أبيض تماماً
            )
            crud.record_hidden_mark(
                db          = db,
                purchase_id = purchase_id,
                page_number = page_num + 1,
                x_pct       = (x / width) * 100,
                y_pct       = (y / height) * 100,
                mark_code   = short_mark,
                mark_type   = MarkType.INVISIBLE_TEXT,
            )

        # --- Micro-dots ---
        dot_rng = random.Random(purchase_id * 1000 + (page_num + 1))
        for _ in range(dot_rng.randint(4, 5)):
            x_dot = (dot_rng.uniform(5.0, 95.0) / 100.0) * width
            y_dot = (dot_rng.uniform(5.0, 95.0) / 100.0) * height
            page.draw_circle(
                center = fitz.Point(x_dot, y_dot),
                radius = 1,
                color  = (0.1, 0.1, 0.1),
                fill   = (0.1, 0.1, 0.1),
                stroke_opacity = 0.05,
                fill_opacity = 0.05,
            )

    # --- إعداد خيارات الحفظ والضغط المحسن ---
    save_optimized_pdf(
        doc=doc,
        output_path=output_path,
        compress=compress,
        is_encrypted=is_encrypted,
        user_pw=buyer_phone if is_encrypted else None,
        owner_pw=buyer_phone if is_encrypted else None,
        fast_batch=LOW_CPU_MODE,
    )
    doc.close()
    logger.info(f"[Stamper] Final PDF saved: {os.path.getsize(output_path)//1024}KB")
    return output_path
