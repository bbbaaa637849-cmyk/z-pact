# ================================================================
# app/recovery.py — الاستشفاء التلقائي للمهام المنقطعة عند إعادة التشغيل
#
# إذا انقطع السيرفر أو أُعيد تشغيله على Railway أثناء ختم PDF:
# يكتشف هذا المحرك الطلبات والمشتريات العالقة في حالة PROCESSING / PROCESSING_STAMP
# ويعيد إكمالها وتسليمها للعملاء تلقائياً دون أي تدخل أو إعادة دفع.
# ================================================================

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import database as app_db
from app.config import settings
from app.logger import logger
from app.models import (
    BotOrder,
    LayoutType,
    Material,
    OrderFulfillmentStatus,
    Purchase,
    PurchaseStatus,
    StampLayout,
)
from app.stamper import get_default_stamp_elements, stamp_pdf


def _resolve_layout_elements(db, material: Material, layout_id: Optional[int]) -> List[Dict[str, Any]]:
    layout = None
    if layout_id:
        layout = db.query(StampLayout).filter(StampLayout.id == layout_id).first()
    if not layout:
        layout = (
            db.query(StampLayout)
            .filter(
                StampLayout.material_id == material.id,
                StampLayout.layout_type == LayoutType.DEFAULT_TEXT,
            )
            .order_by(StampLayout.id.desc())
            .first()
        )
    if not layout:
        layout = (
            db.query(StampLayout)
            .filter(StampLayout.material_id == material.id)
            .order_by(StampLayout.id.desc())
            .first()
        )

    raw_elements = layout.get_elements() if layout else get_default_stamp_elements()
    processed: List[Dict[str, Any]] = []
    for el in raw_elements:
        el_copy = dict(el)
        if material.tag and el_copy.get("custom_text"):
            el_copy["custom_text"] = (
                el_copy["custom_text"]
                .replace("{tag}", material.tag)
                .replace("[tag]", material.tag)
            )
        processed.append(el_copy)
    return processed


def _valid_output_file(raw_path: Optional[str]) -> Optional[str]:
    if not raw_path:
        return None
    resolved = settings.resolve_storage_path(raw_path, default_subdir="stamped")
    if resolved and os.path.isfile(resolved) and os.path.getsize(resolved) > 100:
        return resolved
    return None


def _build_stamp_kwargs(
    db,
    purchase: Purchase,
    order: Optional[BotOrder] = None,
) -> Optional[Dict[str, Any]]:
    material = db.query(Material).filter(Material.id == purchase.material_id).first()
    if not material:
        return None
    resolved_orig = settings.resolve_storage_path(material.original_pdf_path, "originals")
    if not resolved_orig or not os.path.isfile(resolved_orig):
        return None

    elements = _resolve_layout_elements(db, material, purchase.stamp_layout_id)
    buyer_logo = getattr(purchase, "watermark_logo_path", None)
    if not buyer_logo and order:
        buyer_logo = order.watermark_logo_path
        if not buyer_logo and order.teacher_profile:
            buyer_logo = order.teacher_profile.logo_image_path

    wm_type = purchase.watermark_type or (order.watermark_type if order else None) or "none"
    effective_series = (
        getattr(purchase, "series_name", None)
        or (order.series_name if order else None)
        or (order.doc_name_stamp if order else None)
        or purchase.doc_name_stamp
    )
    wm_text = purchase.watermark_text or (order.watermark_text if order else None)

    return {
        "purchase_id": purchase.id,
        "material_path": resolved_orig,
        "stamp_layout": elements,
        "buyer_name": purchase.buyer_name_stamp or (order.buyer_name_stamp if order else "") or "",
        "buyer_phone": purchase.buyer_phone_stamp or (order.buyer_phone_stamp if order else "") or "",
        "watermark_text": wm_text,
        "doc_name_stamp": effective_series,
        "compress": bool(material.compression_enabled),
        "material_tag": material.tag,
        "watermark_type": wm_type,
        "watermark_logo_path": buyer_logo,
    }


