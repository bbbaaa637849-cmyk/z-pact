# ================================================================
# app/routers/internal_bridge.py — الجسر البرمجي الداخلي
#
# المسؤوليات:
#   - التواصل الآمن بين منصة الويب وبوت التيليجرام ومحرك الـ SMS Webhook
#   - حماية المسارات برمز المصادقة الداخلي X-Internal-Service-Key
#   - إدارة ومطابقة طلبات التليجرام وإطلاق عمليات الختم الآلية
#   - قناة WebSocket للبث اللحظي لأحداث النظام وتأكيد الدفع وجهوزية الـ PDF
# ================================================================

import asyncio
import gc
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import (
    APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query,
    Request, Response, WebSocket, WebSocketDisconnect, status,
    UploadFile, File, Form
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from app.config import settings, stamp_settings
from app.database import get_db, SessionLocal
from app import crud, models, schemas
from app.models import (
    User, Material, Purchase, PurchaseStatus, StampLayout, LayoutType,
    TeacherProfile, BotSession, BotOrder, OrderFulfillmentStatus, PaymentSMSLog, WalletTransaction
)
from app.stamper import (
    stamp_pdf,
    get_default_stamp_elements,
    list_available_fonts,
    process_frame_image_to_transparent_png,
    render_stamped_page_preview_png,
    _resolve_material_path,
    _resolve_asset_path,
    _stamp_native,
    _add_marks_and_finalize,
    STAMP_SEMAPHORE,
    LOW_CPU_MODE,
)
from app.logger import logger
from app.formatting import clean_phone_number

router = APIRouter(prefix="/internal", tags=["Internal Bridge"])


# ================================================================
# 1. نظام التحقق والمصادقة للخدمات الداخلية (Machine-to-Machine)
# ================================================================

def verify_internal_key(request: Request) -> str:
    """
    التحقق من صحة مفتاح الأمان الداخلي في ترويسات الطلب أو معاملات الرابط
    """
    key = request.headers.get("X-Internal-Service-Key")
    if not key:
        key = request.query_params.get("token") or request.query_params.get("key")
    
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    
    if not key or key != expected_key:
        logger.warning(f"⛔ [Internal Bridge] محاولة وصول غير مصرح بها من: {request.client.host if request.client else 'unknown'}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: Invalid internal service key"
        )
    return key


# ================================================================
# 2. ناقل الأحداث اللحظي المزدوج (Internal Event Bus)
# ================================================================

class InternalEventBus:
    """
    إدارة اتصالات الـ WebSockets الداخلية بين السيرفر وبوت التيليجرام
    لبث إشعارات الدفع والختم والتسليم اللحظي.
    """
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"🔗 [Internal Event Bus] اتصال داخلي جديد. الإجمالي: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"🔌 [Internal Event Bus] انفصال اتصال. المتبقي: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """بث رسالة لجميع الخدمات المتصلة فوراً"""
        async with self._lock:
            connections = list(self.active_connections)
        
        for conn in connections:
            try:
                await conn.send_json(message)
            except Exception as e:
                logger.error(f"⚠️ [Internal Event Bus] خطأ أثناء الإرسال لعميل: {e}")
                async with self._lock:
                    if conn in self.active_connections:
                        self.active_connections.remove(conn)

    def fire_event(self, message: Dict[str, Any]):
        """إرسال الحدث بأمان سواء من مسار async أو sync thread"""
        try:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.broadcast(message))
                return
            except RuntimeError:
                pass

            import threading
            def _runner():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                new_loop.run_until_complete(self.broadcast(message))
                new_loop.close()

            threading.Thread(target=_runner, daemon=True).start()
        except Exception as e:
            logger.warning(f"⚠️ [Internal Event Bus] Failed to fire event: {e}")

internal_event_bus = InternalEventBus()


@router.websocket("/events/ws")
async def internal_ws_endpoint(websocket: WebSocket):
    """
    قناة الـ WebSocket الداخلية الخاصة بالبوت ومحرك الدفع.
    """
    token = websocket.query_params.get("token") or websocket.query_params.get("key")
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    
    if token != expected_key:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await internal_event_bus.connect(websocket)
    try:
        while True:
            # استقبال رسائل نبض القلب (Heartbeat / Ping) أو أوامر البوت
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await internal_event_bus.disconnect(websocket)
    except Exception as e:
        logger.error(f"⚠️ [Internal WS] استثناء في الاتصال: {e}")
        await internal_event_bus.disconnect(websocket)


# ================================================================
# 3. دوال مساعدة لإنشاء وإطلاق مهام الختم للطلبات
# ================================================================

def _get_or_create_bot_system_user(db: Session) -> User:
    """الحصول على مستخدم النظام الافتراضي لعمليات التليجرام"""
    bot_user = db.query(User).filter(User.email == "bot_service@elite.com").first()
    if not bot_user:
        from app.crud import hash_password
        bot_user = User(
            full_name="Telegram Bot Automation",
            email="bot_service@elite.com",
            password_hash=hash_password(secrets.token_hex(16)),
            role=models.UserRole.CUSTOMER,
            is_active=True,
            coins=999999.0
        )
        db.add(bot_user)
        db.commit()
        db.refresh(bot_user)
    return bot_user


def _trigger_order_stamping(
    order: BotOrder,
    background_tasks: BackgroundTasks,
    db: Session
) -> None:
    """
    تجهيز وإطلاق عملية ختم الـ PDF لطلب التيليجرام في الخلفية.
    """
    material = order.material
    if not material:
        material = db.query(Material).filter(Material.id == order.material_id).first()
    
    if not material:
        logger.error(f"❌ [Internal Bridge] المادة {order.material_id} غير موجودة لطلب {order.order_code}")
        order.status = OrderFulfillmentStatus.FAILED
        order.error_message = "المادة المطلوبة غير موجودة في النظام"
        db.commit()
        return

    # جلب التنسيق المناسب
    layout_type = LayoutType.DEFAULT_LOGO if order.watermark_type in ["logo", "teacher", "platform"] else LayoutType.DEFAULT_TEXT
    stamp_layout = crud.get_default_layout(db, material.id, layout_type)
    
    if not stamp_layout:
        stamp_layout = db.query(StampLayout).filter(StampLayout.material_id == material.id).first()

    raw_elements = stamp_layout.get_elements() if stamp_layout else []
    
    # معالجة نصوص الـ Tags
    processed_elements = []
    for el in raw_elements:
        el_copy = dict(el)
        if material.tag and el_copy.get("custom_text"):
            el_copy["custom_text"] = (
                el_copy["custom_text"]
                .replace("{tag}", material.tag)
                .replace("[tag]", material.tag)
            )
        processed_elements.append(el_copy)

    # شعار المدرس
    buyer_logo_path = order.watermark_logo_path
    if not buyer_logo_path and order.teacher_profile and order.teacher_profile.logo_image_path:
        buyer_logo_path = order.teacher_profile.logo_image_path

    # إنشاء أو تحديث سجل Purchase
    bot_system_user = _get_or_create_bot_system_user(db)
    
    purchase = None
    if order.purchase_id:
        purchase = db.query(Purchase).filter(Purchase.id == order.purchase_id).first()

    if not purchase:
        purchase = Purchase(
            buyer_id=order.user_id or bot_system_user.id,
            material_id=material.id,
            stamp_layout_id=stamp_layout.id if stamp_layout else None,
            buyer_name_stamp=order.buyer_name_stamp,
            buyer_phone_stamp=order.buyer_phone_stamp,
            buyer_phone_stamp_2=order.buyer_phone_stamp_2,
            doc_name_stamp=order.doc_name_stamp,
            series_name=order.series_name,
            watermark_text=order.watermark_text,
            watermark_type=order.watermark_type or "none",
            watermark_logo_path=buyer_logo_path,
            amount_paid=order.total_amount,
            status=PurchaseStatus.PROCESSING,
            transaction_id=order.order_code
        )
        db.add(purchase)
        db.commit()
        db.refresh(purchase)
        order.purchase_id = purchase.id
    else:
        purchase.status = PurchaseStatus.PROCESSING
        if order.series_name:
            purchase.series_name = order.series_name
        if buyer_logo_path:
            purchase.watermark_logo_path = buyer_logo_path
        if order.watermark_type:
            purchase.watermark_type = order.watermark_type
        db.commit()

    order.status = OrderFulfillmentStatus.PROCESSING_STAMP
    db.commit()

    logger.info(f"🚀 [Internal Bridge] إطلاق مهمة الختم لطلب {order.order_code} (Purchase ID: {purchase.id})")

    effective_series = order.series_name or order.doc_name_stamp

    # إطلاق مهمة الختم في الخلفية
    background_tasks.add_task(
        stamp_pdf,
        purchase_id=purchase.id,
        material_path=material.original_pdf_path,
        stamp_layout=processed_elements,
        buyer_name=order.buyer_name_stamp,
        buyer_phone=order.buyer_phone_stamp,
        watermark_text=order.watermark_text,
        doc_name_stamp=effective_series,
        compress=material.compression_enabled,
        material_tag=material.tag,
        watermark_type=order.watermark_type or "none",
        watermark_logo_path=buyer_logo_path,
    )