def collect_and_heal_interrupted_jobs_sync() -> List[Dict[str, Any]]:
    """
    فحص قاعدة البيانات وإصلاح أو جمع المهام التي انقطعت أثناء الختم.
    ترجع قائمة المهام المطلوب إعادة تشغيلها عبر stamp_pdf.
    """
    db = app_db.SessionLocal()
    jobs_to_resume: List[Dict[str, Any]] = []
    handled_purchase_ids = set()

    try:
        from app.routers.internal_bridge import internal_event_bus

        # 1. فحص طلبات البوت/السلة العالقة في PROCESSING_STAMP
        stuck_orders = (
            db.query(BotOrder)
            .filter(BotOrder.status == OrderFulfillmentStatus.PROCESSING_STAMP)
            .order_by(BotOrder.id.asc())
            .all()
        )
        for order in stuck_orders:
            purchase = None
            if order.purchase_id:
                purchase = db.query(Purchase).filter(Purchase.id == order.purchase_id).first()
            if not purchase and order.order_code:
                purchase = db.query(Purchase).filter(Purchase.transaction_id == order.order_code).first()

            existing_out = _valid_output_file(order.stamped_pdf_path) or (
                _valid_output_file(purchase.output_pdf_path) if purchase else None
            )
            if existing_out:
                if purchase:
                    handled_purchase_ids.add(purchase.id)
                    if purchase.status != PurchaseStatus.COMPLETED:
                        purchase.status = PurchaseStatus.COMPLETED
                        purchase.output_pdf_path = existing_out
                        purchase.completed_at = purchase.completed_at or datetime.utcnow()
                order.stamped_pdf_path = existing_out
                order.status = OrderFulfillmentStatus.READY_FOR_DELIVERY
                order.completed_at = order.completed_at or datetime.utcnow()
                db.commit()
                internal_event_bus.fire_event({
                    "event": "ORDER_READY",
                    "order_code": order.order_code,
                    "telegram_chat_id": order.telegram_chat_id,
                    "telegram_user_id": order.telegram_user_id,
                    "material_id": order.material_id,
                    "material_title": (purchase.material.title if purchase and purchase.material else "المذكرة المطلوبة"),
                    "stamped_pdf_path": existing_out,
                    "buyer_name": order.buyer_name_stamp,
                })
                logger.info(f"🩹 [Recovery] تم ترقية الطلب المكتمل فعلياً {order.order_code} إلى READY_FOR_DELIVERY")
                continue

            if purchase:
                handled_purchase_ids.add(purchase.id)
                kwargs = _build_stamp_kwargs(db, purchase, order)
                if kwargs:
                    jobs_to_resume.append(kwargs)
                    logger.info(f"🔄 [Recovery] جدولة استكمال ختم الطلب المنقطع {order.order_code} (Purchase #{purchase.id})")
                else:
                    purchase.status = PurchaseStatus.FAILED
                    order.status = OrderFulfillmentStatus.FAILED
                    order.error_message = "تعذر العثور على ملف المذكرة الأصلي بعد إعادة التشغيل"
                    db.commit()

        # 2. فحص المشتريات المباشرة (Store/App) العالقة في PROCESSING بدون BotOrder
        stuck_purchases = (
            db.query(Purchase)
            .filter(Purchase.status == PurchaseStatus.PROCESSING)
            .order_by(Purchase.id.asc())
            .all()
        )
        for purchase in stuck_purchases:
            if purchase.id in handled_purchase_ids:
                continue
            existing_out = _valid_output_file(purchase.output_pdf_path)
            if existing_out:
                purchase.status = PurchaseStatus.COMPLETED
                purchase.output_pdf_path = existing_out
                purchase.completed_at = purchase.completed_at or datetime.utcnow()
                db.commit()
                logger.info(f"🩹 [Recovery] تم إغلاق عملية الشراء #{purchase.id} بنجاح من الملف الموجود")
                continue

            kwargs = _build_stamp_kwargs(db, purchase, None)
            if kwargs:
                jobs_to_resume.append(kwargs)
                logger.info(f"🔄 [Recovery] جدولة استكمال ختم عملية الشراء المنقطعة #{purchase.id}")
            else:
                purchase.status = PurchaseStatus.FAILED
                db.commit()

    except Exception as exc:
        logger.error(f"⚠️ [Recovery Error] تعذر إتمام فحص الاستشفاء عند الإقلاع: {exc}")
    finally:
        db.close()

    return jobs_to_resume


async def recover_interrupted_stamps(delay_seconds: float = 2.0) -> None:
    """مهمة غير متزامنة تستكمل عمليات الختم المتوقفة بعد إعادة التشغيل."""
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    jobs = collect_and_heal_interrupted_jobs_sync()
    for kwargs in jobs:
        asyncio.create_task(stamp_pdf(**kwargs))