# ================================================================
# 4. مسارات الكتالوج والمواد (Materials Catalog)
# ================================================================

@router.get("/materials", summary="جلب كتالوج المذكرات المفعلة للبوت")
def get_materials_catalog(
    tag: Optional[str] = None,
    search: Optional[str] = None,
    include_unpublished: bool = False,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    """
    إرجاع قائمة المذكرات مع أسعارها وعدد صفحاتها وصور الأغلفة والإطارات
    لعرضها في قوائم وأزرار التيليجرام والميني آب.
    """
    query = db.query(Material)
    if not include_unpublished:
        query = query.filter(Material.is_published == True)
    if tag:
        query = query.filter(Material.tag == tag)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(Material.title.ilike(s) | Material.description.ilike(s))

    materials = query.order_by(Material.id.desc()).all()
    
    result = []
    for m in materials:
        resolved_orig = _resolve_material_path(m.original_pdf_path or "")
        file_exists = bool(resolved_orig and os.path.exists(resolved_orig))
        result.append({
            "id": m.id,
            "title": m.title,
            "tag": m.tag,
            "description": m.description,
            "price": float(m.price),
            "price_coins": float(m.price_coins or 0.0),
            "total_pages": m.total_pages or 0,
            "is_published": bool(m.is_published),
            "file_exists": file_exists,
            "cover_image_path": m.cover_image_path,
            "page_frame_path": m.page_frame_path,
            "has_frame": bool(m.page_frame_path),
            "created_at": m.created_at.isoformat() if m.created_at else None
        })
    return {"total": len(result), "materials": result}


# ================================================================
# 5. مسارات الملف التعريفي للمدرس (Teacher Profile)
# ================================================================

@router.get("/teachers/profile", summary="جلب الملف التعريفي للمدرس بمعرف التيليجرام")
def get_teacher_profile(
    telegram_user_id: str = Query(..., description="معرف المستخدم في تيليجرام"),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.telegram_user_id == str(telegram_user_id)
    ).first()

    if not profile:
        raise HTTPException(status_code=404, detail="الملف التعريفي غير مسجل بعد")

    return schemas.TeacherProfileOut.model_validate(profile)


@router.post("/teachers/profile", summary="إنشاء أو تحديث الملف التعريفي للمدرس")
def upsert_teacher_profile(
    data: schemas.TeacherProfileCreate,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    effective_name = (data.teacher_name or data.full_name or "").strip()
    clean_p1 = clean_phone_number(data.primary_phone or "") if (data.primary_phone or "").strip() else ""
    clean_p2 = clean_phone_number(data.secondary_phone) if data.secondary_phone else None

    is_admin_actor = (
        stamp_settings.is_admin_telegram_id(data.telegram_user_id)
        and (data.is_admin is not False)
    )
    ok_stamp, err_stamp = stamp_settings.validate_stamp_fields(
        teacher_name=effective_name,
        primary_phone=clean_p1,
        secondary_phone=clean_p2,
        series_title=data.series_title,
        watermark_text=data.watermark_text,
        is_admin=is_admin_actor,
    )
    if not ok_stamp:
        raise HTTPException(status_code=400, detail=err_stamp)

    profile = db.query(TeacherProfile).filter(
        TeacherProfile.telegram_user_id == str(data.telegram_user_id)
    ).first()

    if profile:
        profile.teacher_name = effective_name
        profile.telegram_username = data.telegram_username or profile.telegram_username
        profile.subject_specialty = data.subject_specialty
        profile.primary_phone = clean_p1
        profile.secondary_phone = clean_p2
        profile.series_title = data.series_title
        profile.watermark_type = data.watermark_type or profile.watermark_type
        profile.watermark_text = data.watermark_text if data.watermark_text is not None else profile.watermark_text
        profile.logo_image_path = data.logo_image_path or profile.logo_image_path
        profile.updated_at = datetime.utcnow()
    else:
        profile = TeacherProfile(
            telegram_user_id=str(data.telegram_user_id),
            telegram_username=data.telegram_username,
            teacher_name=effective_name,
            subject_specialty=data.subject_specialty,
            primary_phone=clean_p1,
            secondary_phone=clean_p2,
            series_title=data.series_title,
            watermark_type=data.watermark_type or "text",
            watermark_text=data.watermark_text,
            logo_image_path=data.logo_image_path,
            is_verified=False
        )
        db.add(profile)

    db.commit()
    db.refresh(profile)
    logger.info(f"✅ [Internal Bridge] تم حفظ الملف التعريفي للمدرس: {profile.teacher_name} ({profile.telegram_user_id})")
    return schemas.TeacherProfileOut.model_validate(profile)


# ================================================================
# 6. مسار جلسات البوت (Bot Sessions / FSM State)
# ================================================================

@router.post("/sessions/sync", summary="مزامنة حالة وجلسة مستخدم التيليجرام")
def sync_bot_session(
    data: schemas.BotSessionUpdate,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    session = db.query(BotSession).filter(
        BotSession.telegram_chat_id == str(data.telegram_chat_id)
    ).first()

    if session:
        session.current_state = data.current_state
        session.username = data.username or session.username
        if data.state_data is not None:
            session.state_data = data.state_data
        session.last_interaction = datetime.utcnow()
    else:
        session = BotSession(
            telegram_chat_id=str(data.telegram_chat_id),
            telegram_user_id=str(data.telegram_user_id),
            username=data.username,
            current_state=data.current_state,
            state_data=data.state_data or {}
        )
        db.add(session)

    db.commit()
    db.refresh(session)
    return schemas.BotSessionOut.model_validate(session)


@router.get("/sessions/{chat_id}", summary="جلب جلسة البوت الحالية")
def get_bot_session(
    chat_id: str,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    session = db.query(BotSession).filter(BotSession.telegram_chat_id == str(chat_id)).first()
    if not session:
        raise HTTPException(status_code=404, detail="الجلسة غير موجودة")
    return schemas.BotSessionOut.model_validate(session)


# ================================================================
# 7. مسارات الطلبات والمطابقة الآلية (Bot Orders & Auto-Fulfillment)
# ================================================================

@router.post("/orders/create", response_model=schemas.BotOrderOut, summary="إنشاء طلب بوت ومطابقته فوراً إن وُجد تحويل معلق")
def create_bot_order(
    data: schemas.BotOrderCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    """
    إنشاء طلب جديد، والبحث الفوري في رسائل الـ SMS غير المستهلكة
    لتأكيد الدفع وإطلاق عملية الختم فورياً دون أي تأخير.
    """
    material = crud.get_material(db, data.material_id)
    if not material or not material.is_published:
        raise HTTPException(status_code=404, detail="المادة غير متاحة أو غير منشورة")

    # كود الطلب الفريد
    order_code = f"ORD-{secrets.token_hex(3).upper()}"

    # تنظيف رقم الهاتف والتحقق طبقاً لملف stamp_settings.py
    buyer_name = (data.buyer_name_stamp or "").strip()
    stamp_phone = clean_phone_number(data.buyer_phone_stamp or "") if (data.buyer_phone_stamp or "").strip() else ""
    stamp_phone_2 = clean_phone_number(data.buyer_phone_stamp_2) if data.buyer_phone_stamp_2 else None
    claimed_phone = (
        (clean_phone_number(data.claimed_sender_phone) if data.claimed_sender_phone else "")
        or stamp_phone
        or None
    )

    is_admin_actor = (
        stamp_settings.is_admin_telegram_id(data.telegram_user_id)
        and (data.is_admin is not False)
    )
    ok_stamp, err_stamp = stamp_settings.validate_stamp_fields(
        buyer_name_stamp=buyer_name,
        buyer_phone_stamp=stamp_phone,
        buyer_phone_stamp_2=stamp_phone_2,
        series_name=data.series_name,
        doc_name_stamp=data.doc_name_stamp,
        watermark_text=data.watermark_text,
        is_admin=is_admin_actor,
    )
    if not ok_stamp:
        raise HTTPException(status_code=400, detail=err_stamp)

    # التحقق من القائمة السوداء
    blacklist_msg = crud.check_stamp_data_against_blacklist(
        db, buyer_name, stamp_phone, data.watermark_text
    )
    if blacklist_msg:
        raise HTTPException(status_code=403, detail=blacklist_msg)

    # إنشاء كائن الطلب
    order = BotOrder(
        order_code=order_code,
        telegram_chat_id=str(data.telegram_chat_id),
        telegram_user_id=str(data.telegram_user_id) if data.telegram_user_id else None,
        teacher_profile_id=data.teacher_profile_id,
        material_id=material.id,
        buyer_name_stamp=buyer_name,
        buyer_phone_stamp=stamp_phone,
        buyer_phone_stamp_2=stamp_phone_2,
        doc_name_stamp=data.doc_name_stamp,
        series_name=data.series_name,
        watermark_text=data.watermark_text,
        watermark_type=data.watermark_type or "none",
        watermark_logo_path=data.watermark_logo_path,
        total_amount=float(data.total_amount),
        payment_method=data.payment_method or "vodafone_cash",
        claimed_sender_phone=claimed_phone,
        receipt_image_path=data.receipt_image_path,
        receipt_file_id=data.receipt_file_id,
        payment_reference=data.payment_reference,
        status=OrderFulfillmentStatus.PENDING_PAYMENT,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    logger.info(f"📝 [Internal Bridge] تم تسجيل طلب جديد: {order.order_code} بمبلغ {order.total_amount} ج.م للمشتري {order.buyer_name_stamp}")

    # ============================================================
    # المطابقة الفورية (Immediate Cross-Reconciliation Check)
    # فحص إذا كانت رسالة التحويل قد وصلت بالفعل قبل إنشاء الطلب
    # ============================================================
    ten_minutes_ago = datetime.utcnow() - timedelta(minutes=15)
    matched_sms = None

    if claimed_phone:
        # البحث عن رسالة غير مستهلكة تطابق المبلغ والرقم
        candidates = db.query(PaymentSMSLog).filter(
            PaymentSMSLog.is_reconciled == False,
            PaymentSMSLog.extracted_amount == float(order.total_amount),
            PaymentSMSLog.received_at >= ten_minutes_ago
        ).order_by(PaymentSMSLog.received_at.desc()).all()

        for sms in candidates:
            if not sms.extracted_sender:
                continue
            clean_sms_sender = clean_phone_number(sms.extracted_sender)
            # مطابقة تامة أو جزئية برقم الهاتف
            if clean_sms_sender and (clean_sms_sender == claimed_phone or clean_sms_sender.endswith(claimed_phone[-9:]) or claimed_phone.endswith(clean_sms_sender[-9:])):
                matched_sms = sms
                break

    if matched_sms:
        # ✅ وُجد تحويل معلق مطابق تماماً! نؤكد الدفع فوراً
        logger.info(f"🎯 [Internal Bridge] مطابقة فورية ناجحة للطلب {order.order_code} مع رسالة SMS رقم {matched_sms.id}")
        matched_sms.is_reconciled = True
        matched_sms.matched_order_id = order.id
        matched_sms.match_confidence = 1.0
        matched_sms.reconciled_at = datetime.utcnow()

        # استهلاك المعاملة الموازية في جدول محفظة الموقع لمنع الصرف المزدوج
        if matched_sms.extracted_sender:
            pending_wallet_tx = db.query(WalletTransaction).filter(
                WalletTransaction.sender_phone == str(matched_sms.extracted_sender),
                WalletTransaction.amount == float(order.total_amount),
                WalletTransaction.status == "pending"
            ).order_by(WalletTransaction.transaction_date.asc()).first()
            if pending_wallet_tx:
                pending_wallet_tx.status = "completed"
        
        order.status = OrderFulfillmentStatus.PAYMENT_CONFIRMED
        order.payment_reference = matched_sms.reference_number
        db.commit()
        db.refresh(order)

        # إطلاق مهمة الختم فوراً في الخلفية
        _trigger_order_stamping(order, background_tasks, db)

        # إرسال إشعار لحظي عبر Event Bus
        internal_event_bus.fire_event({
            "event": "ORDER_RECONCILED",
            "order_code": order.order_code,
            "telegram_chat_id": order.telegram_chat_id,
            "telegram_user_id": order.telegram_user_id,
            "amount": order.total_amount,
            "status": order.status.value,
            "message": "تم تأكيد الدفع وجاري ختم المذكرة الآن"
        })

    return schemas.BotOrderOut.model_validate(order)


@router.get("/orders/{order_code}/status", response_model=schemas.BotOrderOut, summary="الاستعلام عن حالة الطلب ومسار الملف")
def get_order_status(
    order_code: str,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    order = db.query(BotOrder).filter(BotOrder.order_code == order_code).first()
    if not order:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")
    return schemas.BotOrderOut.model_validate(order)


@router.post("/orders/{order_code}/admin-action", response_model=schemas.BotOrderOut, summary="إجراءات الأدمن من التيليجرام على الطلب")
def admin_order_action(
    order_code: str,
    action_data: schemas.BotOrderAdminAction,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    """
    سماح لأدمن التيليجرام بـ:
      - force_approve: قبول يدوي حتى لو لم ترصد رسالة الـ SMS
      - reject: رفض الطلب وإلغاؤه
      - retrigger_stamp: إعادة توليد الختم إذا حدث عطل
    """
    order = db.query(BotOrder).filter(BotOrder.order_code == order_code).first()
    if not order:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")

    action = action_data.action.lower()
    if action == "force_approve":
        order.status = OrderFulfillmentStatus.PAYMENT_CONFIRMED
        db.commit()
        _trigger_order_stamping(order, background_tasks, db)
        
        internal_event_bus.fire_event({
            "event": "ORDER_ADMIN_APPROVED",
            "order_code": order.order_code,
            "telegram_chat_id": order.telegram_chat_id,
            "admin_notes": action_data.admin_notes
        })
        logger.info(f"👑 [Bot Admin] موافقة يدوية فورية على طلب {order.order_code}")

    elif action == "reject":
        order.status = OrderFulfillmentStatus.CANCELLED
        order.error_message = action_data.admin_notes or "تم رفض الطلب بواسطة المشرف"
        db.commit()
        
        internal_event_bus.fire_event({
            "event": "ORDER_CANCELLED",
            "order_code": order.order_code,
            "telegram_chat_id": order.telegram_chat_id,
            "reason": order.error_message
        })
        logger.info(f"🚫 [Bot Admin] رفض الطلب {order.order_code}: {order.error_message}")

    elif action in ["retrigger", "retrigger_stamp"]:
        _trigger_order_stamping(order, background_tasks, db)
        logger.info(f"🔄 [Bot Admin] إعادة إطلاق الختم للطلب {order.order_code}")

    elif action in ["deliver", "mark_delivered"]:
        order.status = OrderFulfillmentStatus.DELIVERED
        db.commit()
        logger.info(f"📦 [Bot Admin] تحديث حالة الطلب {order.order_code} إلى DELIVERED")

    else:
        raise HTTPException(status_code=400, detail=f"إجراء غير معروف: {action}")

    db.refresh(order)
    return schemas.BotOrderOut.model_validate(order)


@router.get("/orders/{order_code}/download", summary="تحميل ملف الـ PDF المختوم من قبل خدمة البوت")
def download_stamped_order_pdf(
    order_code: str,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    """
    تحميل الملف النهائي ليرسله البوت كملف للمدرس في محادثة التيليجرام
    """
    order = db.query(BotOrder).filter(BotOrder.order_code == order_code).first()
    if not order:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")

    file_path = order.stamped_pdf_path
    if not file_path or not os.path.exists(file_path):
        # البحث عن ملف الـ Purchase المرتبط
        if order.purchase and order.purchase.output_pdf_path and os.path.exists(order.purchase.output_pdf_path):
            file_path = order.purchase.output_pdf_path
        else:
            raise HTTPException(status_code=404, detail="الملف المختوم غير جاهز بعد أو غير موجود على السيرفر")

    filename = f"{order.material.title if order.material else 'Booklet'}_{order.order_code}.pdf"
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=filename
    )


# ================================================================
# 8. مسار معالجة ومطابقة رسائل الـ SMS (SMS Webhook Reconcile)
# ================================================================

@router.post("/webhook/reconcile", summary="استقبال رسائل الـ SMS ومطابقتها مع الطلبات المعلقة")
def reconcile_sms_payment(
    data: schemas.PaymentSMSReconcileRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    """
    تستقبل هذه النقطة الرسائل المفروزة من محرك الـ payment_webhook:
    1. تسجيل الرسالة في جدول payment_sms_logs
    2. فحص التكرار (منع الاستخدام المزدوج)
    3. البحث عن أي BotOrder ينتظر هذا المبلغ من هذا الرقم
    4. إذا وُجد تطابق: تفعيل الختم فوراً وإشعار البوت
    """
    raw_message = data.raw_message.strip()
    source_provider = data.source_provider
    amount = float(data.extracted_amount) if data.extracted_amount is not None else None
    sender = clean_phone_number(data.extracted_sender) if data.extracted_sender else None
    ref_num = data.reference_number.strip() if data.reference_number else None

    logger.info(f"📥 [Reconcile Engine] معالجة تحويل: {amount} ج.م من '{sender}' | المصدر: {source_provider} | كود العملية: {ref_num}")

    # فحص التكرار بناءً على كود العملية أو النص المتطابق لمنع التكرار عند مزامنة صندوق الرسائل التاريخي
    if ref_num:
        existing_ref = db.query(PaymentSMSLog).filter(
            PaymentSMSLog.reference_number == ref_num,
            PaymentSMSLog.source_provider == source_provider
        ).first()
        if existing_ref:
            logger.info(f"⚠️ [Reconcile Engine] تحويل مكرر بكود العملية {ref_num} — تم التجاهل")
            return {
                "status": "ignored",
                "reason": "duplicate_reference",
                "log_id": existing_ref.id,
                "is_reconciled": existing_ref.is_reconciled
            }
    else:
        recent_cutoff = datetime.utcnow() - timedelta(days=7)
        existing_raw = db.query(PaymentSMSLog).filter(
            PaymentSMSLog.raw_message == raw_message,
            PaymentSMSLog.received_at >= recent_cutoff
        ).first()
        if existing_raw:
            return {
                "status": "ignored",
                "reason": "duplicate_raw_message",
                "log_id": existing_raw.id,
                "is_reconciled": existing_raw.is_reconciled
            }

    # تسجيل الرسالة الجديدة في السجل
    sms_log = PaymentSMSLog(
        raw_message=raw_message,
        source_provider=source_provider,
        extracted_amount=amount,
        extracted_sender=sender or data.extracted_sender,
        reference_number=ref_num,
        is_reconciled=False,
        match_confidence=0.0
    )
    db.add(sms_log)
    db.commit()
    db.refresh(sms_log)

    if amount is None or amount <= 0:
        return {"status": "logged_unparsed", "log_id": sms_log.id, "reason": "No valid amount extracted"}

    # ============================================================
    # خوارزمية المطابقة التلقائية مع الطلبات المعلقة (Reconciliation)
    # ============================================================
    window_start = datetime.utcnow() - timedelta(hours=24) # السماح بمطابقة أي طلب معلق تم إنشاؤه في آخر 24 ساعة
    
    pending_orders_query = db.query(BotOrder).filter(
        BotOrder.status == OrderFulfillmentStatus.PENDING_PAYMENT,
        BotOrder.total_amount == amount,
        BotOrder.created_at >= window_start
    ).order_by(BotOrder.created_at.desc())

    pending_orders = pending_orders_query.all()
    matched_order = None
    match_confidence = 0.0

    if sender:
        for order in pending_orders:
            claimed = clean_phone_number(order.claimed_sender_phone)
            buyer_p = clean_phone_number(order.buyer_phone_stamp)
            
            # مطابقة تامة مع رقم التحويل المدعى
            if claimed and (claimed == sender or claimed.endswith(sender[-9:]) or sender.endswith(claimed[-9:])):
                matched_order = order
                match_confidence = 1.0
                break
            
            # مطابقة مع رقم الهاتف الأساسي للمشتري
            if buyer_p and (buyer_p == sender or buyer_p.endswith(sender[-9:]) or sender.endswith(buyer_p[-9:])):
                matched_order = order
                match_confidence = 0.95
                break

    # إذا لم نجد برقم الهاتف، وكان هناك طلب واحد معلق بالضبط بنفس المبلغ خلال آخر 15 دقيقة
    if not matched_order and len(pending_orders) == 1:
        recent_order = pending_orders[0]
        if (datetime.utcnow() - recent_order.created_at).total_seconds() < 900: # 15 دقيقة
            matched_order = recent_order
            match_confidence = 0.85
            logger.info(f"🔍 [Reconcile Engine] مطابقة ترجيحية بالمبلغ والتوقيت لطلب {recent_order.order_code}")

    if matched_order:
        # ✅ نجاح المطابقة!
        sms_log.is_reconciled = True
        sms_log.matched_order_id = matched_order.id
        sms_log.match_confidence = match_confidence
        sms_log.reconciled_at = datetime.utcnow()

        matched_order.status = OrderFulfillmentStatus.PAYMENT_CONFIRMED
        matched_order.payment_reference = ref_num or f"SMS-{sms_log.id}"
        db.commit()
        db.refresh(matched_order)

        logger.info(f"🎉 [Reconcile Engine] تم مطابقة الطلب {matched_order.order_code} بنجاح! جاري إطلاق الختم...")

        # إطلاق الختم في الخلفية
        _trigger_order_stamping(matched_order, background_tasks, db)

        # إشعار عبر Event Bus
        internal_event_bus.fire_event({
            "event": "ORDER_RECONCILED",
            "order_code": matched_order.order_code,
            "telegram_chat_id": matched_order.telegram_chat_id,
            "telegram_user_id": matched_order.telegram_user_id,
            "amount": matched_order.total_amount,
            "status": matched_order.status.value,
            "ref": matched_order.payment_reference,
            "message": "تم تأكيد الدفع وجاري تجهيز الملف فوراً"
        })

        return {
            "status": "reconciled",
            "matched": True,
            "order_code": matched_order.order_code,
            "chat_id": matched_order.telegram_chat_id,
            "amount": matched_order.total_amount,
            "confidence": match_confidence,
            "log_id": sms_log.id
        }

    # مطابقة تلقائية للسلات المجمعة متعددة المذكرات (Multi-Book Bundle Reconciliation)
    pending_bundle_items = db.query(BotOrder).filter(
        BotOrder.status == OrderFulfillmentStatus.PENDING_PAYMENT,
        BotOrder.bundle_code.isnot(None),
        BotOrder.created_at >= window_start
    ).order_by(BotOrder.created_at.desc()).all()

    bundles_map: Dict[str, List[BotOrder]] = {}
    for bo_item in pending_bundle_items:
        bundles_map.setdefault(bo_item.bundle_code, []).append(bo_item)

    for b_code, b_items in bundles_map.items():
        b_sum = round(sum(float(x.total_amount or 0.0) for x in b_items), 2)
        if abs(b_sum - float(amount)) <= 0.5:
            first_item = b_items[0]
            claimed = clean_phone_number(first_item.claimed_sender_phone)
            buyer_p = clean_phone_number(first_item.buyer_phone_stamp)
            phone_ok = False
            if sender:
                if claimed and (claimed == sender or claimed.endswith(sender[-9:]) or sender.endswith(claimed[-9:])):
                    phone_ok = True
                elif buyer_p and (buyer_p == sender or buyer_p.endswith(sender[-9:]) or sender.endswith(buyer_p[-9:])):
                    phone_ok = True
            if phone_ok:
                sms_log.is_reconciled = True
                sms_log.matched_order_id = first_item.id
                sms_log.match_confidence = 1.0
                sms_log.reconciled_at = datetime.utcnow()
                for x in b_items:
                    x.status = OrderFulfillmentStatus.PAYMENT_CONFIRMED
                    x.payment_reference = ref_num or f"SMS-BUNDLE-{sms_log.id}"
                db.commit()
                for x in b_items:
                    db.refresh(x)
                    _trigger_order_stamping(x, background_tasks, db)
                internal_event_bus.fire_event({
                    "event": "ORDER_RECONCILED",
                    "order_code": b_code,
                    "telegram_chat_id": first_item.telegram_chat_id,
                    "telegram_user_id": first_item.telegram_user_id,
                    "amount": b_sum,
                    "status": "payment_confirmed",
                    "ref": sms_log.reference_number,
                    "message": f"تم تأكيد تحويل السلة المجمعة ({len(b_items)} مذكرات) وجاري الختم فوراً"
                })
                return {
                    "status": "reconciled",
                    "matched": True,
                    "order_code": b_code,
                    "bundle_items": len(b_items),
                    "chat_id": first_item.telegram_chat_id,
                    "amount": b_sum,
                    "confidence": 1.0,
                    "log_id": sms_log.id
                }

    # تسجيل التحويل أيضاً في جدول محفظة الموقع (WalletTransaction) ليعمل شحن الكوينز التلقائي على الويب وتطبيق المدرسين
    wallet_sender = str(sender or data.extracted_sender or "").strip()
    if wallet_sender and amount and amount > 0:
        dup_cutoff = datetime.utcnow() - timedelta(minutes=5)
        existing_wallet_tx = db.query(WalletTransaction).filter(
            WalletTransaction.sender_phone == wallet_sender,
            WalletTransaction.amount == float(amount),
            WalletTransaction.transaction_date >= dup_cutoff
        ).first()
        if not existing_wallet_tx:
            db.add(WalletTransaction(
                sender_phone=wallet_sender,
                amount=float(amount),
                source=source_provider or "VodafoneCash",
                status="pending"
            ))
            db.commit()

    logger.info(f"⏳ [Reconcile Engine] لم يتم العثور على طلب معلق مطابق للمبلغ {amount} من {sender}. حُفظت الرسالة في السيرفر للمطابقة اللاحقة وشحن المحفظة.")
    return {
        "status": "stored_unmatched",
        "matched": False,
        "amount": amount,
        "sender": sender,
        "log_id": sms_log.id
    }


# ================================================================
# 9. إحصائيات لوحة تحكم الأدمن في التيليجرام (Admin Bot Stats)
# ================================================================

@router.get("/admin/stats", response_model=schemas.InternalAdminStats, summary="إحصائيات المنصة الشاملة لبوت الإدارة")
def get_internal_admin_stats(
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    total_orders = db.query(func.count(BotOrder.id)).scalar() or 0
    pending_orders = db.query(func.count(BotOrder.id)).filter(BotOrder.status == OrderFulfillmentStatus.PENDING_PAYMENT).scalar() or 0
    completed_orders = db.query(func.count(BotOrder.id)).filter(BotOrder.status.in_([OrderFulfillmentStatus.READY_FOR_DELIVERY, OrderFulfillmentStatus.DELIVERED])).scalar() or 0
    
    total_materials = db.query(func.count(Material.id)).scalar() or 0
    total_teachers = db.query(func.count(TeacherProfile.id)).scalar() or 0
    
    total_sms_logs = db.query(func.count(PaymentSMSLog.id)).scalar() or 0
    unreconciled_sms_logs = db.query(func.count(PaymentSMSLog.id)).filter(PaymentSMSLog.is_reconciled == False).scalar() or 0

    latest_orders = db.query(BotOrder).order_by(BotOrder.id.desc()).limit(10).all()
    undelivered_orders = (
        db.query(BotOrder)
        .filter(BotOrder.status == OrderFulfillmentStatus.READY_FOR_DELIVERY)
        .order_by(BotOrder.id.asc())
        .limit(50)
        .all()
    )
    seen_order_ids = set()
    recent_orders = []
    for o in (*undelivered_orders, *latest_orders):
        if o.id not in seen_order_ids:
            seen_order_ids.add(o.id)
            recent_orders.append(o)
    recent_unmatched_sms = db.query(PaymentSMSLog).filter(PaymentSMSLog.is_reconciled == False).order_by(PaymentSMSLog.id.desc()).limit(10).all()

    return schemas.InternalAdminStats(
        total_orders=total_orders,
        pending_orders=pending_orders,
        completed_orders=completed_orders,
        total_materials=total_materials,
        total_teachers=total_teachers,
        total_sms_logs=total_sms_logs,
        unreconciled_sms_logs=unreconciled_sms_logs,
        recent_orders=[schemas.BotOrderOut.model_validate(o) for o in recent_orders],
        recent_unmatched_sms=[schemas.PaymentSMSLogOut.model_validate(s) for s in recent_unmatched_sms]
    )


# ================================================================
# 10. استعلام طلبات العميل (User Orders List)
# ================================================================

@router.get("/orders/user/{chat_id}", response_model=List[schemas.BotOrderOut], summary="جلب قائمة طلبات مستخدم معين")
def get_user_orders(
    chat_id: str,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    orders = db.query(BotOrder).filter(
        (BotOrder.telegram_chat_id == str(chat_id)) | (BotOrder.telegram_user_id == str(chat_id))
    ).order_by(BotOrder.id.desc()).limit(15).all()
    return [schemas.BotOrderOut.model_validate(o) for o in orders]


# ================================================================
# 11. إدارة المذكرات والأسعار من لوحة الأدمن والبوت (Admin Material Manager)
# ================================================================

@router.put("/admin/materials/{material_id}", summary="تعديل سعر وبيانات المذكرة")
def admin_update_material(
    material_id: int,
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    mat = db.query(Material).filter(Material.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المذكرة غير موجودة")

    if "price" in data:
        mat.price = float(data["price"])
    if "title" in data:
        mat.title = str(data["title"]).strip()
    if "tag" in data:
        mat.tag = str(data["tag"]).strip()
    if "description" in data:
        mat.description = str(data["description"]).strip()
    if "is_published" in data:
        mat.is_published = bool(data["is_published"])

    db.commit()
    db.refresh(mat)
    logger.info(f"📚 [Admin Bridge] تم تحديث المذكرة {material_id}: {mat.title} - السعر: {mat.price}")
    return schemas.MaterialOut.model_validate(mat)


@router.post("/admin/materials/{material_id}/toggle-publish", summary="تبديل حالة النشر للمذكرة")
def admin_toggle_material_publish(
    material_id: int,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    mat = db.query(Material).filter(Material.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المذكرة غير موجودة")

    mat.is_published = not mat.is_published
    db.commit()
    db.refresh(mat)
    status_str = "منشورة" if mat.is_published else "مخفية"
    logger.info(f"📚 [Admin Bridge] تم تغيير حالة نشر المذكرة {material_id} إلى: {status_str}")
    return {"id": mat.id, "is_published": mat.is_published, "status": status_str}


@router.delete("/admin/materials/{material_id}", summary="حذف مذكرة من المتجر")
def admin_delete_material(
    material_id: int,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    mat = db.query(Material).filter(Material.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المذكرة غير موجودة")

    db.delete(mat)
    db.commit()
    logger.info(f"🗑️ [Admin Bridge] تم حذف المذكرة {material_id}")
    return {"status": "deleted", "id": material_id}


@router.post("/admin/materials/upload", summary="رفع مذكرة جديدة مباشرة من البوت أو لوحة التحكم")
async def admin_upload_material(
    file: UploadFile = File(...),
    title: str = Form(...),
    price: float = Form(...),
    tag: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    price_coins: Optional[float] = Form(None),
    cover_image: Optional[UploadFile] = File(None),
    page_frame: Optional[UploadFile] = File(None),
    font_family: Optional[str] = Form("cairo"),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    admin_user = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
    if not admin_user:
        raise HTTPException(status_code=400, detail="لا يوجد مستخدم مدير في النظام")

    mat_dir = os.path.join(settings.STORAGE_DIR, "originals")
    covers_dir = os.path.join(settings.STORAGE_DIR, "covers")
    frames_dir = os.path.join(settings.STORAGE_DIR, "frames")
    os.makedirs(mat_dir, exist_ok=True)
    os.makedirs(covers_dir, exist_ok=True)
    os.makedirs(frames_dir, exist_ok=True)

    orig_name = file.filename or "booklet.pdf"
    clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', orig_name)
    safe_name = f"mat_{secrets.token_hex(4)}_{clean_name}"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    file_path = os.path.join(mat_dir, safe_name)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    try:
        from app.stamper import optimize_original_pdf_file
        await asyncio.to_thread(optimize_original_pdf_file, file_path)
    except Exception:
        pass

    total_pages = 1
    cover_image_path = None
    try:
        import fitz
        doc = fitz.open(file_path)
        total_pages = len(doc)
        if not cover_image and total_pages > 0:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            c_name = f"cov_{secrets.token_hex(6)}.png"
            pix.save(os.path.join(covers_dir, c_name))
            cover_image_path = f"/storage/covers/{c_name}"
        doc.close()
    except Exception:
        pass

    if cover_image:
        cov_bytes = await cover_image.read()
        cov_ext = os.path.splitext(cover_image.filename or "cover.png")[1] or ".png"
        c_name = f"cov_{secrets.token_hex(6)}{cov_ext}"
        with open(os.path.join(covers_dir, c_name), "wb") as cf:
            cf.write(cov_bytes)
        cover_image_path = f"/storage/covers/{c_name}"

    page_frame_path = None
    if page_frame:
        fr_bytes = await page_frame.read()
        fr_name = f"frm_{secrets.token_hex(6)}.png"
        fr_abs = os.path.join(frames_dir, fr_name)
        process_frame_image_to_transparent_png(fr_bytes, fr_abs)
        page_frame_path = f"/storage/frames/{fr_name}"

    effective_coins = float(price_coins) if price_coins is not None else float(price)
    new_mat = Material(
        uploaded_by=admin_user.id,
        title=title.strip(),
        tag=tag.strip() if tag else "مذكرات عامة",
        description=description.strip() if description else f"مذكرة {title.strip()}",
        price=float(price),
        price_coins=effective_coins,
        original_pdf_path=settings.to_portable_storage_path(file_path, "originals"),
        cover_image_path=cover_image_path,
        page_frame_path=page_frame_path,
        total_pages=total_pages,
        is_published=True,
    )
    db.add(new_mat)
    db.commit()
    db.refresh(new_mat)

    # إنشاء تخطيط افتراضي صالح للأختام (DEFAULT_TEXT) باستخدام عناصر معيارية كاملة
    default_elements = get_default_stamp_elements(font_family=(font_family or "cairo").strip().lower())
    default_layout = StampLayout(
        material_id=new_mat.id,
        created_by=admin_user.id,
        layout_type=LayoutType.DEFAULT_TEXT,
        stamp_elements=default_elements,
    )
    db.add(default_layout)
    db.commit()

    try:
        from app.routers.materials import invalidate_materials_cache
        invalidate_materials_cache()
    except Exception:
        pass

    logger.info(f"✨ [Admin Bridge] تم رفع ونشر مذكرة جديدة: '{new_mat.title}' ({total_pages} ص) بسعر {new_mat.price} ج.م")
    return schemas.MaterialOut.model_validate(new_mat)


# ================================================================
# 12. مستلمو الإذاعة العامة (Broadcast Recipients)
# ================================================================

@router.get("/admin/broadcast-recipients", summary="جلب معرفات الدردشة لجميع المدرسين لإرسال الإذاعة")
def get_broadcast_recipients(
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key)
):
    sessions = db.query(models.BotSession.telegram_chat_id).filter(models.BotSession.telegram_chat_id != None).distinct().all()
    orders = db.query(models.BotOrder.telegram_chat_id).filter(models.BotOrder.telegram_chat_id != None).distinct().all()
    teachers = db.query(models.TeacherProfile.telegram_user_id).filter(models.TeacherProfile.telegram_user_id != None).distinct().all()

    chat_ids = set()
    for row in sessions + orders + teachers:
        cid = row[0]
        if cid and str(cid).strip().isdigit() and len(str(cid).strip()) >= 5:
            chat_ids.add(int(cid))

    return {"total_recipients": len(chat_ids), "chat_ids": list(chat_ids)}


# ================================================================
# 13. طابور إرسال واستلام رموز الـ OTP (Admin SIM SMS Relay + Telegram Bot)
# ================================================================

@router.get("/otp/outbox", summary="جلب رسائل الـ OTP الجاهزة للإرسال عبر شريحة هاتف الأدمن (SIM Outbox)")
def get_otp_sms_outbox(
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    now = datetime.utcnow()
    items = (
        db.query(models.OtpChallenge)
        .filter(
            models.OtpChallenge.sms_dispatch_status == "queued",
            models.OtpChallenge.is_verified == False,
            models.OtpChallenge.otp_plain_outbox != None,
            models.OtpChallenge.expires_at > now,
        )
        .order_by(models.OtpChallenge.id.asc())
        .limit(15)
        .all()
    )
    items_list = [
        {
            "id": c.id,
            "phone": c.phone,
            "otp_code": c.otp_plain_outbox,
            "target_role": c.target_role,
            "sms_text": f"رمز التحقق السري لمنصة Z-PACT هو: {c.otp_plain_outbox}\nلا تشارك هذا الكود مع أي شخص.",
        }
        for c in items
    ]
    return {
        "status": "ok",
        "items": items_list,
        "outbox": items_list,
    }


@router.post("/otp/outbox/{challenge_id}/ack", summary="تأكيد إرسال رسالة الـ OTP بنجاح من شريحة هاتف الأدمن")
def ack_otp_sms_dispatched(
    challenge_id: int,
    payload: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    ch = db.query(models.OtpChallenge).filter(models.OtpChallenge.id == challenge_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="OTP challenge not found")
    ok_flag = True if not payload else bool(payload.get("sent", True))
    ch.sms_dispatch_status = "sent" if ok_flag else "failed"
    db.commit()
    return {"status": "ok", "id": ch.id, "sms_dispatch_status": ch.sms_dispatch_status}


def normalize_eg_phone(raw_phone: str) -> str:
    """تطبيع أرقام الهواتف المصرية لمقارنة دقيقة وموثوقة"""
    if not raw_phone:
        return ""
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    s = str(raw_phone).translate(trans).strip()
    digits = re.sub(r"\D+", "", s)
    if digits.startswith("0020") and len(digits) >= 14:
        digits = "0" + digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits or s


@router.post("/otp/claim-telegram", summary="استلام كود الـ OTP فوراً داخل بوت التليجرام وربط الرقم تلقائياً")
def claim_otp_via_telegram_bot(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    token = str(payload.get("challenge_token", "")).strip()
    tg_uid = str(payload.get("telegram_user_id", "")).strip()
    tg_uname = str(payload.get("telegram_username", "") or "").strip() or None
    raw_tg_phone = str(payload.get("telegram_phone", "") or "").strip()

    if not token:
        raise HTTPException(status_code=400, detail="Missing challenge_token")

    ch = db.query(models.OtpChallenge).filter(models.OtpChallenge.challenge_token == token).first()
    if not ch or ch.is_verified or ch.expires_at <= datetime.utcnow() or not ch.otp_plain_outbox:
        raise HTTPException(status_code=404, detail="رمز التحقق منتهي الصلاحية أو تم استخدامه بالفعل")

    admin_env_id = os.getenv("ADMIN_TELEGRAM_ID", "8287744705")
    admin_ids = {"8287744705", str(getattr(settings, "ADMIN_TELEGRAM_ID", admin_env_id) or "").strip()}
    is_admin_user = (tg_uid in admin_ids)

    ch_norm = normalize_eg_phone(ch.phone)
    tg_norm = normalize_eg_phone(raw_tg_phone) if raw_tg_phone else ""

    phone_verified = False

    # 1. حساب الأدمن يُسمح له مباشرة للاختبار والمراقبة
    if is_admin_user:
        phone_verified = True
    # 2. إذا تم تمرير رقم الهاتف الموثق من تليجرام (مشاركة جهة الاتصال)
    elif tg_norm:
        if tg_norm == ch_norm:
            phone_verified = True
        else:
            return {
                "status": "phone_mismatch",
                "expected_phone": ch.phone,
                "actual_phone": raw_tg_phone,
                "message": f"رقم حساب التليجرام ({raw_tg_phone}) لا يتطابق مع الرقم المطلوب ({ch.phone})",
            }
    # 3. فحص هل هذا الحساب التليجرامي موثق ومسجل سابقاً بنفس رقم الهاتف
    else:
        existing_tp = db.query(models.TeacherProfile).filter(
            models.TeacherProfile.telegram_user_id == tg_uid,
            models.TeacherProfile.primary_phone == ch.phone,
        ).first()
        existing_snd = db.query(models.SenderAgent).filter(
            models.SenderAgent.telegram_user_id == tg_uid,
            models.SenderAgent.phone == ch.phone,
        ).first()
        if existing_tp or existing_snd:
            phone_verified = True

    # إذا لم يكن الهاتف موثقاً بعد بحساب التليجرام الحالي، نطلب مشاركة جهة الاتصال
    if not phone_verified:
        return {
            "status": "need_contact_verification",
            "required_phone": ch.phone,
            "masked_phone": ch.phone[:3] + "****" + ch.phone[-4:] if len(ch.phone) >= 7 else ch.phone,
            "target_role": ch.target_role,
            "purpose": ch.purpose,
            "challenge_token": token,
        }

    ch.telegram_delivered = True

    # ربط معرف التليجرام برقم الهاتف لتوصيل الأكواد القادمة تلقائياً بدون ضغطة!
    if tg_uid:
        if ch.target_role == "sender":
            snd = db.query(models.SenderAgent).filter(models.SenderAgent.phone == ch.phone).first()
            if snd and not snd.telegram_user_id:
                snd.telegram_user_id = tg_uid
        else:
            tp = db.query(models.TeacherProfile).filter(models.TeacherProfile.primary_phone == ch.phone).first()
            if tp and not tp.telegram_user_id:
                existing_tg = db.query(models.TeacherProfile).filter(models.TeacherProfile.telegram_user_id == tg_uid).first()
                if not existing_tg:
                    tp.telegram_user_id = tg_uid
                    if tg_uname:
                        tp.telegram_username = tg_uname

    db.commit()
    return {
        "status": "ok",
        "otp_code": ch.otp_plain_outbox,
        "phone": ch.phone,
        "target_role": ch.target_role,
        "purpose": ch.purpose,
    }


# ================================================================
# 14. استوديو الموبايل والـ Bot (الإحداثيات، الخطوط، الإطارات، الختم المتسلسل الخفيف)
# ================================================================

def _get_material_or_404(db: Session, material_id: int) -> Material:
    mat = db.query(Material).filter(Material.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المذكرة غير موجودة")
    return mat


def _get_or_build_layout_elements(db: Session, material_id: int, layout_type: LayoutType = LayoutType.DEFAULT_TEXT) -> tuple[Optional[StampLayout], list[dict]]:
    layout = crud.get_default_layout(db, material_id, layout_type)
    if not layout:
        layout = db.query(StampLayout).filter(StampLayout.material_id == material_id).first()
    elements = layout.get_elements() if layout else get_default_stamp_elements()
    if not elements:
        elements = get_default_stamp_elements()
    return layout, [dict(el) for el in elements]


@router.get("/admin/fonts", summary="جلب الخطوط العربية المتوفرة محلياً في المحرك")
def get_available_fonts_endpoint(_key: str = Depends(verify_internal_key)):
    return {"fonts": list_available_fonts()}


@router.post("/admin/materials/{material_id}/frame", summary="رفع ودمج إطار شفاف للصفحات على مذكرة معينة")
async def upload_material_frame(
    material_id: int,
    frame_image: UploadFile = File(...),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    frames_dir = os.path.join(settings.STORAGE_DIR, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    fr_name = f"frm_{material_id}_{secrets.token_hex(4)}.png"
    fr_abs = os.path.join(frames_dir, fr_name)

    raw_bytes = await frame_image.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="ملف الإطار فارغ")

    try:
        process_frame_image_to_transparent_png(raw_bytes, fr_abs)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"صورة الإطار غير صالحة: {e}")

    mat.page_frame_path = f"/storage/frames/{fr_name}"
    db.commit()
    db.refresh(mat)
    try:
        from app.routers.materials import invalidate_materials_cache
        invalidate_materials_cache()
    except Exception:
        pass
    logger.info(f"🖼️ [Admin Frame] تم تركيب إطار شفاف للمذكرة #{material_id}: {mat.page_frame_path}")
    return {"id": mat.id, "title": mat.title, "page_frame_path": mat.page_frame_path}


@router.delete("/admin/materials/{material_id}/frame", summary="إزالة إطار الصفحات عن المذكرة")
def remove_material_frame(
    material_id: int,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    mat.page_frame_path = None
    db.commit()
    return {"id": mat.id, "page_frame_path": None}


@router.get("/admin/materials/{material_id}/layout", summary="جلب إحداثيات الأختام والخطوط للموبايل والبوت")
def get_material_layout_studio(
    material_id: int,
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    layout, elements = _get_or_build_layout_elements(db, material_id)
    return {
        "material": {
            "id": mat.id,
            "title": mat.title,
            "tag": mat.tag,
            "total_pages": mat.total_pages or 1,
            "page_frame_path": mat.page_frame_path,
            "cover_image_path": mat.cover_image_path,
        },
        "layout_id": layout.id if layout else None,
        "layout_type": layout.layout_type.value if layout else LayoutType.DEFAULT_TEXT.value,
        "stamp_elements": elements,
        "fonts": list_available_fonts(),
    }


@router.put("/admin/materials/{material_id}/layout", summary="حفظ إحداثيات الأختام والخطوط من الموبايل أو البوت")
def upsert_material_layout_studio(
    material_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    raw_els = payload.get("stamp_elements") or []
    if not raw_els:
        raise HTTPException(status_code=400, detail="يجب إرسال عنصر ختم واحد على الأقل")

    raw_lt = str(payload.get("layout_type", "default_text")).strip().lower()
    lt = LayoutType.DEFAULT_LOGO if raw_lt == "default_logo" else LayoutType.DEFAULT_TEXT

    validated = [schemas.StampElement.model_validate(el) for el in raw_els]
    admin_user = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
    creator_id = admin_user.id if admin_user else 1

    saved = crud.create_layout(
        db=db,
        data=schemas.StampLayoutCreate(
            material_id=mat.id,
            layout_type=lt,
            stamp_elements=validated,
        ),
        creator_id=creator_id,
    )
    logger.info(f"🎨 [Mobile Studio] تم حفظ تنسيق أختام المذكرة #{mat.id} ({len(validated)} عناصر)")
    return {
        "status": "saved",
        "layout_id": saved.id,
        "material_id": mat.id,
        "layout_type": saved.layout_type.value,
        "stamp_elements": saved.get_elements(),
    }


@router.post("/admin/materials/{material_id}/quick-font-adjust", summary="تعديل سريع للخط والحجم والموضع من البوت")
def quick_adjust_material_fonts(
    material_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    layout, elements = _get_or_build_layout_elements(db, material_id)

    target_type = str(payload.get("target_type", "all")).strip().lower()
    font_family = payload.get("font_family")
    font_size = payload.get("font_size")
    font_size_delta = float(payload.get("font_size_delta", 0) or 0)
    dx_pct = float(payload.get("dx_pct", 0) or 0)
    dy_pct = float(payload.get("dy_pct", 0) or 0)
    color = payload.get("color")
    opacity = payload.get("opacity")

    updated_elements = []
    for el in elements:
        item = dict(el)
        el_type = str(item.get("type", "")).lower()
        matches = (target_type == "all") or (el_type == target_type)
        if matches:
            if font_family:
                item["font_family"] = str(font_family).strip().lower()
            if font_size is not None:
                item["font_size"] = max(4.0, min(180.0, float(font_size)))
            elif font_size_delta != 0:
                cur_sz = float(item.get("font_size", 14.0))
                item["font_size"] = max(4.0, min(180.0, round(cur_sz + font_size_delta, 1)))
            if dx_pct != 0:
                item["x_pct"] = round(max(-50.0, min(150.0, float(item.get("x_pct", 50.0)) + dx_pct)), 1)
            if dy_pct != 0:
                item["y_pct"] = round(max(-50.0, min(150.0, float(item.get("y_pct", 50.0)) + dy_pct)), 1)
            if color:
                item["color"] = str(color).strip()
            if opacity is not None:
                item["opacity"] = max(0.05, min(1.0, float(opacity)))
        updated_elements.append(item)

    validated = [schemas.StampElement.model_validate(el) for el in updated_elements]
    admin_user = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
    creator_id = admin_user.id if admin_user else 1
    lt = layout.layout_type if layout else LayoutType.DEFAULT_TEXT

    saved = crud.create_layout(
        db=db,
        data=schemas.StampLayoutCreate(
            material_id=mat.id,
            layout_type=lt,
            stamp_elements=validated,
        ),
        creator_id=creator_id,
    )
    return {
        "status": "updated",
        "material_id": mat.id,
        "layout_id": saved.id,
        "stamp_elements": saved.get_elements(),
    }


@router.get("/admin/materials/{material_id}/preview-page", summary="معاينة PNG خفيفة لصفحة واحدة فقط بالأختام")
async def preview_material_page_get(
    material_id: int,
    page: int = Query(1, ge=1),
    stamped: int = Query(1),
    zoom: float = Query(1.35, ge=0.5, le=2.5),
    name: str = Query("أحمد محمد علي"),
    phone: str = Query("01012345678"),
    series: str = Query("سلسلة التفوق"),
    watermark: str = Query("نسخة معاينة"),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    pdf_path = _resolve_material_path(mat.original_pdf_path)
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="ملف الـ PDF الأصلي غير موجود")

    _, elements = _get_or_build_layout_elements(db, material_id)
    png_bytes = await render_stamped_page_preview_png(
        material_path=pdf_path,
        stamp_layout=elements,
        page_num=page,
        buyer_name=name,
        buyer_phone=phone,
        doc_name_stamp=series,
        watermark_text=watermark,
        watermark_type="text",
        material_tag=mat.tag,
        page_frame_path=mat.page_frame_path,
        zoom=zoom,
        apply_stamps=bool(stamped),
    )
    return Response(content=png_bytes, media_type="image/png")


@router.post("/admin/materials/{material_id}/preview-page", summary="معاينة فورية لتنسيق غير محفوظ بعد")
async def preview_material_page_post(
    material_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    mat = _get_material_or_404(db, material_id)
    pdf_path = _resolve_material_path(mat.original_pdf_path)
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="ملف الـ PDF الأصلي غير موجود")

    _, db_elements = _get_or_build_layout_elements(db, material_id)
    elements = payload.get("stamp_elements") or db_elements
    page = int(payload.get("page", 1) or 1)
    zoom = float(payload.get("zoom", 1.35) or 1.35)
    apply_stamps = bool(payload.get("apply_stamps", True))

    png_bytes = await render_stamped_page_preview_png(
        material_path=pdf_path,
        stamp_layout=elements,
        page_num=page,
        buyer_name=str(payload.get("buyer_name") or "أحمد محمد علي"),
        buyer_phone=str(payload.get("buyer_phone") or "01012345678"),
        buyer_phone_2=payload.get("buyer_phone_2"),
        doc_name_stamp=str(payload.get("doc_name_stamp") or "سلسلة التفوق"),
        watermark_text=str(payload.get("watermark_text") or "نسخة معاينة"),
        watermark_type=str(payload.get("watermark_type") or "text"),
        material_tag=mat.tag,
        page_frame_path=payload.get("override_frame_path") or mat.page_frame_path,
        zoom=min(2.2, max(0.6, zoom)),
        apply_stamps=apply_stamps,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.post("/orders/direct-stamp-sync", summary="ختم متسلسل خفيف المعالج لملف واحد وإرجاع المسار فوراً للدفعات")
async def direct_stamp_sync_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _key: str = Depends(verify_internal_key),
):
    """
    نقطة نهاية مصممة خصيصاً لمعالجة طلبات البوت الدفعية (Excel / Word / VIP)
    بطريقة متتابعة (Sequential) بأقل استهلاك ممكن للمعالج والذاكرة.
    """
    material_id = int(payload.get("material_id"))
    mat = db.query(Material).filter(Material.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail=f"المذكرة #{material_id} غير موجودة")

    pdf_path = _resolve_material_path(mat.original_pdf_path)
    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail=f"ملف الـ PDF الأصلي للمذكرة '{mat.title}' غير موجود")

    buyer_name = str(payload.get("buyer_name_stamp") or "").strip()
    raw_phone1 = str(payload.get("buyer_phone_stamp") or "").strip()
    raw_phone2 = str(payload.get("buyer_phone_stamp_2") or "").strip() or None
    series_name = str(payload.get("series_name") or payload.get("doc_name_stamp") or "").strip() or None
    wm_text = str(payload.get("watermark_text") or "").strip() or None
    wm_type = str(payload.get("watermark_type") or ("text" if wm_text else "none")).strip().lower()

    tg_uid = payload.get("telegram_user_id")
    if tg_uid is not None and str(tg_uid).strip():
        is_admin_actor = stamp_settings.is_admin_telegram_id(tg_uid) and (payload.get("is_admin") is not False)
    else:
        is_admin_actor = payload.get("is_admin") is not False
    ok_stamp, err_stamp = stamp_settings.validate_stamp_fields(
        buyer_name_stamp=buyer_name,
        buyer_phone_stamp=raw_phone1,
        buyer_phone_stamp_2=raw_phone2,
        series_name=series_name,
        doc_name_stamp=payload.get("doc_name_stamp"),
        watermark_text=wm_text,
        is_admin=is_admin_actor,
    )
    if not ok_stamp:
        raise HTTPException(status_code=400, detail=err_stamp)

    phone1 = clean_phone_number(raw_phone1) if raw_phone1 else ""
    phone2 = clean_phone_number(raw_phone2) if raw_phone2 else None

    blacklist_msg = crud.check_stamp_data_against_blacklist(db, buyer_name, phone1, wm_text)
    if blacklist_msg:
        raise HTTPException(status_code=403, detail=blacklist_msg)

    _, raw_elements = _get_or_build_layout_elements(db, mat.id)
    override_font = payload.get("font_family")
    font_size_delta = float(payload.get("font_size_delta") or 0)
    explicit_font_size = payload.get("font_size")

    processed_elements = []
    for el in raw_elements:
        item = dict(el)
        if mat.tag and item.get("custom_text"):
            item["custom_text"] = item["custom_text"].replace("{tag}", mat.tag).replace("[tag]", mat.tag)
        if override_font:
            item["font_family"] = str(override_font).strip().lower()
        if explicit_font_size is not None and item.get("type") in ("name", "phone", "doc_name"):
            # الحفاظ على حجم خط وسام الغلاف (صفحة محددة page > 0) كما هو في الديفولت دون تقليصه بحجم النص العادي
            if not (item.get("type") == "doc_name" and int(item.get("page") or 0) > 0):
                item["font_size"] = max(4.0, min(80.0, float(explicit_font_size)))
        elif font_size_delta != 0:
            item["font_size"] = max(4.0, min(180.0, float(item.get("font_size", 14.0)) + font_size_delta))
        processed_elements.append(item)

    tg_chat = str(payload.get("telegram_chat_id") or "8287744705")
    tg_user = str(payload.get("telegram_user_id") or tg_chat)
    order_code = f"BAT-{secrets.token_hex(3).upper()}"

    bot_user = _get_or_create_bot_system_user(db)
    purchase = Purchase(
        buyer_id=bot_user.id,
        material_id=mat.id,
        buyer_name_stamp=buyer_name,
        buyer_phone_stamp=phone1,
        buyer_phone_stamp_2=phone2,
        doc_name_stamp=series_name,
        series_name=series_name,
        watermark_text=wm_text,
        watermark_type=wm_type,
        amount_paid=float(mat.price or 0.0),
        status=PurchaseStatus.PROCESSING,
        transaction_id=order_code,
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    order = BotOrder(
        order_code=order_code,
        telegram_chat_id=tg_chat,
        telegram_user_id=tg_user,
        user_id=bot_user.id,
        material_id=mat.id,
        purchase_id=purchase.id,
        buyer_name_stamp=buyer_name,
        buyer_phone_stamp=phone1,
        buyer_phone_stamp_2=phone2,
        doc_name_stamp=series_name,
        series_name=series_name,
        watermark_text=wm_text,
        watermark_type=wm_type,
        total_amount=float(mat.price or 0.0),
        payment_method="admin_batch",
        claimed_sender_phone=phone1,
        status=OrderFulfillmentStatus.PROCESSING_STAMP,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    raw_frame_ref = payload.get("override_frame_path") or mat.page_frame_path
    resolved_frame = _resolve_asset_path(raw_frame_ref, "frames")

    async with STAMP_SEMAPHORE:
        try:
            from app.stamper import OUTPUT_DIR as STAMPER_OUT_DIR
            final_target_path = f"{STAMPER_OUT_DIR}/{purchase.id}_final.pdf"
            mark_entries: list[dict] = []
            final_path = await asyncio.to_thread(
                _stamp_native,
                purchase.id,
                pdf_path,
                processed_elements,
                buyer_name,
                phone1,
                wm_text,
                series_name,
                mat.tag,
                resolved_frame,
                wm_type,
                None,
                phone2,
                True,  # fast_batch=True لتقليل ضغط المعالج
                final_target_path,
                bool(mat.compression_enabled),
                False,
                mark_entries,
            )
            if mark_entries:
                crud.record_hidden_marks_bulk(db, mark_entries)

            crud.update_purchase_status(db, purchase.id, PurchaseStatus.COMPLETED, output_path=final_path)
            order.stamped_pdf_path = final_path
            # نضبط الحالة على DELIVERED حتى لا يعيد RealTimeNotifier إرسال نفس الملف مرتين
            order.status = OrderFulfillmentStatus.DELIVERED
            order.completed_at = datetime.utcnow()
            db.commit()
            gc.collect()
            size_kb = os.path.getsize(final_path) // 1024 if os.path.exists(final_path) else 0
            logger.info(f"✅ [Batch Stamp] {order_code} جاهز ({size_kb}KB) للمدرس {buyer_name}")
            return {
                "status": "completed",
                "order_code": order_code,
                "purchase_id": purchase.id,
                "material_id": mat.id,
                "material_title": mat.title,
                "buyer_name": buyer_name,
                "buyer_phone": phone1,
                "total_pages": mat.total_pages or 1,
                "file_size_kb": size_kb,
                "download_url": f"/api/internal/orders/{order_code}/download",
            }
        except Exception as e:
            logger.error(f"❌ [Batch Stamp Error] {order_code}: {e}")
            crud.update_purchase_status(db, purchase.id, PurchaseStatus.FAILED)
            order.status = OrderFulfillmentStatus.FAILED
            order.error_message = str(e)
            db.commit()
            raise HTTPException(status_code=500, detail=f"فشل ختم المذكرة: {e}")



