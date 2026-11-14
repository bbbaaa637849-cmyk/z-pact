# ================================================================
# app/routers/ecosystem_v3.py — المحرك المتكامل للنسخة الثالثة (Z-PACT v3.0)
#
# يشمل:
#   1. التسجيل السريع المحكم بالسيريال وموافقة الإدارة للمدرسين (Zero-Random Access)
#   2. منظومة فريق المرسلين (التسجيل بالرقم فقط + قفل السيريال + إنشاء الطلبات + طابور المعلقات)
#   3. نظام العمولات والحسابات الدقيق لكل مرسل ولكل ملف ولكل عملية + التسويات المالية
#   4. سلة شراء عدة مذكرات معاً (Multi-PDF Bundle Cart) بإرفاق صورة التحويل أو الـ SMS الآلي أو الكوينز
#   5. نظام التحديث التلقائي الهوائي المباشر (In-App OTA Auto-Updater) للنسخ الثلاث (Admin / Teacher / Sender)
# ================================================================

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form,
    HTTPException, Query, Request, UploadFile, status
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.auth import create_access_token, decode_access_token
from app.config import settings, stamp_settings
from app.database import get_db
from app.formatting import clean_phone_number
from app.logger import logger
from app.models import (
    ActivationSerial, AdminSecurityEvent, AppReleaseConfig, BotOrder, Material,
    Notification, OrderFulfillmentStatus, OtpChallenge, PaymentMethod, PaymentSMSLog,
    Purchase, PurchaseStatus, SenderAgent, SenderLedgerEntry,
    TeacherProfile, TransactionType, User, UserDevice, UserRole, WalletTransaction
)
from app.routers.internal_bridge import (
    _get_or_create_bot_system_user,
    _trigger_order_stamping,
    internal_event_bus,
)

router = APIRouter(prefix="/v3", tags=["Z-PACT Enterprise V3"])
ota_router = APIRouter(prefix="/app-updates", tags=["OTA App Updates"])

ADMIN_WALLET_NUMBERS = {
    "01097671808",
    "+201097671808",
    "201097671808",
    "01032881402",
    "+201032881402",
    "201032881402",
    "zpact@instapay",
}


# ================================================================
# دوال المساعدة والمصادقة الهجينة (Admin JWT أو Internal Bridge Key)
# ================================================================

def normalize_eg_phone(raw_phone: str) -> str:
    """تطبيع أرقام الهواتف المصرية والأرقام العربية الشرقية بدقة"""
    if not raw_phone:
        return ""
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    s = str(raw_phone).translate(trans).strip()
    if "@" in s:
        return s.lower()
    digits = re.sub(r"\D+", "", s)
    if digits.startswith("0020") and len(digits) >= 14:
        digits = "0" + digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits or s


def make_serial_code(prefix: str, seed: str = "") -> str:
    raw = f"{prefix}:{seed}:{secrets.token_hex(4)}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest().upper()
    return f"ZP-{prefix}-{digest[:4]}-{digest[4:8]}"


def is_admin_wallet(target_wallet: Optional[str]) -> bool:
    if not target_wallet:
        return True
    norm = normalize_eg_phone(target_wallet)
    return norm in ADMIN_WALLET_NUMBERS or target_wallet.strip().lower() in ADMIN_WALLET_NUMBERS


def require_admin_or_internal(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """يقبل إما مفتاح الجسر الداخلي (للبوت وتطبيق الأدمن) أو توكن JWT الخاص بأدمن معتمد"""
    internal_key = request.headers.get("X-Internal-Service-Key") or request.query_params.get("key")
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    if internal_key and internal_key == expected_key:
        founding = db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
        return founding or db.query(User).filter(User.role == UserRole.ADMIN).first()

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        payload = decode_access_token(token)
        if payload:
            user = crud.get_user_by_id(db, int(payload.get("sub", 0)))
            if (
                user
                and user.is_active
                and user.role == UserRole.ADMIN
                and getattr(user, "approval_status", "approved") in (None, "approved")
            ):
                return user

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="غير مصرح — خاص بإدارة المنصة المعتمدة فقط")


def require_founding_admin_or_internal(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """حصري لأول أدمن (المؤسس السيادي) أو بوت الإدارة الرسمي فقط"""
    internal_key = request.headers.get("X-Internal-Service-Key") or request.query_params.get("key")
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    if internal_key and internal_key == expected_key:
        return db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        payload = decode_access_token(token)
        if payload:
            user = crud.get_user_by_id(db, int(payload.get("sub", 0)))
            if (
                user
                and user.is_active
                and user.role == UserRole.ADMIN
                and getattr(user, "approval_status", "approved") in (None, "approved")
                and bool(getattr(user, "is_founding_admin", False))
            ):
                return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="⛔ هذه الصلاحية السيادية حصرية لـ (أول أدمن / المؤسس) فقط!",
    )


def verify_admin_apk_exclusive(request: Request, device_id: str) -> None:
    """التحقق الصارم من أن الطلب قادم حصرياً من داخل تطبيق الإدارة الأصيل (Z-PACT-Admin-Radar.apk)"""
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    app_proof = (
        request.headers.get("X-Admin-App-Proof")
        or request.headers.get("X-Internal-Service-Key")
        or ""
    ).strip()
    clean_dev = (device_id or "").strip().upper()
    if app_proof != expected_key or not clean_dev.startswith("ZP-ADM-"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="⛔ عملية محمية: تهيئة كلمة سر الأدمن أو إنشاء حساب أدمن متاح حصرياً من داخل تطبيق الإدارة الرسمي (Z-PACT-Admin-Radar.apk) فقط!",
        )


def _record_and_alert_admin_login(
    db: Session,
    admin_user: User,
    device_id: str,
    ip_addr: Optional[str],
    is_native_admin_apk: bool = False,
) -> None:
    """تسجيل عملية دخول الأدمن وإرسال تنبيه فوري لأول أدمن (المؤسس) داخل اللوحة وعلى تليجرام"""
    label = admin_user.admin_label or admin_user.full_name or f"Admin #{admin_user.id}"
    is_founder = bool(getattr(admin_user, "is_founding_admin", False))
    surface = "تطبيق الأدمن الرسمي (APK)" if (is_native_admin_apk or str(device_id).startswith("ZP-ADM-")) else "متصفح الويب"

    ev = AdminSecurityEvent(
        admin_id=admin_user.id,
        event_type="ADMIN_LOGIN",
        admin_label=label,
        device_id=device_id,
        ip_address=ip_addr or "unknown",
        details=f"{'أول أدمن (المؤسس)' if is_founder else 'أدمن فرعي'} دخل عبر {surface}",
    )
    db.add(ev)

    founder = db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
    db.add(Notification(
        user_id=founder.id if founder else admin_user.id,
        title="🛡️ تنبيه أمني: تسجيل دخول بحساب أدمن",
        message=f"الحساب: {label} ({'أول أدمن' if is_founder else 'أدمن إضافي'}) | الجهاز: {device_id} | IP: {ip_addr or 'محلي'}",
    ))
    db.commit()

    internal_event_bus.fire_event({
        "event": "ADMIN_LOGIN_ALERT",
        "admin_id": admin_user.id,
        "admin_label": label,
        "is_founding_admin": is_founder,
        "device_id": device_id,
        "ip_address": ip_addr or "unknown",
        "surface": surface,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


def get_optional_jwt_user(request: Request, db: Session) -> Optional[User]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        payload = decode_access_token(token)
        if payload:
            u = crud.get_user_by_id(db, int(payload.get("sub", 0)))
            if u and u.is_active and getattr(u, "approval_status", "approved") in (None, "approved"):
                return u
    return None


def require_approved_sender(
    request: Request,
    db: Session = Depends(get_db),
) -> SenderAgent:
    """التحقق من توكن المرسل وسيريال جهازه وحالة موافقة الأدمن"""
    token = (
        request.headers.get("X-Sender-Token")
        or request.query_params.get("sender_token")
        or ""
    ).strip()
    device_id = (
        request.headers.get("X-Device-Serial")
        or request.query_params.get("device_id")
        or ""
    ).strip()

    if not token:
        raise HTTPException(status_code=401, detail="يرجى تسجيل الدخول برقم هاتفك أولاً")

    sender = db.query(SenderAgent).filter(SenderAgent.auth_token == token).first()
    if not sender:
        raise HTTPException(status_code=401, detail="جلسة المرسل غير صالحة — أعد إدخال رقم هاتفك")

    if sender.status == "pending":
        raise HTTPException(status_code=403, detail="⏳ حسابك قيد انتظار موافقة الإدارة")
    if sender.status != "approved":
        raise HTTPException(status_code=403, detail="⛔ تم إيقاف أو رفض صلاحية هذا الحساب من الإدارة")

    if sender.device_id and device_id and sender.device_id != device_id:
        raise HTTPException(
            status_code=403,
            detail="⛔ سيريال الجهاز غير مطابق للسيريال المعتمد لهذا الحساب! تواصل مع الإدارة لإعادة ضبط السيريال.",
        )

    if not sender.device_id and device_id:
        sender.device_id = device_id
    sender.last_seen_at = datetime.utcnow()
    db.commit()
    return sender


def calculate_sender_commission_for_material(sender: SenderAgent, material: Material) -> float:
    """
    حساب عمولة المرسل الدقيقة لملف معين وفق الأولوية:
      1. عمولة مخصصة لهذا المرسل تحديداً على هذا الملف (custom_material_commissions)
      2. عمولة مخصصة للملف نفسه على مستوى المنصة (Material.sender_commission)
      3. العمولة الافتراضية للمرسل لكل مذكرة (commission_per_pdf) + النسبة المئوية (commission_percent)
    """
    custom_map = sender.custom_material_commissions or {}
    if isinstance(custom_map, str):
        try:
            custom_map = json.loads(custom_map)
        except Exception:
            custom_map = {}

    str_id = str(material.id)
    if str_id in custom_map and custom_map[str_id] is not None:
        return round(max(0.0, float(custom_map[str_id])), 2)

    if material.sender_commission is not None:
        return round(max(0.0, float(material.sender_commission)), 2)

    price_val = float(material.price or material.price_coins or 0.0)
    flat_comm = float(sender.commission_per_pdf or 0.0)
    pct_comm = price_val * (float(sender.commission_percent or 0.0) / 100.0)
    return round(max(0.0, flat_comm + pct_comm), 2)


def serialize_sender(s: SenderAgent) -> Dict[str, Any]:
    custom_map = s.custom_material_commissions or {}
    if isinstance(custom_map, str):
        try:
            custom_map = json.loads(custom_map)
        except Exception:
            custom_map = {}
    return {
        "id": s.id,
        "phone": s.phone,
        "display_name": s.display_name or f"مرسل ({s.phone})",
        "serial_code": s.serial_code,
        "device_id": s.device_id,
        "device_bound": bool(s.device_id),
        "phone_verified": bool(getattr(s, "phone_verified", False)),
        "status": s.status,
        "wallet_number": s.wallet_number or s.phone,
        "commission_per_pdf": float(s.commission_per_pdf or 0.0),
        "commission_percent": float(s.commission_percent or 0.0),
        "custom_material_commissions": custom_map,
        "total_pdfs_count": int(s.total_pdfs_count or 0),
        "total_orders_count": int(s.total_orders_count or 0),
        "total_collected_by_sender": round(float(s.total_collected_by_sender or 0.0), 2),
        "total_collected_by_admin": round(float(s.total_collected_by_admin or 0.0), 2),
        "total_commission_earned": round(float(s.total_commission_earned or 0.0), 2),
        "net_due_to_admin": round(float(s.net_due_to_admin or 0.0), 2),
        "settled_amount": round(float(s.settled_amount or 0.0), 2),
        "admin_notes": s.admin_notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "approved_at": s.approved_at.isoformat() if s.approved_at else None,
        "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
    }


def save_uploaded_receipt(upload: Optional[UploadFile]) -> Optional[str]:
    if not upload or not upload.filename:
        return None
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    receipts_dir = os.path.join(settings.STORAGE_DIR, "receipts")
    os.makedirs(receipts_dir, exist_ok=True)
    fname = f"rcpt_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{ext}"
    fpath = os.path.join(receipts_dir, fname)
    with open(fpath, "wb") as f:
        f.write(upload.file.read())
    return f"/storage/receipts/{fname}"


def save_uploaded_watermark_logo(upload: Optional[UploadFile]) -> Optional[str]:
    if not upload or not upload.filename:
        return None
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        ext = ".png"
    wm_dir = os.path.join(settings.STORAGE_DIR, "watermarks")
    os.makedirs(wm_dir, exist_ok=True)
    fname = f"wm_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{ext}"
    fpath = os.path.join(wm_dir, fname)
    with open(fpath, "wb") as f:
        f.write(upload.file.read())
    return f"/storage/watermarks/{fname}"


# ================================================================
# 0. بوابة القيادة السيادية للأدمن (كلمة سر فقط + أول أدمن + موافقة المشرفين)
# ================================================================

BOT_PUBLIC_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "veen637bot").lstrip("@")


@router.get("/admin-vault/status", summary="فحص حالة تهيئة كلمة سر الأدمن المؤسس وحالة الجهاز")
def get_admin_vault_status(
    device_id: str = Query(""),
    db: Session = Depends(get_db),
):
    founding = db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
    approved_admins_count = (
        db.query(User)
        .filter(User.role == UserRole.ADMIN, User.is_active == True, User.approval_status == "approved")
        .count()
    )
    clean_dev = (device_id or "").strip()
    pending_co = None
    if clean_dev:
        co = (
            db.query(User)
            .filter(
                User.role == UserRole.ADMIN,
                User.serial_code == clean_dev,
                User.is_founding_admin == False,
            )
            .order_by(User.id.desc())
            .first()
        )
        if co and co.approval_status == "pending":
            pending_co = {
                "user_id": co.id,
                "admin_label": co.admin_label or co.full_name,
                "serial_code": co.serial_code,
                "approval_status": co.approval_status,
            }

    return {
        "has_founding_admin": bool(founding),
        "founding_label": (founding.admin_label or founding.full_name) if founding else None,
        "is_admin_apk_serial": clean_dev.upper().startswith("ZP-ADM-"),
        "pending_co_admin": pending_co,
        "approved_admins_count": approved_admins_count,
    }


@router.post("/admin-vault/setup-founding", summary="تعيين كلمة السر الرئيسية لأول أدمن (المؤسس) حصرياً من تطبيق الأدمن")
def setup_founding_admin_password(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    password = str(payload.get("password", "")).strip()
    admin_label = str(payload.get("admin_label", "")).strip() or "الأدمن المؤسس (أول أدمن)"
    device_id = str(payload.get("device_id", "")).strip()

    # تحقق صارم: حصري لتطبيق الأدمن الرسمي فقط!
    verify_admin_apk_exclusive(request, device_id)

    if len(password) < 6:
        raise HTTPException(status_code=400, detail="⚠️ يجب أن لا تقل كلمة سر الأدمن عن 6 أحرف أو أرقام")

    existing_founder = db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
    if existing_founder:
        raise HTTPException(
            status_code=409,
            detail="⚠️ تم تعيين كلمة سر (أول أدمن / المؤسس) بالفعل! سجّل دخولك بكلمة السر أو اطلب حساب أدمن إضافي.",
        )

    # ترقية حساب الأدمن الأولي الافتراضي إلى حساب (أول أدمن المؤسس)
    target_admin = db.query(User).filter(User.role == UserRole.ADMIN).order_by(User.id.asc()).first()
    if target_admin:
        target_admin.full_name = admin_label
        target_admin.admin_label = admin_label
        target_admin.password_hash = crud.hash_password(password)
        target_admin.is_founding_admin = True
        target_admin.has_custom_password = True
        target_admin.approval_status = "approved"
        target_admin.is_active = True
        target_admin.serial_code = device_id
    else:
        target_admin = User(
            full_name=admin_label,
            admin_label=admin_label,
            email="founder@admins.zpact.live",
            password_hash=crud.hash_password(password),
            role=UserRole.ADMIN,
            is_active=True,
            is_founding_admin=True,
            has_custom_password=True,
            approval_status="approved",
            serial_code=device_id,
        )
        db.add(target_admin)

    db.commit()
    db.refresh(target_admin)

    ip_addr = request.client.host if request.client else "unknown"
    crud.register_user_device(
        db=db,
        user_id=target_admin.id,
        device_id=device_id,
        user_agent=request.headers.get("User-Agent"),
        ip=ip_addr,
    )

    db.add(AdminSecurityEvent(
        admin_id=target_admin.id,
        event_type="FOUNDING_SETUP",
        admin_label=admin_label,
        device_id=device_id,
        ip_address=ip_addr,
        details="تم تأسيس وتعيين كلمة السر الرئيسية لأول أدمن من تطبيق الإدارة الرسمي",
    ))
    db.commit()

    internal_event_bus.fire_event({
        "event": "FOUNDING_ADMIN_INITIALIZED",
        "admin_id": target_admin.id,
        "admin_label": admin_label,
        "device_id": device_id,
        "ip_address": ip_addr,
    })

    token = create_access_token(user_id=target_admin.id, role=target_admin.role.value)
    return {
        "status": "approved",
        "access_token": token,
        "token_type": "bearer",
        "user": schemas.UserOut.model_validate(target_admin).model_dump(mode="json"),
        "message": "👑 تم تعيين كلمة السر السيادية لأول أدمن بنجاح!",
    }


@router.post("/admin-vault/login", summary="دخول الأدمن بكلمة السر فقط (بدون إيميل أو هاتف) مع تنبيه أمني فوري لأول أدمن")
def admin_password_only_login(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    password = str(payload.get("password", ""))
    device_id = str(payload.get("device_id", "")).strip() or f"ZP-WEB-{secrets.token_hex(4).upper()}"
    ip_addr = request.client.host if request.client else "unknown"

    if not password:
        raise HTTPException(status_code=400, detail="أدخل كلمة سر الأدمن")

    founding = db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
    if not founding:
        raise HTTPException(
            status_code=403,
            detail="👑 لم يتم تعيين كلمة سر (أول أدمن) بعد! افتح تطبيق الإدارة الرسمي (Z-PACT-Admin-Radar.apk) لتعيين كلمة السر الرئيسية أولاً.",
        )

    admin_accounts = db.query(User).filter(User.role == UserRole.ADMIN).order_by(User.is_founding_admin.desc(), User.id.asc()).all()
    matched_admin: Optional[User] = None
    for adm in admin_accounts:
        if crud.verify_password(password, adm.password_hash):
            matched_admin = adm
            break

    if not matched_admin:
        raise HTTPException(status_code=401, detail="⛔ كلمة سر الأدمن غير صحيحة")

    if getattr(matched_admin, "approval_status", "approved") == "pending":
        raise HTTPException(
            status_code=403,
            detail="⏳ حساب الأدمن الإضافي بانتظار موافقة (أول أدمن / المؤسس). لا يمكن الدخول قبل اعتماده.",
        )

    if not matched_admin.is_active or getattr(matched_admin, "approval_status", "approved") != "approved":
        raise HTTPException(status_code=403, detail="⛔ تم رفض أو إيقاف صلاحية حساب الأدمن هذا من قبل أول أدمن.")

    # تسجيل / تحديث الجهاز
    devices = crud.get_user_devices(db, matched_admin.id)
    existing_dev = next((d for d in devices if d.device_id == device_id), None)
    if existing_dev:
        if existing_dev.is_blocked:
            raise HTTPException(status_code=403, detail="⛔ هذا الجهاز محظور أمنياً من قبل أول أدمن.")
        existing_dev.last_login = datetime.utcnow()
        existing_dev.last_ip = ip_addr
        db.commit()
    else:
        crud.register_user_device(
            db=db,
            user_id=matched_admin.id,
            device_id=device_id,
            user_agent=request.headers.get("User-Agent"),
            ip=ip_addr,
        )

    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    is_native_apk = (request.headers.get("X-Admin-App-Proof") == expected_key) and device_id.upper().startswith("ZP-ADM-")

    # تنبيه فوري لأول أدمن عند كل دخول لأي أدمن!
    _record_and_alert_admin_login(
        db=db,
        admin_user=matched_admin,
        device_id=device_id,
        ip_addr=ip_addr,
        is_native_admin_apk=is_native_apk,
    )

    token = create_access_token(user_id=matched_admin.id, role=matched_admin.role.value)
    return {
        "status": "approved",
        "access_token": token,
        "token_type": "bearer",
        "is_founding_admin": bool(matched_admin.is_founding_admin),
        "user": schemas.UserOut.model_validate(matched_admin).model_dump(mode="json"),
    }


@router.post("/admin-vault/request-co-admin", summary="طلب إنشاء حساب أدمن جديد من تطبيق الأدمن (يتطلب موافقة أول أدمن إلزامياً)")
def request_new_co_admin_account(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    admin_label = str(payload.get("admin_label", "")).strip()
    password = str(payload.get("password", "")).strip()
    device_id = str(payload.get("device_id", "")).strip()
    ip_addr = request.client.host if request.client else "unknown"

    # حصرياً من داخل تطبيق الأدمن الرسمي!
    verify_admin_apk_exclusive(request, device_id)

    founding = db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
    if not founding:
        raise HTTPException(
            status_code=400,
            detail="لم يتم تعيين أول أدمن بعد — قم بتعيين كلمة سر أول أدمن مباشرة أولاً.",
        )

    if len(admin_label) < 2:
        raise HTTPException(status_code=400, detail="أدخل اسماً توضيحياً للمشرف الجديد (حرفين على الأقل)")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="يجب أن لا تقل كلمة سر المشرف الجديد عن 6 أحرف أو أرقام")

    # منع تشابه كلمة سر المشرف الفرعي مع أي مشرف آخر لتمييز هوية كل أدمن عند الدخول بكلمة السر فقط
    existing_admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
    for adm in existing_admins:
        if adm.serial_code != device_id and crud.verify_password(password, adm.password_hash):
            raise HTTPException(
                status_code=400,
                detail="⚠️ اختر كلمة سر مختلفة ومميزة لهذا الحساب لتمييزه أمنياً.",
            )

    existing_for_dev = (
        db.query(User)
        .filter(User.role == UserRole.ADMIN, User.serial_code == device_id, User.is_founding_admin == False)
        .first()
    )
    if existing_for_dev:
        existing_for_dev.full_name = admin_label
        existing_for_dev.admin_label = admin_label
        existing_for_dev.password_hash = crud.hash_password(password)
        existing_for_dev.approval_status = "pending"
        existing_for_dev.is_active = True
        co_admin = existing_for_dev
    else:
        co_admin = User(
            full_name=admin_label,
            admin_label=admin_label,
            email=f"coadmin_{secrets.token_hex(4)}@admins.zpact.live",
            password_hash=crud.hash_password(password),
            role=UserRole.ADMIN,
            is_active=True,
            is_founding_admin=False,
            has_custom_password=True,
            approval_status="pending",
            serial_code=device_id,
        )
        db.add(co_admin)

    db.commit()
    db.refresh(co_admin)

    db.add(AdminSecurityEvent(
        admin_id=co_admin.id,
        event_type="CO_ADMIN_REQUEST",
        admin_label=admin_label,
        device_id=device_id,
        ip_address=ip_addr,
        details=f"طلب إنشاء حساب أدمن إضافي باسم ({admin_label}) — بانتظار موافقة أول أدمن",
    ))
    db.add(Notification(
        user_id=founding.id,
        title="🚨 طلب إنشاء حساب أدمن جديد (يحتاج موافقتك كأول أدمن)",
        message=f"الاسم: {admin_label} | سيريال الهاتف: {device_id} | IP: {ip_addr}",
    ))
    db.commit()

    internal_event_bus.fire_event({
        "event": "CO_ADMIN_APPROVAL_REQUEST",
        "user_id": co_admin.id,
        "admin_label": admin_label,
        "device_id": device_id,
        "ip_address": ip_addr,
    })

    return {
        "status": "pending_founding_approval",
        "user_id": co_admin.id,
        "admin_label": admin_label,
        "serial_code": device_id,
        "message": "⏳ تم إرسال طلب إنشاء حساب الأدمن إلى (أول أدمن / المؤسس)! لن يُفعل الحساب إلا بعد موافقته الصريحة.",
    }


@router.get("/admin-vault/poll-co-admin", summary="فحص لحظي لموافقة أول أدمن على حساب الأدمن الإضافي")
def poll_co_admin_approval(
    request: Request,
    user_id: int = Query(...),
    device_id: str = Query(...),
    db: Session = Depends(get_db),
):
    verify_admin_apk_exclusive(request, device_id)
    co = db.query(User).filter(User.id == user_id, User.role == UserRole.ADMIN).first()
    if not co or co.serial_code != device_id:
        return {"status": "not_found"}

    if co.approval_status == "approved" and co.is_active:
        ip_addr = request.client.host if request.client else "unknown"
        devices = crud.get_user_devices(db, co.id)
        if not any(d.device_id == device_id for d in devices):
            crud.register_user_device(db, co.id, device_id, request.headers.get("User-Agent"), ip_addr)
        _record_and_alert_admin_login(db, co, device_id, ip_addr, is_native_admin_apk=True)
        token = create_access_token(user_id=co.id, role=co.role.value)
        return {
            "status": "approved",
            "access_token": token,
            "user": schemas.UserOut.model_validate(co).model_dump(mode="json"),
        }

    return {
        "status": co.approval_status if co.is_active else "rejected",
        "admin_label": co.admin_label or co.full_name,
    }


@router.post("/admin-vault/co-admins/{user_id}/action", summary="موافقة أو رفض أو سحب صلاحية أدمن فرعي (حصري لأول أدمن فقط)")
def founding_admin_manage_co_admin(
    user_id: int,
    payload: Dict[str, Any],
    request: Request,
    founder: Optional[User] = Depends(require_founding_admin_or_internal),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id, User.role == UserRole.ADMIN).first()
    if not target:
        raise HTTPException(status_code=404, detail="حساب الأدمن المطلوب غير موجود")
    if target.is_founding_admin:
        raise HTTPException(status_code=400, detail="لا يمكن إيقاف أو تعديل صلاحية (أول أدمن / المؤسس)!")

    action = str(payload.get("action", "approve")).strip().lower()
    ip_addr = request.client.host if request.client else "unknown"

    if action == "approve":
        target.approval_status = "approved"
        target.is_active = True
        ev_type = "CO_ADMIN_APPROVED"
        details = f"تمت الموافقة من أول أدمن على تفعيل حساب ({target.admin_label or target.full_name})"
    elif action in ("reject", "revoke", "suspend"):
        target.approval_status = "rejected"
        target.is_active = False
        ev_type = "CO_ADMIN_REJECTED"
        details = f"تم رفض/سحب صلاحية حساب الأدمن ({target.admin_label or target.full_name}) بواسطة أول أدمن"
    else:
        raise HTTPException(status_code=400, detail="إجراء غير صالح")

    db.add(AdminSecurityEvent(
        admin_id=target.id,
        event_type=ev_type,
        admin_label=target.admin_label or target.full_name,
        device_id=target.serial_code,
        ip_address=ip_addr,
        details=details,
    ))
    db.commit()
    db.refresh(target)

    return {
        "status": "ok",
        "user_id": target.id,
        "admin_label": target.admin_label or target.full_name,
        "approval_status": target.approval_status,
        "is_active": target.is_active,
    }


@router.post("/admin-vault/change-master-password", summary="تغيير كلمة سر أول أدمن (حصري لأول أدمن ومن تطبيق الأدمن فقط)")
def change_founding_master_password(
    request: Request,
    payload: Dict[str, Any],
    founder: Optional[User] = Depends(require_founding_admin_or_internal),
    db: Session = Depends(get_db),
):
    device_id = str(payload.get("device_id", "")).strip()
    new_password = str(payload.get("new_password", "")).strip()
    verify_admin_apk_exclusive(request, device_id)

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="كلمة السر الجديدة يجب أن لا تقل عن 6 أحرف أو أرقام")

    target = founder or db.query(User).filter(User.role == UserRole.ADMIN, User.is_founding_admin == True).first()
    if not target:
        raise HTTPException(status_code=404, detail="حساب أول أدمن غير موجود")

    target.password_hash = crud.hash_password(new_password)
    db.add(AdminSecurityEvent(
        admin_id=target.id,
        event_type="MASTER_PASSWORD_CHANGED",
        admin_label=target.admin_label or target.full_name,
        device_id=device_id,
        ip_address=request.client.host if request.client else "unknown",
        details="قام أول أدمن بتحديث كلمة السر الرئيسية من تطبيق الإدارة الرسمي",
    ))
    db.commit()
    return {"status": "ok", "message": "✅ تم تحديث كلمة السر السيادية لأول أدمن بنجاح!"}


# ================================================================
# 0.5 محرك رموز التحقق (OTP 6-Digits) الموحد للمدرسين والمرسلين
# ================================================================

def _mask_phone(phone: str) -> str:
    if len(phone) >= 10:
        return f"{phone[:3]}****{phone[-4:]}"
    return phone


def _issue_otp_challenge(
    db: Session,
    phone: str,
    target_role: str,
    purpose: str,
) -> Dict[str, Any]:
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    challenge_token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    # إبطال أي تحديات قديمة غير مستخدمة لنفس الرقم والغرض
    db.query(OtpChallenge).filter(
        OtpChallenge.phone == phone,
        OtpChallenge.target_role == target_role,
        OtpChallenge.is_verified == False,
    ).update({"sms_dispatch_status": "superseded", "otp_plain_outbox": None})

    # تجزئة سريعة فائقة الأداء HMAC-SHA256 تمنع تعليق أو بطء التطبيق وتؤمن الرمز بالكامل
    salt = challenge_token[:8]
    fast_otp_hash = f"sha256${salt}${hashlib.sha256(f'{salt}:{otp_code}'.encode()).hexdigest()}"

    ch = OtpChallenge(
        challenge_token=challenge_token,
        phone=phone,
        target_role=target_role,
        purpose=purpose,
        otp_code_hash=fast_otp_hash,
        otp_plain_outbox=otp_code,
        sms_dispatch_status="queued",
        telegram_delivered=False,
        is_verified=False,
        attempts=0,
        expires_at=expires_at,
    )
    db.add(ch)

    # فحص هل الرقم مربوط بتليجرام لإرسال الكود تلقائياً في التو واللحظة أيضاً!
    linked_tg_id: Optional[str] = None
    if target_role == "sender":
        snd = db.query(SenderAgent).filter(SenderAgent.phone == phone).first()
        if snd and snd.telegram_user_id and str(snd.telegram_user_id).isdigit():
            linked_tg_id = str(snd.telegram_user_id)
    else:
        tp = db.query(TeacherProfile).filter(TeacherProfile.primary_phone == phone).first()
        if tp and tp.telegram_user_id and str(tp.telegram_user_id).isdigit():
            linked_tg_id = str(tp.telegram_user_id)

    if linked_tg_id:
        ch.telegram_delivered = True

    db.commit()
    db.refresh(ch)

    if linked_tg_id:
        internal_event_bus.fire_event({
            "event": "OTP_TELEGRAM_PUSH",
            "telegram_user_id": linked_tg_id,
            "otp_code": otp_code,
            "phone": phone,
            "target_role": target_role,
            "purpose": purpose,
        })

    tg_deep_link = f"https://t.me/{BOT_PUBLIC_USERNAME}?start=otp_{challenge_token}"
    return {
        "status": "otp_sent",
        "challenge_token": challenge_token,
        "phone": phone,
        "masked_phone": _mask_phone(phone),
        "expires_in_seconds": 600,
        "sms_queued": True,
        "auto_sent_telegram": bool(linked_tg_id),
        "telegram_otp_url": tg_deep_link,
        "message": (
            "📲 تم إرسال كود الـ OTP (6 أرقام) تلقائياً إلى تليجرام ورسائل الـ SMS!"
            if linked_tg_id
            else "📲 تم إصدار كود الـ OTP (6 أرقام) لرقمك! سيصلك برسالة SMS أو اضغط زر تليجرام بالأسفل لاستلامه في ثانية واحدة."
        ),
    }


def _verify_and_consume_otp(
    db: Session,
    phone: str,
    target_role: str,
    challenge_token: str,
    otp_code: str,
) -> OtpChallenge:
    clean_tok = (challenge_token or "").strip()
    clean_code = re.sub(r"\D+", "", str(otp_code or "").strip())

    if not clean_tok or len(clean_code) != 6:
        raise HTTPException(status_code=400, detail="⚠️ أدخل رمز التحقق (OTP) المكون من 6 أرقام كاملاً")

    ch = db.query(OtpChallenge).filter(OtpChallenge.challenge_token == clean_tok).first()
    if not ch or ch.phone != phone or ch.target_role != target_role:
        raise HTTPException(status_code=400, detail="⚠️ جلسة التحقق غير صالحة — اطلب كود OTP جديد")

    if ch.is_verified:
        raise HTTPException(status_code=400, detail="⚠️ تم استخدام رمز التحقق هذا مسبقاً — اطلب رمزاً جديداً")

    if ch.expires_at <= datetime.utcnow():
        ch.otp_plain_outbox = None
        db.commit()
        raise HTTPException(status_code=400, detail="⌛ انتهت صلاحية رمز الـ OTP — اضغط إعادة إرسال الكود")

    if ch.attempts >= 5:
        ch.otp_plain_outbox = None
        db.commit()
        raise HTTPException(status_code=429, detail="⛔ تجاوزت الحد الأقصى لمحاولات إدخال الكود — اطلب كود OTP جديد")

    stored_hash = ch.otp_code_hash or ""
    is_valid = False
    if stored_hash.startswith("sha256$"):
        parts = stored_hash.split("$", 2)
        if len(parts) == 3:
            salt, expected_digest = parts[1], parts[2]
            computed = hashlib.sha256(f"{salt}:{clean_code}".encode()).hexdigest()
            is_valid = hmac.compare_digest(computed, expected_digest)
    else:
        is_valid = crud.verify_password(clean_code, stored_hash)

    if not is_valid:
        ch.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="⚠️ رمز التحقق (OTP) غير صحيح — تأكد من الـ 6 أرقام")

    ch.is_verified = True
    ch.otp_plain_outbox = None
    if ch.sms_dispatch_status == "queued":
        ch.sms_dispatch_status = "consumed"
    db.commit()
    return ch


@router.post("/otp/request", summary="طلب إرسال رمز تحقق (OTP 6-Digits) لرقم الهاتف للمدرسين والمرسلين")
def request_phone_otp(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    raw_phone = str(payload.get("phone", "")).strip()
    target_role = str(payload.get("target_role", "teacher")).strip().lower()
    purpose = str(payload.get("purpose", "register")).strip().lower()

    if target_role not in ("teacher", "sender"):
        raise HTTPException(status_code=400, detail="نوع الحساب غير صالح")

    phone = normalize_eg_phone(raw_phone)
    if not re.match(r"^01[0125][0-9]{8}$", phone):
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم موبايل مصري صحيح مكون من 11 رقماً (مثال: 01012345678)")

    if crud.is_blacklisted(db, phone):
        raise HTTPException(status_code=403, detail="⛔ هذا الرقم محظور أمنياً. تواصل مع الإدارة.")

    if target_role == "teacher":
        synthetic_email = f"{phone}@teachers.zpact.live"
        existing = db.query(User).filter((User.phone == phone) | (User.email == synthetic_email)).first()
        if purpose == "register" and existing and getattr(existing, "has_custom_password", False) and getattr(existing, "phone_verified", False):
            raise HTTPException(status_code=400, detail="📱 هذا الرقم مسجل بالفعل! انتقل إلى (تسجيل الدخول) وأدخل كلمة السر الخاصة بك.")
        if purpose == "reset_password" and not existing:
            raise HTTPException(status_code=404, detail="هذا الرقم غير مسجل بعد — أنشئ حساباً جديداً أولاً.")
    else:
        existing_snd = db.query(SenderAgent).filter(SenderAgent.phone == phone).first()
        if purpose == "register" and existing_snd and existing_snd.password_hash and existing_snd.phone_verified:
            raise HTTPException(status_code=400, detail="📱 رقم المرسل هذا مسجل بالفعل! انتقل إلى (دخول المرسلين) وأدخل كلمة السر.")
        if purpose == "reset_password" and not existing_snd:
            raise HTTPException(status_code=404, detail="رقم الهاتف غير مسجل في فريق المرسلين.")

    return _issue_otp_challenge(db=db, phone=phone, target_role=target_role, purpose=purpose)


@router.get("/otp/poll-delivery", summary="متابعة حالة وصول رسالة الـ OTP (SMS أو تليجرام)")
def poll_otp_delivery_status(
    challenge_token: str = Query(...),
    db: Session = Depends(get_db),
):
    ch = db.query(OtpChallenge).filter(OtpChallenge.challenge_token == challenge_token.strip()).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return {
        "sms_dispatch_status": ch.sms_dispatch_status,
        "telegram_delivered": bool(ch.telegram_delivered),
        "is_verified": bool(ch.is_verified),
        "is_expired": ch.expires_at <= datetime.utcnow(),
    }


# ================================================================
# 1. منظومة تسجيل ودخول المدرسين المحمية (Phone + Password + OTP + Serial)
# ================================================================

@router.post("/auth/teacher-quick", summary="التسجيل السريع للمدرس برقم الهاتف وقفل السيريال (متوافق مع الإصدارات السابقة)")
def teacher_quick_access(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    raw_phone = str(payload.get("phone", "")).strip()
    full_name = str(payload.get("full_name", "")).strip()
    pin_code = str(payload.get("pin", "")).strip()
    vip_serial_input = str(payload.get("serial_code", "")).strip().upper()
    device_id = str(payload.get("device_id", "")).strip() or f"dev_{secrets.token_hex(8)}"

    phone = normalize_eg_phone(raw_phone)
    if len(phone) < 10 or not phone.isdigit():
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف مصري صحيح مكون من 11 رقماً (مثال: 01012345678)")

    if crud.is_blacklisted(db, phone):
        raise HTTPException(status_code=403, detail="⛔ هذا الرقم محظور من دخول المنصة. تواصل مع الإدارة.")

    synthetic_email = f"{phone}@teachers.zpact.live"
    user = db.query(User).filter(
        (User.phone == phone) | (User.email == synthetic_email)
    ).first()

    valid_serial_obj: Optional[ActivationSerial] = None
    if vip_serial_input:
        valid_serial_obj = db.query(ActivationSerial).filter(
            ActivationSerial.serial_code == vip_serial_input,
            ActivationSerial.is_active == True,
        ).first()
        if not valid_serial_obj or valid_serial_obj.used_count >= valid_serial_obj.max_uses:
            raise HTTPException(status_code=400, detail="⚠️ كود السيريال المدخل غير صحيح أو تم استنفاده مسبقاً")

    if user:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="⛔ تم إيقاف هذا الحساب من قبل الإدارة")

        if not user.serial_code:
            user.serial_code = make_serial_code("TCH", phone)
            db.commit()

        if getattr(user, "approval_status", "approved") != "approved":
            if valid_serial_obj:
                user.approval_status = "approved"
                user.phone_verified = True
                valid_serial_obj.used_count += 1
                if valid_serial_obj.used_count >= valid_serial_obj.max_uses:
                    valid_serial_obj.is_active = False
                if float(valid_serial_obj.bonus_coins or 0) > 0:
                    crud.update_user_coins(
                        db, user.id, float(valid_serial_obj.bonus_coins),
                        TransactionType.RECHARGE, f"هدية تفعيل سيريال {valid_serial_obj.serial_code}"
                    )
                db.commit()
                db.refresh(user)
            else:
                return {
                    "status": "pending_approval",
                    "user_id": user.id,
                    "serial_code": user.serial_code,
                    "phone": phone,
                    "full_name": user.full_name,
                    "message": "⏳ طلب تفعيل حسابك وصل للإدارة بالفعل! سيتم فتح المتجر تلقائياً فور موافقة المسؤول.",
                }

        devices = crud.get_user_devices(db, user.id)
        matching_dev = next((d for d in devices if d.device_id == device_id), None)

        if matching_dev:
            if matching_dev.is_blocked:
                raise HTTPException(status_code=403, detail="⛔ هذا الجهاز محظور أمنياً. راجع الإدارة.")
            matching_dev.last_login = datetime.utcnow()
            matching_dev.last_ip = request.client.host if request.client else None
            db.commit()
        else:
            if user.pin_code_hash:
                if not pin_code:
                    return {
                        "status": "require_pin",
                        "phone": phone,
                        "serial_code": user.serial_code,
                        "message": "🔒 تم التعرف على حسابك! أدخل الـ PIN السري المكون من 4 أرقام لربط جهازك الجديد.",
                    }
                if not crud.verify_password(pin_code, user.pin_code_hash):
                    raise HTTPException(status_code=401, detail="❌ رمز الـ PIN غير صحيح")

            limit = 5 if user.role == UserRole.ADMIN else 2
            if len(devices) >= limit:
                raise HTTPException(
                    status_code=403,
                    detail=f"🔒 هذا الحساب مربوط بسيريال أجهزتك المعتمدة ({limit} أجهزة كحد أقصى). اطلب من الإدارة إعادة ضبط السيريال.",
                )

            crud.register_user_device(
                db=db,
                user_id=user.id,
                device_id=device_id,
                user_agent=request.headers.get("User-Agent"),
                ip=request.client.host if request.client else None,
            )

        token = create_access_token(user_id=user.id, role=user.role.value)
        return {
            "status": "approved",
            "access_token": token,
            "token_type": "bearer",
            "user": schemas.UserOut.model_validate(user).model_dump(mode="json"),
        }

    serial_code = make_serial_code("TCH", phone)
    approval_state = "approved" if valid_serial_obj else "pending_approval"
    safe_pin = pin_code if len(pin_code) >= 4 else phone[-4:]

    new_user = User(
        full_name=full_name or f"مدرس {phone[-4:]}",
        email=synthetic_email,
        phone=phone,
        password_hash=crud.hash_password(f"zpact_{phone}_{safe_pin}"),
        pin_code_hash=crud.hash_password(safe_pin),
        role=UserRole.CUSTOMER,
        is_active=True,
        phone_verified=True,
        serial_code=serial_code,
        approval_status=approval_state,
        referral_code=secrets.token_hex(4).upper(),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    crud.register_user_device(
        db=db,
        user_id=new_user.id,
        device_id=device_id,
        user_agent=request.headers.get("User-Agent"),
        ip=request.client.host if request.client else None,
    )

    existing_tp = db.query(TeacherProfile).filter(TeacherProfile.primary_phone == phone).first()
    if not existing_tp:
        db.add(TeacherProfile(
            user_id=new_user.id,
            teacher_name=full_name or f"مدرس {phone[-4:]}",
            primary_phone=phone,
            watermark_type="text",
            watermark_text=f"خاص بطلاب {full_name or phone}",
            is_verified=(approval_state == "approved"),
        ))
        db.commit()

    if valid_serial_obj:
        valid_serial_obj.used_count += 1
        if valid_serial_obj.used_count >= valid_serial_obj.max_uses:
            valid_serial_obj.is_active = False
        db.commit()
        if float(valid_serial_obj.bonus_coins or 0) > 0:
            crud.update_user_coins(
                db, new_user.id, float(valid_serial_obj.bonus_coins),
                TransactionType.RECHARGE, f"رصيد ترحيبي من سيريال {valid_serial_obj.serial_code}"
            )
            db.refresh(new_user)

        token = create_access_token(user_id=new_user.id, role=new_user.role.value)
        return {
            "status": "approved",
            "access_token": token,
            "token_type": "bearer",
            "user": schemas.UserOut.model_validate(new_user).model_dump(mode="json"),
        }

    db.add(Notification(
        user_id=None,
        title="👨‍🏫 طلب تفعيل حساب مدرس جديد",
        message=f"المدرس: {full_name} | الهاتف: {phone} | السيريال: {serial_code}",
    ))
    db.commit()

    internal_event_bus.fire_event({
        "event": "TEACHER_APPROVAL_REQUEST",
        "user_id": new_user.id,
        "full_name": full_name,
        "phone": phone,
        "serial_code": serial_code,
    })

    return {
        "status": "pending_approval",
        "user_id": new_user.id,
        "serial_code": serial_code,
        "phone": phone,
        "full_name": full_name,
        "message": "✅ تم تسجيل طلبك وإصدار السيريال بنجاح! وصل إشعار فوري للإدارة وسيفتح المتجر تلقائياً بمجرد التفعيل.",
    }


@router.post("/auth/teacher-register", summary="إنشاء حساب مدرس جديد برقم الهاتف + كلمة السر + رمز الـ OTP")
def teacher_register_with_otp(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    full_name = str(payload.get("full_name", "")).strip()
    password = str(payload.get("password", "")).strip()
    challenge_token = str(payload.get("challenge_token", "")).strip()
    otp_code = str(payload.get("otp_code", "")).strip()
    vip_serial_input = str(payload.get("serial_code", "")).strip().upper()
    device_id = str(payload.get("device_id", "")).strip() or f"dev_{secrets.token_hex(8)}"

    if not re.match(r"^01[0125][0-9]{8}$", phone):
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف مصري صحيح مكون من 11 رقماً")
    if len(full_name) < 3:
        raise HTTPException(status_code=400, detail="يرجى إدخال اسمك الأكاديمي الثلاثي بشكل صحيح")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="يجب أن لا تقل كلمة السر عن 6 أحرف أو أرقام")

    if crud.is_blacklisted(db, phone) or crud.is_blacklisted(db, full_name):
        raise HTTPException(status_code=403, detail="⛔ هذه البيانات محظورة أمنياً. تواصل مع الإدارة.")

    # التحقق من سيريال VIP الاختياري قبل استهلاك الـ OTP حتى لا يضيع الكود عند خطأ كتابة السيريال
    valid_serial_obj: Optional[ActivationSerial] = None
    if vip_serial_input:
        valid_serial_obj = db.query(ActivationSerial).filter(
            ActivationSerial.serial_code == vip_serial_input,
            ActivationSerial.is_active == True,
        ).first()
        if not valid_serial_obj or valid_serial_obj.used_count >= valid_serial_obj.max_uses:
            raise HTTPException(status_code=400, detail="⚠️ كود السيريال المدخل غير صحيح أو تم استنفاده")

    # التحقق الصارم من رمز الـ OTP المكون من 6 أرقام
    _verify_and_consume_otp(db, phone=phone, target_role="teacher", challenge_token=challenge_token, otp_code=otp_code)

    synthetic_email = f"{phone}@teachers.zpact.live"
    existing_user = db.query(User).filter((User.phone == phone) | (User.email == synthetic_email)).first()

    if existing_user and getattr(existing_user, "has_custom_password", False) and getattr(existing_user, "phone_verified", False):
        raise HTTPException(status_code=400, detail="هذا الرقم مسجل بالفعل — استخدم تسجيل الدخول بكلمة السر")

    serial_code = (existing_user.serial_code if existing_user and existing_user.serial_code else make_serial_code("TCH", phone))
    approval_state = "approved" if (valid_serial_obj or (existing_user and existing_user.approval_status == "approved")) else "pending"

    if existing_user:
        existing_user.full_name = full_name
        existing_user.phone = phone
        existing_user.password_hash = crud.hash_password(password)
        existing_user.phone_verified = True
        existing_user.has_custom_password = True
        existing_user.serial_code = serial_code
        existing_user.approval_status = approval_state
        user_obj = existing_user
    else:
        user_obj = User(
            full_name=full_name,
            email=synthetic_email,
            phone=phone,
            password_hash=crud.hash_password(password),
            role=UserRole.CUSTOMER,
            is_active=True,
            phone_verified=True,
            has_custom_password=True,
            serial_code=serial_code,
            approval_status=approval_state,
            referral_code=secrets.token_hex(4).upper(),
        )
        db.add(user_obj)

    db.commit()
    db.refresh(user_obj)

    devices = crud.get_user_devices(db, user_obj.id)
    if not any(d.device_id == device_id for d in devices):
        crud.register_user_device(
            db=db,
            user_id=user_obj.id,
            device_id=device_id,
            user_agent=request.headers.get("User-Agent"),
            ip=request.client.host if request.client else None,
        )

    existing_tp = db.query(TeacherProfile).filter(TeacherProfile.primary_phone == phone).first()
    if not existing_tp:
        db.add(TeacherProfile(
            user_id=user_obj.id,
            teacher_name=full_name,
            primary_phone=phone,
            watermark_type="text",
            watermark_text=f"خاص بطلاب {full_name}",
            is_verified=(approval_state == "approved"),
        ))
        db.commit()
    elif not existing_tp.user_id:
        existing_tp.user_id = user_obj.id
        db.commit()

    if valid_serial_obj:
        valid_serial_obj.used_count += 1
        if valid_serial_obj.used_count >= valid_serial_obj.max_uses:
            valid_serial_obj.is_active = False
        db.commit()
        if float(valid_serial_obj.bonus_coins or 0) > 0:
            crud.update_user_coins(
                db, user_obj.id, float(valid_serial_obj.bonus_coins),
                TransactionType.RECHARGE, f"رصيد ترحيبي من سيريال {valid_serial_obj.serial_code}"
            )
            db.refresh(user_obj)

    if approval_state == "approved":
        token = create_access_token(user_id=user_obj.id, role=user_obj.role.value)
        return {
            "status": "approved",
            "access_token": token,
            "token_type": "bearer",
            "user": schemas.UserOut.model_validate(user_obj).model_dump(mode="json"),
        }

    db.add(Notification(
        user_id=None,
        title="👨‍🏫 طلب تفعيل حساب مدرس موثق بالـ OTP",
        message=f"المدرس: {full_name} | الهاتف الموثق: {phone} | السيريال: {serial_code}",
    ))
    db.commit()

    internal_event_bus.fire_event({
        "event": "TEACHER_APPROVAL_REQUEST",
        "user_id": user_obj.id,
        "full_name": full_name,
        "phone": phone,
        "serial_code": serial_code,
    })

    return {
        "status": "pending_approval",
        "user_id": user_obj.id,
        "serial_code": serial_code,
        "phone": phone,
        "full_name": full_name,
        "message": "✅ تم التحقق من رقمك بالـ OTP وتسجيل كلمة السر بنجاح! سيفتح المتجر تلقائياً فور ضغط الإدارة على موافقة.",
    }


@router.post("/auth/teacher-login", summary="تسجيل دخول المدرس برقم الهاتف + كلمة السر (+ OTP للأجهزة الجديدة)")
def teacher_login_with_password_and_otp(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    password = str(payload.get("password", "")).strip()
    device_id = str(payload.get("device_id", "")).strip() or f"dev_{secrets.token_hex(8)}"
    challenge_token = str(payload.get("challenge_token", "")).strip()
    otp_code = str(payload.get("otp_code", "")).strip()

    if not re.match(r"^01[0125][0-9]{8}$", phone):
        raise HTTPException(status_code=400, detail="أدخل رقم هاتف مصري صحيح مكون من 11 رقماً")

    synthetic_email = f"{phone}@teachers.zpact.live"
    user = db.query(User).filter((User.phone == phone) | (User.email == synthetic_email)).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="📱 هذا الرقم غير مسجل بعد — اضغط (حساب مدرس جديد) للتسجيل برقمك وكلمة السر ورمز الـ OTP.",
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="⛔ تم إيقاف هذا الحساب من قبل الإدارة")

    # ترقية سلسة للحسابات القديمة التي أنشئت قبل نظام كلمة السر + OTP
    if not getattr(user, "has_custom_password", False):
        otp_info = _issue_otp_challenge(db, phone=phone, target_role="teacher", purpose="reset_password")
        return {
            "status": "need_password_setup",
            **otp_info,
            "message": "🔐 لتأمين حسابك بالنظام الجديد، أرسلنا كود OTP لرقمك — أدخل الكود واختر كلمة السر الخاصة بك.",
        }

    if not password or not crud.verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="⚠️ رقم الهاتف أو كلمة السر غير صحيحة")

    if not user.serial_code:
        user.serial_code = make_serial_code("TCH", phone)
        db.commit()

    devices = crud.get_user_devices(db, user.id)
    matching_dev = next((d for d in devices if d.device_id == device_id), None)
    needs_otp = (matching_dev is None) or (not bool(getattr(user, "phone_verified", False)))

    if needs_otp:
        if challenge_token and otp_code:
            _verify_and_consume_otp(db, phone=phone, target_role="teacher", challenge_token=challenge_token, otp_code=otp_code)
            user.phone_verified = True
            if not matching_dev:
                limit = 5 if user.role == UserRole.ADMIN else 2
                if len(devices) >= limit:
                    raise HTTPException(
                        status_code=403,
                        detail=f"🔒 هذا الحساب مربوط بسيريال أجهزتك المعتمدة ({limit} أجهزة كحد أقصى). تواصل مع الإدارة لإعادة ضبط السيريال.",
                    )
                crud.register_user_device(
                    db=db,
                    user_id=user.id,
                    device_id=device_id,
                    user_agent=request.headers.get("User-Agent"),
                    ip=request.client.host if request.client else None,
                )
            db.commit()
        else:
            otp_info = _issue_otp_challenge(db, phone=phone, target_role="teacher", purpose="login_device")
            return {
                "status": "require_otp",
                **otp_info,
                "message": "🔐 جهاز جديد — أرسلنا رمز تحقق (OTP) من 6 أرقام لرقم هاتفك لتأكيد هويتك قبل فتح الحساب.",
            }
    else:
        if matching_dev.is_blocked:
            raise HTTPException(status_code=403, detail="⛔ هذا الجهاز محظور أمنياً. راجع الإدارة.")
        matching_dev.last_login = datetime.utcnow()
        matching_dev.last_ip = request.client.host if request.client else None
        db.commit()

    if getattr(user, "approval_status", "approved") != "approved":
        return {
            "status": "pending_approval",
            "serial_code": user.serial_code,
            "phone": phone,
            "full_name": user.full_name,
            "message": "⏳ حسابك موثق وبانتظار ضغطة تفعيل من الإدارة ليفتح المتجر تلقائياً.",
        }

    token = create_access_token(user_id=user.id, role=user.role.value)
    return {
        "status": "approved",
        "access_token": token,
        "token_type": "bearer",
        "user": schemas.UserOut.model_validate(user).model_dump(mode="json"),
    }


@router.post("/auth/teacher-reset-password", summary="استعادة أو تعيين كلمة سر المدرس عبر رمز التحقق OTP")
def teacher_reset_password_with_otp(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    new_password = str(payload.get("new_password", "")).strip()
    challenge_token = str(payload.get("challenge_token", "")).strip()
    otp_code = str(payload.get("otp_code", "")).strip()
    device_id = str(payload.get("device_id", "")).strip() or f"dev_{secrets.token_hex(8)}"

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="كلمة السر الجديدة يجب أن لا تقل عن 6 أحرف أو أرقام")

    synthetic_email = f"{phone}@teachers.zpact.live"
    user = db.query(User).filter((User.phone == phone) | (User.email == synthetic_email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="هذا الرقم غير مسجل بالمنصة")

    _verify_and_consume_otp(db, phone=phone, target_role="teacher", challenge_token=challenge_token, otp_code=otp_code)

    user.password_hash = crud.hash_password(new_password)
    user.has_custom_password = True
    user.phone_verified = True
    db.commit()

    devices = crud.get_user_devices(db, user.id)
    if not any(d.device_id == device_id for d in devices) and len(devices) < 2:
        crud.register_user_device(
            db=db,
            user_id=user.id,
            device_id=device_id,
            user_agent=request.headers.get("User-Agent"),
            ip=request.client.host if request.client else None,
        )

    if getattr(user, "approval_status", "approved") != "approved":
        return {
            "status": "pending_approval",
            "serial_code": user.serial_code,
            "phone": phone,
            "full_name": user.full_name,
            "message": "✅ تم تعيين كلمة السر الجديدة! حسابك بانتظار موافقة الإدارة.",
        }

    token = create_access_token(user_id=user.id, role=user.role.value)
    return {
        "status": "approved",
        "access_token": token,
        "token_type": "bearer",
        "user": schemas.UserOut.model_validate(user).model_dump(mode="json"),
    }


@router.post("/auth/teacher-activate-serial", summary="تفعيل فوري لحساب مدرس معلق باستخدام كود سيريال VIP")
def teacher_activate_pending_with_vip_serial(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    vip_code = str(payload.get("serial_code", "")).strip().upper()
    device_id = str(payload.get("device_id", "")).strip()

    synthetic_email = f"{phone}@teachers.zpact.live"
    user = db.query(User).filter((User.phone == phone) | (User.email == synthetic_email)).first()
    if not user or not getattr(user, "phone_verified", False):
        raise HTTPException(status_code=403, detail="يرجى إتمام التحقق من رقم الهاتف بالـ OTP أولاً")

    devices = crud.get_user_devices(db, user.id)
    if not any(d.device_id == device_id and not d.is_blocked for d in devices):
        raise HTTPException(status_code=403, detail="هذا الجهاز غير مطابق للجهاز الموثق")

    valid_serial_obj = db.query(ActivationSerial).filter(
        ActivationSerial.serial_code == vip_code,
        ActivationSerial.is_active == True,
    ).first()
    if not valid_serial_obj or valid_serial_obj.used_count >= valid_serial_obj.max_uses:
        raise HTTPException(status_code=400, detail="⚠️ كود السيريال غير صحيح أو تم استخدامه بالكامل")

    user.approval_status = "approved"
    valid_serial_obj.used_count += 1
    if valid_serial_obj.used_count >= valid_serial_obj.max_uses:
        valid_serial_obj.is_active = False
    if float(valid_serial_obj.bonus_coins or 0) > 0:
        crud.update_user_coins(
            db, user.id, float(valid_serial_obj.bonus_coins),
            TransactionType.RECHARGE, f"هدية تفعيل سيريال {valid_serial_obj.serial_code}"
        )
    db.commit()
    db.refresh(user)

    token = create_access_token(user_id=user.id, role=user.role.value)
    return {
        "status": "approved",
        "access_token": token,
        "token_type": "bearer",
        "user": schemas.UserOut.model_validate(user).model_dump(mode="json"),
    }


@router.get("/auth/teacher-status", summary="فحص لحظي لحالة موافقة الإدارة على حساب المدرس الموثق")
def check_teacher_approval_status(
    phone: str = Query(...),
    device_id: str = Query(...),
    db: Session = Depends(get_db),
):
    clean_p = normalize_eg_phone(phone)
    synthetic_email = f"{clean_p}@teachers.zpact.live"
    user = db.query(User).filter((User.phone == clean_p) | (User.email == synthetic_email)).first()
    if not user:
        return {"status": "not_found"}

    st = getattr(user, "approval_status", "approved") or "approved"
    if st == "approved" and user.is_active and getattr(user, "phone_verified", False):
        devices = crud.get_user_devices(db, user.id)
        if any(d.device_id == device_id and not d.is_blocked for d in devices):
            token = create_access_token(user_id=user.id, role=user.role.value)
            return {
                "status": "approved",
                "access_token": token,
                "user": schemas.UserOut.model_validate(user).model_dump(mode="json"),
            }
    return {
        "status": st if user.is_active else "rejected",
        "serial_code": user.serial_code,
        "full_name": user.full_name,
    }


# ================================================================
# 2. منظومة فريق المرسلين المحمية (Phone + Password + OTP + قفل السيريال)
# ================================================================

@router.post("/sender/auth", summary="تسجيل أو دخول المرسل برقم الهاتف فقط مع قفل السيريال (متوافق مع الإصدارات السابقة)")
def sender_register_or_login(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    raw_phone = str(payload.get("phone", "")).strip()
    device_id = str(payload.get("device_id", "")).strip() or f"snd_dev_{secrets.token_hex(8)}"
    display_name = str(payload.get("display_name", "")).strip() or None

    phone = normalize_eg_phone(raw_phone)
    if len(phone) < 10 or not phone.isdigit():
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم الهاتف الصحيح (11 رقماً)")

    sender = db.query(SenderAgent).filter(SenderAgent.phone == phone).first()

    if sender:
        if display_name and not sender.display_name:
            sender.display_name = display_name

        if sender.device_id and sender.device_id != device_id:
            raise HTTPException(
                status_code=403,
                detail=f"⛔ هذا الحساب مربوط بسيريال جهاز آخر ({sender.serial_code}). لا يمكن فتحه على هاتف مختلف إلا بموافقة الإدارة.",
            )

        if not sender.device_id:
            sender.device_id = device_id

        if not sender.auth_token:
            sender.auth_token = f"snd_{secrets.token_hex(24)}"

        sender.last_seen_at = datetime.utcnow()
        db.commit()
        db.refresh(sender)

        return {
            "status": sender.status,
            "sender_token": sender.auth_token if sender.status == "approved" else None,
            "sender": serialize_sender(sender),
        }

    serial_code = make_serial_code("SND", phone)
    new_sender = SenderAgent(
        phone=phone,
        display_name=display_name or f"مرسل {phone[-4:]}",
        serial_code=serial_code,
        device_id=device_id,
        auth_token=f"snd_{secrets.token_hex(24)}",
        status="pending",
        phone_verified=True,
        wallet_number=phone,
        commission_per_pdf=15.0,
        commission_percent=0.0,
        custom_material_commissions={},
    )
    db.add(new_sender)
    db.commit()
    db.refresh(new_sender)

    internal_event_bus.fire_event({
        "event": "SENDER_APPROVAL_REQUEST",
        "sender_id": new_sender.id,
        "phone": new_sender.phone,
        "display_name": new_sender.display_name,
        "serial_code": new_sender.serial_code,
    })

    logger.info(f"📨 [Sender Team] طلب انضمام مرسل جديد بالهاتف {phone} | السيريال: {serial_code}")
    return {
        "status": "pending",
        "sender_token": None,
        "sender": serialize_sender(new_sender),
        "message": "✅ تم إرسال رقمك وسيريال جهازك للإدارة! سيفتح تطبيق المرسلين تلقائياً فور ضغط المسؤول على موافقة.",
    }


@router.post("/sender/register", summary="تسجيل مرسل جديد برقم الهاتف + كلمة السر + رمز التحقق OTP + قفل السيريال")
def sender_register_with_otp(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    display_name = str(payload.get("display_name", "")).strip()
    password = str(payload.get("password", "")).strip()
    challenge_token = str(payload.get("challenge_token", "")).strip()
    otp_code = str(payload.get("otp_code", "")).strip()
    device_id = str(payload.get("device_id", "")).strip() or f"snd_dev_{secrets.token_hex(8)}"

    if not re.match(r"^01[0125][0-9]{8}$", phone):
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف مصري صحيح مكون من 11 رقماً")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="يجب أن لا تقل كلمة سر المرسل عن 6 أحرف أو أرقام")

    existing = db.query(SenderAgent).filter(SenderAgent.phone == phone).first()
    if existing and existing.password_hash and existing.phone_verified:
        raise HTTPException(status_code=400, detail="رقم الهاتف مسجل بالفعل في فريق المرسلين — سجّل الدخول بكلمة السر")

    # التحقق الصارم من رمز الـ OTP المكون من 6 أرقام
    _verify_and_consume_otp(db, phone=phone, target_role="sender", challenge_token=challenge_token, otp_code=otp_code)

    if existing:
        if existing.device_id and existing.device_id != device_id:
            raise HTTPException(
                status_code=403,
                detail=f"⛔ هذا الرقم مربوط بسيريال هاتف آخر ({existing.serial_code}). تواصل مع الإدارة لإعادة ضبط السيريال.",
            )
        existing.display_name = display_name or existing.display_name or f"مرسل {phone[-4:]}"
        existing.password_hash = crud.hash_password(password)
        existing.phone_verified = True
        existing.device_id = device_id
        if not existing.auth_token:
            existing.auth_token = f"snd_{secrets.token_hex(24)}"
        sender_obj = existing
    else:
        serial_code = make_serial_code("SND", phone)
        sender_obj = SenderAgent(
            phone=phone,
            display_name=display_name or f"مرسل {phone[-4:]}",
            password_hash=crud.hash_password(password),
            phone_verified=True,
            serial_code=serial_code,
            device_id=device_id,
            auth_token=f"snd_{secrets.token_hex(24)}",
            status="pending",
            wallet_number=phone,
            commission_per_pdf=15.0,
            commission_percent=0.0,
            custom_material_commissions={},
        )
        db.add(sender_obj)

    db.commit()
    db.refresh(sender_obj)

    if sender_obj.status == "approved":
        return {
            "status": "approved",
            "sender_token": sender_obj.auth_token,
            "sender": serialize_sender(sender_obj),
        }

    internal_event_bus.fire_event({
        "event": "SENDER_APPROVAL_REQUEST",
        "sender_id": sender_obj.id,
        "phone": sender_obj.phone,
        "display_name": sender_obj.display_name,
        "serial_code": sender_obj.serial_code,
    })

    logger.info(f"📨 [Sender Team] طلب انضمام مرسل موثق بالـ OTP {phone} | السيريال: {sender_obj.serial_code}")
    return {
        "status": "pending",
        "sender_token": None,
        "sender": serialize_sender(sender_obj),
        "message": "✅ تم توثيق رقمك بالـ OTP وحفظ كلمة السر! وصل طلبك للإدارة وسيفتح النظام تلقائياً فور الموافقة.",
    }


@router.post("/sender/login", summary="تسجيل دخول المرسل برقم الهاتف + كلمة السر + قفل السيريال (+ OTP عند الحاجة)")
def sender_login_with_password(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    password = str(payload.get("password", "")).strip()
    device_id = str(payload.get("device_id", "")).strip() or f"snd_dev_{secrets.token_hex(8)}"
    challenge_token = str(payload.get("challenge_token", "")).strip()
    otp_code = str(payload.get("otp_code", "")).strip()

    if not re.match(r"^01[0125][0-9]{8}$", phone):
        raise HTTPException(status_code=400, detail="أدخل رقم هاتف مصري صحيح (11 رقماً)")

    sender = db.query(SenderAgent).filter(SenderAgent.phone == phone).first()
    if not sender:
        raise HTTPException(
            status_code=404,
            detail="📱 رقم الهاتف غير مسجل في فريق المرسلين — اضغط (تسجيل مرسل جديد) للتسجيل برقمك وكلمة السر والـ OTP.",
        )

    if sender.device_id and sender.device_id != device_id:
        raise HTTPException(
            status_code=403,
            detail=f"⛔ هذا الحساب مربوط بسيريال هاتف آخر ({sender.serial_code}). لا يمكن فتحه على هاتف مختلف إلا بإعادة ضبط السيريال من الإدارة.",
        )

    # ترقية حسابات المرسلين السابقة التي لم تُعين لها كلمة سر بعد
    if not sender.password_hash or not getattr(sender, "phone_verified", False):
        if challenge_token and otp_code and len(password) >= 6:
            _verify_and_consume_otp(db, phone=phone, target_role="sender", challenge_token=challenge_token, otp_code=otp_code)
            sender.password_hash = crud.hash_password(password)
            sender.phone_verified = True
            sender.device_id = device_id
            db.commit()
        else:
            otp_info = _issue_otp_challenge(db, phone=phone, target_role="sender", purpose="reset_password")
            return {
                "status": "require_otp_setup",
                **otp_info,
                "message": "🔐 لتأمين حسابك بالنظام الجديد، أرسلنا كود OTP لرقمك — أدخل الكود لتفعيل كلمة السر الخاصة بك.",
            }
    else:
        if not password or not crud.verify_password(password, sender.password_hash):
            raise HTTPException(status_code=401, detail="⚠️ رقم الهاتف أو كلمة سر المرسل غير صحيحة")

    if not sender.device_id:
        sender.device_id = device_id
    if not sender.auth_token:
        sender.auth_token = f"snd_{secrets.token_hex(24)}"

    sender.last_seen_at = datetime.utcnow()
    db.commit()
    db.refresh(sender)

    return {
        "status": sender.status,
        "sender_token": sender.auth_token if sender.status == "approved" else None,
        "sender": serialize_sender(sender),
    }


@router.post("/sender/reset-password", summary="استعادة كلمة سر المرسل عبر رمز التحقق OTP على هاتفه المعتمد")
def sender_reset_password_with_otp(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    phone = normalize_eg_phone(str(payload.get("phone", "")).strip())
    new_password = str(payload.get("new_password", "")).strip()
    challenge_token = str(payload.get("challenge_token", "")).strip()
    otp_code = str(payload.get("otp_code", "")).strip()
    device_id = str(payload.get("device_id", "")).strip()

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="كلمة السر الجديدة يجب أن لا تقل عن 6 أحرف أو أرقام")

    sender = db.query(SenderAgent).filter(SenderAgent.phone == phone).first()
    if not sender:
        raise HTTPException(status_code=404, detail="رقم الهاتف غير مسجل في فريق المرسلين")

    if sender.device_id and device_id and sender.device_id != device_id:
        raise HTTPException(status_code=403, detail="⛔ لا يمكن استعادة كلمة السر إلا من نفس الهاتف المربوط بالسيريال أو عبر الإدارة")

    _verify_and_consume_otp(db, phone=phone, target_role="sender", challenge_token=challenge_token, otp_code=otp_code)

    sender.password_hash = crud.hash_password(new_password)
    sender.phone_verified = True
    if not sender.auth_token:
        sender.auth_token = f"snd_{secrets.token_hex(24)}"
    db.commit()
    db.refresh(sender)

    return {
        "status": sender.status,
        "sender_token": sender.auth_token if sender.status == "approved" else None,
        "sender": serialize_sender(sender),
    }


@router.get("/sender/status", summary="فحص لحظي لحالة اعتماد المرسل الموثق")
def sender_poll_status(
    phone: str = Query(...),
    device_id: str = Query(...),
    db: Session = Depends(get_db),
):
    clean_p = normalize_eg_phone(phone)
    sender = db.query(SenderAgent).filter(SenderAgent.phone == clean_p).first()
    if not sender:
        return {"status": "not_found"}

    if sender.device_id and sender.device_id != device_id:
        return {"status": "device_mismatch", "serial_code": sender.serial_code}

    if sender.status == "approved" and getattr(sender, "phone_verified", False):
        if not sender.auth_token:
            sender.auth_token = f"snd_{secrets.token_hex(24)}"
            db.commit()
        return {
            "status": "approved",
            "sender_token": sender.auth_token,
            "sender": serialize_sender(sender),
        }

    return {
        "status": sender.status,
        "sender": serialize_sender(sender),
    }


@router.get("/sender/workspace", summary="جلب بيانات مساحة عمل المرسل والمذكرات والعمولات وطابور المعلقات")
def get_sender_workspace(
    sender: SenderAgent = Depends(require_approved_sender),
    db: Session = Depends(get_db),
):
    materials = db.query(Material).filter(Material.is_published == True).order_by(Material.id.desc()).all()
    catalog = []
    for m in materials:
        price_egp = float(m.price or m.price_coins or 0.0)
        comm = calculate_sender_commission_for_material(sender, m)
        catalog.append({
            "id": m.id,
            "title": m.title,
            "tag": m.tag or "عام",
            "description": m.description or "",
            "price": price_egp,
            "total_pages": m.total_pages or 0,
            "cover_image_path": m.cover_image_path,
            "my_commission": comm,
        })

    # الطلبات المعلقة الواردة من المنصة / تطبيق المدرسين / الميني آب / البوت
    pending_raw = (
        db.query(BotOrder)
        .filter(BotOrder.status == OrderFulfillmentStatus.PENDING_PAYMENT)
        .order_by(BotOrder.id.desc())
        .limit(60)
        .all()
    )
    pending_groups = _group_bot_orders_as_bundles(pending_raw, db, for_sender=sender)

    # سجل عمليات المرسل الأخيرة وحالة جهوزية روابط التحميل والواتساب
    ledger_rows = (
        db.query(SenderLedgerEntry)
        .filter(SenderLedgerEntry.sender_id == sender.id)
        .order_by(SenderLedgerEntry.id.desc())
        .limit(40)
        .all()
    )
    ledger_out = [_serialize_ledger_entry_with_live_links(row, db) for row in ledger_rows]

    # بروفايلات المدرسين المحفوظة لتعبئة سريعة بنقرة زر
    teachers = db.query(TeacherProfile).order_by(TeacherProfile.updated_at.desc()).limit(100).all()
    saved_teachers = [
        {
            "id": t.id,
            "teacher_name": t.teacher_name,
            "primary_phone": t.primary_phone,
            "secondary_phone": t.secondary_phone or "",
            "series_title": t.series_title or "",
            "watermark_text": t.watermark_text or "",
        }
        for t in teachers
    ]

    return {
        "sender": serialize_sender(sender),
        "admin_wallets": {
            "vodafone_cash": "01097671808",
            "instapay": "zpact@instapay",
        },
        "catalog": catalog,
        "pending_orders": pending_groups,
        "ledger": ledger_out,
        "saved_teachers": saved_teachers,
    }


@router.post("/sender/orders/create", summary="إنشاء وختم طلب فوري (مذكرة أو عدة مذكرات) بواسطة المرسل لإرساله للمدرس عبر الواتساب")
def sender_create_direct_bundle(
    request: Request,
    background_tasks: BackgroundTasks,
    material_ids_json: str = Form(..., description="قائمة معرفات المذكرات JSON مثل [1,2]"),
    buyer_name_stamp: str = Form(""),
    buyer_phone_stamp: str = Form(""),
    buyer_phone_stamp_2: Optional[str] = Form(None),
    doc_name_stamp: Optional[str] = Form(None),
    series_name: Optional[str] = Form(None),
    watermark_text: Optional[str] = Form(None),
    watermark_type: str = Form("none"),
    from_wallet_phone: str = Form(..., description="الرقم المحول منه"),
    to_wallet_phone: str = Form(..., description="الرقم المحول إليه"),
    transferred_amount: float = Form(...),
    payment_method: str = Form("vodafone_cash"),
    notes: Optional[str] = Form(None),
    receipt_image: Optional[UploadFile] = File(None),
    watermark_logo: Optional[UploadFile] = File(None),
    sender: SenderAgent = Depends(require_approved_sender),
    db: Session = Depends(get_db),
):
    try:
        mat_ids = [int(x) for x in json.loads(material_ids_json)]
    except Exception:
        raise HTTPException(status_code=400, detail="قائمة المذكرات المختارة غير صالحة")

    mat_ids = list(dict.fromkeys(mat_ids))
    if not mat_ids:
        raise HTTPException(status_code=400, detail="اختر مذكرة واحدة على الأقل")

    buyer_name_stamp = (buyer_name_stamp or "").strip()
    clean_teacher_p1 = normalize_eg_phone(buyer_phone_stamp) if (buyer_phone_stamp or "").strip() else ""
    clean_teacher_p2 = normalize_eg_phone(buyer_phone_stamp_2) if (buyer_phone_stamp_2 or "").strip() else None
    clean_from_wallet = normalize_eg_phone(from_wallet_phone)
    clean_to_wallet = normalize_eg_phone(to_wallet_phone)

    if len(clean_from_wallet) < 5 or len(clean_to_wallet) < 5:
        raise HTTPException(status_code=400, detail="يرجى كتابة الرقم المحول منه والرقم المحول إليه بشكل صحيح")

    jwt_user = get_optional_jwt_user(request, db)
    is_admin_actor = stamp_settings.is_admin_actor(role=jwt_user.role if jwt_user else None)
    ok_stamp, err_stamp = stamp_settings.validate_stamp_fields(
        buyer_name_stamp=buyer_name_stamp,
        buyer_phone_stamp=clean_teacher_p1 or buyer_phone_stamp,
        buyer_phone_stamp_2=clean_teacher_p2,
        series_name=series_name,
        doc_name_stamp=doc_name_stamp,
        watermark_text=watermark_text,
        is_admin=is_admin_actor,
    )
    if not ok_stamp:
        raise HTTPException(status_code=400, detail=err_stamp)

    blacklist_err = crud.check_stamp_data_against_blacklist(
        db, buyer_name_stamp, clean_teacher_p1, watermark_text or ""
    )
    if blacklist_err:
        raise HTTPException(status_code=403, detail=blacklist_err)

    materials = db.query(Material).filter(Material.id.in_(mat_ids), Material.is_published == True).all()
    if len(materials) != len(mat_ids):
        raise HTTPException(status_code=404, detail="بعض المذكرات المختارة غير متاحة حالياً")

    receipt_path = save_uploaded_receipt(receipt_image)
    wm_logo_path = save_uploaded_watermark_logo(watermark_logo)
    bundle_code = f"SND-{secrets.token_hex(3).upper()}"

    effective_series = (series_name or doc_name_stamp or "").strip() or None
    effective_wm_type = "logo" if wm_logo_path else (watermark_type or "none")

    # حفظ / تحديث بروفايل المدرس فقط عند وجود رقم هاتف لتجنب تصادم السجلات الفارغة الهاتف
    tp = None
    if clean_teacher_p1:
        tp = db.query(TeacherProfile).filter(TeacherProfile.primary_phone == clean_teacher_p1).first()
        if tp:
            if buyer_name_stamp:
                tp.teacher_name = buyer_name_stamp
            tp.secondary_phone = clean_teacher_p2 or tp.secondary_phone
            tp.series_title = effective_series or tp.series_title
            tp.watermark_text = watermark_text or tp.watermark_text
            tp.watermark_type = effective_wm_type
            if wm_logo_path:
                tp.logo_image_path = wm_logo_path
            tp.updated_at = datetime.utcnow()
        else:
            tp = TeacherProfile(
                teacher_name=buyer_name_stamp,
                primary_phone=clean_teacher_p1,
                secondary_phone=clean_teacher_p2,
                series_title=effective_series,
                watermark_text=watermark_text,
                watermark_type=effective_wm_type,
                logo_image_path=wm_logo_path,
                is_verified=True,
            )
            db.add(tp)
        db.commit()
        db.refresh(tp)

    created_orders: List[BotOrder] = []
    total_catalog_price = 0.0
    total_sender_comm = 0.0
    titles_list: List[str] = []

    for idx, mat in enumerate(materials, start=1):
        m_price = float(mat.price or mat.price_coins or 0.0)
        m_comm = calculate_sender_commission_for_material(sender, mat)
        total_catalog_price += m_price
        total_sender_comm += m_comm
        titles_list.append(mat.title)

        order_code = f"{bundle_code}-{idx}" if len(materials) > 1 else bundle_code
        bo = BotOrder(
            order_code=order_code,
            bundle_code=bundle_code,
            telegram_chat_id=f"sender_{sender.id}",
            telegram_user_id=f"sender_{sender.id}",
            teacher_profile_id=tp.id if tp else None,
            material_id=mat.id,
            buyer_name_stamp=buyer_name_stamp,
            buyer_phone_stamp=clean_teacher_p1,
            buyer_phone_stamp_2=clean_teacher_p2,
            doc_name_stamp=effective_series,
            series_name=effective_series,
            watermark_text=watermark_text,
            watermark_type=effective_wm_type,
            watermark_logo_path=wm_logo_path,
            total_amount=m_price,
            payment_method=payment_method,
            claimed_sender_phone=clean_from_wallet,
            receiver_wallet_phone=clean_to_wallet,
            receipt_image_path=receipt_path,
            payment_reference=f"SENDER:{sender.phone}",
            processed_by_sender_id=sender.id,
            sender_commission_earned=m_comm,
            status=OrderFulfillmentStatus.PAYMENT_CONFIRMED,
        )
        db.add(bo)
        created_orders.append(bo)

    db.commit()

    gross_val = float(transferred_amount) if float(transferred_amount) > 0 else total_catalog_price
    to_admin_flag = is_admin_wallet(clean_to_wallet)

    # حساب العهدة المالية:
    # إذا استلم المرسل التحويل في محفظته -> المرسل مدين للأدمن بـ (إجمالي التحويل - عمولة المرسل)
    # إذا دخل التحويل محفظة الأدمن مباشرة -> الأدمن مدين للمرسل بعمولته (-total_sender_comm)
    admin_delta = round((gross_val - total_sender_comm) if not to_admin_flag else (-total_sender_comm), 2)

    sender.total_pdfs_count = int(sender.total_pdfs_count or 0) + len(materials)
    sender.total_orders_count = int(sender.total_orders_count or 0) + 1
    sender.total_commission_earned = round(float(sender.total_commission_earned or 0.0) + total_sender_comm, 2)
    if to_admin_flag:
        sender.total_collected_by_admin = round(float(sender.total_collected_by_admin or 0.0) + gross_val, 2)
    else:
        sender.total_collected_by_sender = round(float(sender.total_collected_by_sender or 0.0) + gross_val, 2)
    sender.net_due_to_admin = round(float(sender.net_due_to_admin or 0.0) + admin_delta, 2)

    download_items = [
        {
            "order_code": o.order_code,
            "material_id": o.material_id,
            "title": next((m.title for m in materials if m.id == o.material_id), "مذكرة"),
            "download_url": f"/api/v3/sender/orders/{o.order_code}/download",
        }
        for o in created_orders
    ]

    ledger_entry = SenderLedgerEntry(
        sender_id=sender.id,
        entry_type="direct_order",
        bundle_code=bundle_code,
        order_code=created_orders[0].order_code,
        teacher_name=buyer_name_stamp,
        teacher_phone=clean_teacher_p1,
        from_wallet_phone=clean_from_wallet,
        to_wallet_phone=clean_to_wallet,
        collected_to_admin_wallet=to_admin_flag,
        pdfs_count=len(materials),
        materials_summary=" + ".join(titles_list),
        gross_amount=gross_val,
        sender_commission=round(total_sender_comm, 2),
        admin_net_delta=admin_delta,
        receipt_image_path=receipt_path,
        download_items_json=download_items,
        notes=notes,
    )
    db.add(ledger_entry)
    db.commit()
    db.refresh(ledger_entry)

    # إطلاق محرك الختم المتجه لكل مذكرة في السلة فوراً
    for bo in created_orders:
        db.refresh(bo)
        _trigger_order_stamping(bo, background_tasks, db)

    internal_event_bus.fire_event({
        "event": "SENDER_ORDER_CREATED",
        "bundle_code": bundle_code,
        "sender_name": sender.display_name or sender.phone,
        "sender_phone": sender.phone,
        "teacher_name": buyer_name_stamp,
        "teacher_phone": clean_teacher_p1,
        "from_wallet": clean_from_wallet,
        "to_wallet": clean_to_wallet,
        "pdfs_count": len(materials),
        "materials_summary": " + ".join(titles_list),
        "gross_amount": gross_val,
        "sender_commission": round(total_sender_comm, 2),
    })

    return {
        "status": "processing",
        "bundle_code": bundle_code,
        "pdfs_count": len(materials),
        "sender_commission": round(total_sender_comm, 2),
        "sender": serialize_sender(sender),
        "ledger_entry": _serialize_ledger_entry_with_live_links(ledger_entry, db),
    }


@router.post("/sender/orders/{target_code}/fulfill", summary="تأكيد وتنفيذ طلب معلق من طابور المعلقات بواسطة المرسل")
def sender_fulfill_pending_order(
    target_code: str,
    background_tasks: BackgroundTasks,
    payload: Optional[Dict[str, Any]] = None,
    sender: SenderAgent = Depends(require_approved_sender),
    db: Session = Depends(get_db),
):
    payload = payload or {}
    # قفل ذري لمنع مرسلين اثنين من اعتماد نفس الطلب في نفس اللحظة
    orders = (
        db.query(BotOrder)
        .filter((BotOrder.bundle_code == target_code) | (BotOrder.order_code == target_code))
        .with_for_update()
        .all()
    )
    if not orders:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")

    pending_items = [o for o in orders if o.status == OrderFulfillmentStatus.PENDING_PAYMENT]
    if not pending_items:
        raise HTTPException(status_code=409, detail="تم تنفيذ أو معالجة هذا الطلب بالفعل بواسطة مسؤول أو مرسل آخر!")

    to_wallet = normalize_eg_phone(str(payload.get("to_wallet_phone") or pending_items[0].receiver_wallet_phone or "01097671808"))
    to_admin_flag = is_admin_wallet(to_wallet)

    total_gross = 0.0
    total_comm = 0.0
    titles = []
    dl_items = []

    for o in pending_items:
        mat = db.query(Material).filter(Material.id == o.material_id).first()
        comm = calculate_sender_commission_for_material(sender, mat) if mat else float(sender.commission_per_pdf or 15.0)
        total_gross += float(o.total_amount or 0.0)
        total_comm += comm
        if mat:
            titles.append(mat.title)

        o.status = OrderFulfillmentStatus.PAYMENT_CONFIRMED
        o.processed_by_sender_id = sender.id
        o.sender_commission_earned = comm
        o.receiver_wallet_phone = to_wallet
        o.payment_reference = f"FULFILLED_BY:{sender.phone}"

        dl_items.append({
            "order_code": o.order_code,
            "material_id": o.material_id,
            "title": mat.title if mat else f"مذكرة #{o.material_id}",
            "download_url": f"/api/v3/sender/orders/{o.order_code}/download",
        })

    db.commit()

    admin_delta = round((total_gross - total_comm) if not to_admin_flag else (-total_comm), 2)
    sender.total_pdfs_count = int(sender.total_pdfs_count or 0) + len(pending_items)
    sender.total_orders_count = int(sender.total_orders_count or 0) + 1
    sender.total_commission_earned = round(float(sender.total_commission_earned or 0.0) + total_comm, 2)
    if to_admin_flag:
        sender.total_collected_by_admin = round(float(sender.total_collected_by_admin or 0.0) + total_gross, 2)
    else:
        sender.total_collected_by_sender = round(float(sender.total_collected_by_sender or 0.0) + total_gross, 2)
    sender.net_due_to_admin = round(float(sender.net_due_to_admin or 0.0) + admin_delta, 2)

    first = pending_items[0]
    ledger_entry = SenderLedgerEntry(
        sender_id=sender.id,
        entry_type="pending_fulfilled",
        bundle_code=first.bundle_code or first.order_code,
        order_code=first.order_code,
        teacher_name=first.buyer_name_stamp,
        teacher_phone=first.buyer_phone_stamp,
        from_wallet_phone=first.claimed_sender_phone,
        to_wallet_phone=to_wallet,
        collected_to_admin_wallet=to_admin_flag,
        pdfs_count=len(pending_items),
        materials_summary=" + ".join(titles),
        gross_amount=round(total_gross, 2),
        sender_commission=round(total_comm, 2),
        admin_net_delta=admin_delta,
        receipt_image_path=first.receipt_image_path,
        download_items_json=dl_items,
        notes="تمت المراجعة والتأكيد من طابور الطلبات المعلقة",
    )
    db.add(ledger_entry)
    db.commit()
    db.refresh(ledger_entry)

    for o in pending_items:
        db.refresh(o)
        _trigger_order_stamping(o, background_tasks, db)
        if o.telegram_chat_id and str(o.telegram_chat_id).isdigit():
            internal_event_bus.fire_event({
                "event": "ORDER_ADMIN_APPROVED",
                "order_code": o.order_code,
                "telegram_chat_id": o.telegram_chat_id,
            })

    internal_event_bus.fire_event({
        "event": "SENDER_FULFILLED_PENDING",
        "target_code": target_code,
        "sender_name": sender.display_name or sender.phone,
        "sender_phone": sender.phone,
        "pdfs_count": len(pending_items),
        "gross_amount": round(total_gross, 2),
        "sender_commission": round(total_comm, 2),
    })

    return {
        "status": "approved_and_stamping",
        "pdfs_count": len(pending_items),
        "sender_commission": round(total_comm, 2),
        "ledger_entry": _serialize_ledger_entry_with_live_links(ledger_entry, db),
    }


@router.get("/sender/orders/{order_code}/download", summary="تنزيل ملف الـ PDF المختوم للمرسل لإرساله للمدرس عبر الواتساب")
def sender_download_stamped_pdf(
    order_code: str,
    request: Request,
    sender_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    tok = sender_token or request.headers.get("X-Sender-Token")
    sender = None
    if tok:
        sender = db.query(SenderAgent).filter(SenderAgent.auth_token == tok, SenderAgent.status == "approved").first()

    if not sender:
        # السماح للأدمن أيضاً بالتنزيل المباشر
        require_admin_or_internal(request, db)

    order = db.query(BotOrder).filter(BotOrder.order_code == order_code).first()
    if not order:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")

    pdf_path = order.stamped_pdf_path
    if (not pdf_path or not os.path.isfile(pdf_path)) and order.purchase_id:
        p = db.query(Purchase).filter(Purchase.id == order.purchase_id).first()
        if p and p.output_pdf_path and os.path.isfile(p.output_pdf_path):
            pdf_path = p.output_pdf_path
            order.stamped_pdf_path = pdf_path
            if order.status == OrderFulfillmentStatus.PROCESSING_STAMP:
                order.status = OrderFulfillmentStatus.READY_FOR_DELIVERY
            db.commit()

    if not pdf_path or not os.path.isfile(pdf_path):
        raise HTTPException(status_code=425, detail="⏳ جاري ختم وتجهيز الملف... يرجى المحاولة بعد ثوانٍ")

    mat = db.query(Material).filter(Material.id == order.material_id).first()
    safe_title = re.sub(r'[\\/*?:"<>|]+', "_", (mat.title if mat else order.order_code))[:60]
    safe_teacher = re.sub(r'[\\/*?:"<>|]+', "_", (order.buyer_name_stamp or order.series_name or order.doc_name_stamp or order.order_code))[:40]
    filename = f"{safe_title}_{safe_teacher}.pdf"

    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)


# ================================================================
# 3. سلة الشراء المجمعة لعدة مذكرات معاً (Platform + Teacher APK + MiniApp + Bot)
# ================================================================

@router.post("/bundles/checkout", summary="شراء عدة مذكرات معاً ببيانات ختم موحدة (صورة تحويل / SMS تلقائي / كوينز)")
def create_multi_material_bundle_checkout(
    request: Request,
    background_tasks: BackgroundTasks,
    material_ids_json: str = Form(...),
    buyer_name_stamp: str = Form(""),
    buyer_phone_stamp: str = Form(""),
    buyer_phone_stamp_2: Optional[str] = Form(None),
    doc_name_stamp: Optional[str] = Form(None),
    series_name: Optional[str] = Form(None),
    watermark_text: Optional[str] = Form(None),
    watermark_type: str = Form("none"),
    payment_mode: str = Form("receipt_pending", description="receipt_pending | sms_auto | coins"),
    from_wallet_phone: Optional[str] = Form(None),
    to_wallet_phone: Optional[str] = Form("01097671808"),
    telegram_user_id: Optional[str] = Form(None),
    telegram_chat_id: Optional[str] = Form(None),
    receipt_image: Optional[UploadFile] = File(None),
    watermark_logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    try:
        mat_ids = list(dict.fromkeys(int(x) for x in json.loads(material_ids_json)))
    except Exception:
        raise HTTPException(status_code=400, detail="قائمة المذكرات غير صالحة")

    if not mat_ids:
        raise HTTPException(status_code=400, detail="اختر مذكرة واحدة على الأقل في السلة")

    buyer_name_stamp = (buyer_name_stamp or "").strip()
    clean_p1 = normalize_eg_phone(buyer_phone_stamp) if (buyer_phone_stamp or "").strip() else ""
    clean_p2 = normalize_eg_phone(buyer_phone_stamp_2) if (buyer_phone_stamp_2 or "").strip() else None
    clean_from = normalize_eg_phone(from_wallet_phone) if (from_wallet_phone or "").strip() else clean_p1
    clean_to = normalize_eg_phone(to_wallet_phone) if to_wallet_phone else "01097671808"

    jwt_user = get_optional_jwt_user(request, db)
    internal_key = request.headers.get("X-Internal-Service-Key")
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    has_internal_key = bool(internal_key and internal_key == expected_key)
    is_admin_actor = stamp_settings.is_admin_actor(
        role=jwt_user.role if jwt_user else None,
        telegram_user_id=telegram_user_id if has_internal_key else None,
    )

    ok_stamp, err_stamp = stamp_settings.validate_stamp_fields(
        buyer_name_stamp=buyer_name_stamp,
        buyer_phone_stamp=clean_p1 or buyer_phone_stamp,
        buyer_phone_stamp_2=clean_p2,
        series_name=series_name,
        doc_name_stamp=doc_name_stamp,
        watermark_text=watermark_text,
        is_admin=is_admin_actor,
    )
    if not ok_stamp:
        raise HTTPException(status_code=400, detail=err_stamp)

    blacklist_err = crud.check_stamp_data_against_blacklist(db, buyer_name_stamp, clean_p1, watermark_text or "")
    if blacklist_err:
        raise HTTPException(status_code=403, detail=blacklist_err)

    materials = db.query(Material).filter(Material.id.in_(mat_ids), Material.is_published == True).all()
    if len(materials) != len(mat_ids):
        raise HTTPException(status_code=404, detail="بعض المذكرات المختارة غير متوفرة")

    bot_sys_user = _get_or_create_bot_system_user(db)
    target_buyer_id = jwt_user.id if jwt_user else bot_sys_user.id

    total_price_egp = round(sum(float(m.price or m.price_coins or 0.0) for m in materials), 2)
    total_price_coins = round(sum(float(m.price_coins or m.price or 0.0) for m in materials), 2)
    bundle_code = f"BND-{secrets.token_hex(3).upper()}"
    receipt_path = save_uploaded_receipt(receipt_image)
    wm_logo_path = save_uploaded_watermark_logo(watermark_logo)

    effective_series = (series_name or doc_name_stamp or "").strip() or None
    effective_wm_type = "logo" if wm_logo_path else (watermark_type or "none")

    # ── الخيار أ: الدفع الفوري من رصيد المحفظة (الكوينز) ──
    if payment_mode == "coins":
        if not jwt_user:
            raise HTTPException(status_code=401, detail="سجّل دخولك أولاً لاستخدام رصيد المحفظة")
        if float(jwt_user.coins or 0.0) < total_price_coins:
            raise HTTPException(
                status_code=400,
                detail=f"رصيدك الحالي ({jwt_user.coins} كوينز) أقل من إجمالي السلة ({total_price_coins} كوينز)",
            )

        crud.update_user_coins(
            db=db,
            user_id=jwt_user.id,
            amount=-total_price_coins,
            tx_type=TransactionType.PURCHASE,
            description=f"شراء سلة مجمعة ({len(materials)} مذكرات) — {bundle_code}",
        )
        return _create_and_stamp_confirmed_bundle(
            db=db,
            background_tasks=background_tasks,
            materials=materials,
            bundle_code=bundle_code,
            buyer_id=target_buyer_id,
            tg_chat=telegram_chat_id or f"web_{target_buyer_id}",
            tg_user=telegram_user_id or f"web_{target_buyer_id}",
            buyer_name_stamp=buyer_name_stamp,
            clean_p1=clean_p1,
            clean_p2=clean_p2,
            doc_name_stamp=effective_series,
            series_name=effective_series,
            watermark_text=watermark_text,
            watermark_type=effective_wm_type,
            watermark_logo_path=wm_logo_path,
            clean_from=clean_from,
            clean_to=clean_to,
            receipt_path=receipt_path,
            payment_method="coins",
        )

    # ── فحص تلقائي لرادار الـ SMS (سواء اختار sms_auto أو أرفق إيصالاً وكان التحويل قد وصل بالفعل!) ──
    matched_tx = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.status == "pending")
        .filter(WalletTransaction.amount >= total_price_egp)
        .filter(
            (WalletTransaction.sender_phone == clean_from)
            | (WalletTransaction.sender_phone == (from_wallet_phone or ""))
        )
        .order_by(WalletTransaction.transaction_date.asc())
        .with_for_update()
        .first()
    )

    if matched_tx:
        matched_tx.status = "completed"
        matching_sms = (
            db.query(PaymentSMSLog)
            .filter(PaymentSMSLog.is_reconciled == False)
            .filter(PaymentSMSLog.extracted_amount == float(matched_tx.amount))
            .order_by(PaymentSMSLog.received_at.asc())
            .first()
        )
        if matching_sms:
            matching_sms.is_reconciled = True
            matching_sms.reconciled_at = datetime.utcnow()
        db.commit()

        # لو كان التحويل أكبر من إجمالي السلة والمستخدم مسجل دخول، نضيف الباقي لرصيده تلقائياً!
        surplus = round(float(matched_tx.amount) - total_price_egp, 2)
        if surplus > 0 and jwt_user:
            crud.update_user_coins(
                db, jwt_user.id, surplus, TransactionType.RECHARGE,
                f"فائض تحويل سلة {bundle_code}"
            )

        return _create_and_stamp_confirmed_bundle(
            db=db,
            background_tasks=background_tasks,
            materials=materials,
            bundle_code=bundle_code,
            buyer_id=target_buyer_id,
            tg_chat=telegram_chat_id or f"web_{target_buyer_id}",
            tg_user=telegram_user_id or f"web_{target_buyer_id}",
            buyer_name_stamp=buyer_name_stamp,
            clean_p1=clean_p1,
            clean_p2=clean_p2,
            doc_name_stamp=effective_series,
            series_name=effective_series,
            watermark_text=watermark_text,
            watermark_type=effective_wm_type,
            watermark_logo_path=wm_logo_path,
            clean_from=clean_from,
            clean_to=clean_to,
            receipt_path=receipt_path,
            payment_method="sms_auto_radar",
        )

    if payment_mode == "sms_auto":
        raise HTTPException(
            status_code=404,
            detail=f"لم يرصد الرادار بعد تحويلاً بمبلغ {total_price_egp} ج.م من الرقم {clean_from}. تأكد من الرقم أو أرفق صورة التحويل لإرساله للمراجعة الفورية.",
        )

    # ── الخيار ج: إنشاء طلب معلق (بصورة التحويل) يظهر فوراً للأدمن وفريق المرسلين! ──
    if not is_admin_actor and (not clean_from or len(clean_from) < 5):
        raise HTTPException(status_code=400, detail="أدخل الرقم المحول منه لتأكيد الطلب")

    created_codes = []
    for idx, mat in enumerate(materials, start=1):
        code = f"{bundle_code}-{idx}" if len(materials) > 1 else bundle_code
        m_price = float(mat.price or mat.price_coins or 0.0)

        p = Purchase(
            buyer_id=target_buyer_id,
            material_id=mat.id,
            buyer_name_stamp=buyer_name_stamp,
            buyer_phone_stamp=clean_p1,
            buyer_phone_stamp_2=clean_p2,
            doc_name_stamp=effective_series,
            series_name=effective_series,
            watermark_text=watermark_text,
            watermark_type=effective_wm_type,
            watermark_logo_path=wm_logo_path,
            amount_paid=m_price,
            payment_method=PaymentMethod.MONEY,
            status=PurchaseStatus.PENDING,
            transaction_id=code,
        )
        db.add(p)
        db.commit()
        db.refresh(p)

        bo = BotOrder(
            order_code=code,
            bundle_code=bundle_code,
            telegram_chat_id=str(telegram_chat_id or f"web_{target_buyer_id}"),
            telegram_user_id=str(telegram_user_id or f"web_{target_buyer_id}"),
            user_id=jwt_user.id if jwt_user else None,
            material_id=mat.id,
            purchase_id=p.id,
            buyer_name_stamp=buyer_name_stamp,
            buyer_phone_stamp=clean_p1,
            buyer_phone_stamp_2=clean_p2,
            doc_name_stamp=effective_series,
            series_name=effective_series,
            watermark_text=watermark_text,
            watermark_type=effective_wm_type,
            watermark_logo_path=wm_logo_path,
            total_amount=m_price,
            payment_method="receipt_review",
            claimed_sender_phone=clean_from,
            receiver_wallet_phone=clean_to,
            receipt_image_path=receipt_path,
            status=OrderFulfillmentStatus.PENDING_PAYMENT,
        )
        db.add(bo)
        created_codes.append(code)

    db.commit()

    titles_str = " + ".join(m.title for m in materials)
    internal_event_bus.fire_event({
        "event": "NEW_PENDING_BUNDLE",
        "bundle_code": bundle_code,
        "order_codes": created_codes,
        "buyer_name": buyer_name_stamp,
        "buyer_phone": clean_p1,
        "from_wallet": clean_from,
        "to_wallet": clean_to,
        "total_amount": total_price_egp,
        "pdfs_count": len(materials),
        "materials_summary": titles_str,
        "receipt_image_path": receipt_path,
    })

    return {
        "status": "pending_review",
        "bundle_code": bundle_code,
        "order_codes": created_codes,
        "pdfs_count": len(materials),
        "total_amount": total_price_egp,
        "message": "✅ تم إرسال سلة طلباتك وصورة التحويل للإدارة وفريق المرسلين! بمجرد المراجعة (أو وصول رسالة التحويل للرادار) سيتم ختم وإتاحة جميع مذكراتك تلقائياً.",
    }


def _create_and_stamp_confirmed_bundle(
    db: Session,
    background_tasks: BackgroundTasks,
    materials: List[Material],
    bundle_code: str,
    buyer_id: int,
    tg_chat: str,
    tg_user: str,
    buyer_name_stamp: str,
    clean_p1: str,
    clean_p2: Optional[str],
    doc_name_stamp: Optional[str],
    series_name: Optional[str] = None,
    watermark_text: Optional[str] = None,
    watermark_type: str = "none",
    watermark_logo_path: Optional[str] = None,
    clean_from: str = "",
    clean_to: str = "",
    receipt_path: Optional[str] = None,
    payment_method: str = "vodafone_cash",
) -> Dict[str, Any]:
    orders_created: List[BotOrder] = []
    purchase_ids: List[int] = []

    effective_series = (series_name or doc_name_stamp or "").strip() or None

    for idx, mat in enumerate(materials, start=1):
        code = f"{bundle_code}-{idx}" if len(materials) > 1 else bundle_code
        m_price = float(mat.price or mat.price_coins or 0.0)
        bo = BotOrder(
            order_code=code,
            bundle_code=bundle_code,
            telegram_chat_id=str(tg_chat),
            telegram_user_id=str(tg_user),
            user_id=buyer_id,
            material_id=mat.id,
            buyer_name_stamp=buyer_name_stamp,
            buyer_phone_stamp=clean_p1,
            buyer_phone_stamp_2=clean_p2,
            doc_name_stamp=effective_series,
            series_name=effective_series,
            watermark_text=watermark_text,
            watermark_type=watermark_type or "none",
            watermark_logo_path=watermark_logo_path,
            total_amount=m_price,
            payment_method=payment_method,
            claimed_sender_phone=clean_from,
            receiver_wallet_phone=clean_to,
            receipt_image_path=receipt_path,
            status=OrderFulfillmentStatus.PAYMENT_CONFIRMED,
        )
        db.add(bo)
        orders_created.append(bo)

    db.commit()

    for bo in orders_created:
        db.refresh(bo)
        _trigger_order_stamping(bo, background_tasks, db)
        if bo.purchase_id:
            purchase_ids.append(bo.purchase_id)

    return {
        "status": "completed_stamping",
        "bundle_code": bundle_code,
        "purchase_ids": purchase_ids,
        "order_codes": [o.order_code for o in orders_created],
        "pdfs_count": len(materials),
        "message": f"🎉 تم تأكيد الدفع فوراً! جاري الآن ختم وتجهيز ({len(materials)}) مذكرات ببياناتك.",
    }


# ================================================================
# 4. غرفة قيادة الأدمن الشاملة (المرسلون، العمولات، السيريالات، الطلبات، التحديثات)
# ================================================================

@router.get("/admin/overview", summary="لوحة تحكم شاملة للمرسلين والعمولات وطلبات المدرسين والسلال المعلقة وحوكمة المشرفين")
def admin_v3_overview(
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    senders = db.query(SenderAgent).order_by(SenderAgent.id.desc()).all()
    pending_teachers = (
        db.query(User)
        .filter(User.role == UserRole.CUSTOMER, User.approval_status == "pending")
        .order_by(User.id.desc())
        .all()
    )
    serials = db.query(ActivationSerial).order_by(ActivationSerial.id.desc()).limit(50).all()

    pending_orders_raw = (
        db.query(BotOrder)
        .filter(BotOrder.status == OrderFulfillmentStatus.PENDING_PAYMENT)
        .order_by(BotOrder.id.desc())
        .limit(80)
        .all()
    )
    pending_bundles = _group_bot_orders_as_bundles(pending_orders_raw, db, for_sender=None)

    materials = db.query(Material).order_by(Material.id.desc()).all()
    materials_comm = [
        {
            "id": m.id,
            "title": m.title,
            "price": float(m.price or m.price_coins or 0.0),
            "is_published": bool(m.is_published),
            "sender_commission": float(m.sender_commission) if m.sender_commission is not None else None,
        }
        for m in materials
    ]

    recent_ledger = (
        db.query(SenderLedgerEntry)
        .order_by(SenderLedgerEntry.id.desc())
        .limit(50)
        .all()
    )

    releases = _ensure_and_list_app_releases(db)

    all_admins = (
        db.query(User)
        .filter(User.role == UserRole.ADMIN)
        .order_by(User.is_founding_admin.desc(), User.id.asc())
        .all()
    )
    founding_adm = next((a for a in all_admins if bool(getattr(a, "is_founding_admin", False))), None)
    pending_co_admins = [
        a for a in all_admins
        if not bool(getattr(a, "is_founding_admin", False)) and getattr(a, "approval_status", "approved") == "pending"
    ]
    recent_sec_events = (
        db.query(AdminSecurityEvent)
        .order_by(AdminSecurityEvent.id.desc())
        .limit(25)
        .all()
    )

    return {
        "summary": {
            "total_senders": len(senders),
            "pending_senders": sum(1 for s in senders if s.status == "pending"),
            "active_senders": sum(1 for s in senders if s.status == "approved"),
            "pending_teachers": len(pending_teachers),
            "pending_bundles": len(pending_bundles),
            "pending_co_admins": len(pending_co_admins),
            "total_sender_pdfs": sum(int(s.total_pdfs_count or 0) for s in senders),
            "total_commissions_paid": round(sum(float(s.total_commission_earned or 0.0) for s in senders), 2),
            "total_net_due_to_admin": round(sum(float(s.net_due_to_admin or 0.0) for s in senders), 2),
        },
        "admin_vault": {
            "has_founding_admin": bool(founding_adm),
            "is_current_viewer_founding": bool(_admin and getattr(_admin, "is_founding_admin", False)),
            "current_admin_label": (_admin.admin_label or _admin.full_name) if _admin else "الإدارة",
            "admins": [
                {
                    "id": a.id,
                    "admin_label": a.admin_label or a.full_name,
                    "serial_code": a.serial_code,
                    "is_founding_admin": bool(getattr(a, "is_founding_admin", False)),
                    "approval_status": getattr(a, "approval_status", "approved") or "approved",
                    "is_active": bool(a.is_active),
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in all_admins
            ],
            "recent_security_events": [
                {
                    "id": ev.id,
                    "admin_id": ev.admin_id,
                    "event_type": ev.event_type,
                    "admin_label": ev.admin_label,
                    "device_id": ev.device_id,
                    "ip_address": ev.ip_address,
                    "details": ev.details,
                    "created_at": ev.created_at.isoformat() if ev.created_at else None,
                }
                for ev in recent_sec_events
            ],
        },
        "senders": [serialize_sender(s) for s in senders],
        "pending_teachers": [
            {
                "id": u.id,
                "full_name": u.full_name,
                "phone": u.phone or u.email.split("@")[0],
                "serial_code": u.serial_code,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in pending_teachers
        ],
        "serials": [
            {
                "id": sr.id,
                "serial_code": sr.serial_code,
                "target_role": sr.target_role,
                "max_uses": sr.max_uses,
                "used_count": sr.used_count,
                "bonus_coins": sr.bonus_coins,
                "note": sr.note,
                "is_active": sr.is_active,
            }
            for sr in serials
        ],
        "pending_bundles": pending_bundles,
        "materials_commissions": materials_comm,
        "recent_sender_ledger": [_serialize_ledger_entry_with_live_links(r, db) for r in recent_ledger],
        "app_releases": releases,
    }


@router.post("/admin/senders/{sender_id}/action", summary="موافقة / رفض / إيقاف / إعادة ضبط سيريال / تعديل عمولات مرسل")
def admin_manage_sender(
    sender_id: int,
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    sender = db.query(SenderAgent).filter(SenderAgent.id == sender_id).first()
    if not sender:
        raise HTTPException(status_code=404, detail="المرسل غير موجود")

    action = str(payload.get("action", "update")).strip()

    if action == "approve":
        sender.status = "approved"
        sender.approved_at = datetime.utcnow()
        if not sender.auth_token:
            sender.auth_token = f"snd_{secrets.token_hex(24)}"
    elif action == "reject":
        sender.status = "rejected"
    elif action == "suspend":
        sender.status = "suspended"
    elif action == "reset_serial":
        sender.device_id = None
        sender.serial_code = make_serial_code("SND", sender.phone)

    if "display_name" in payload and payload["display_name"] is not None:
        sender.display_name = str(payload["display_name"]).strip()
    if "wallet_number" in payload and payload["wallet_number"] is not None:
        sender.wallet_number = normalize_eg_phone(str(payload["wallet_number"]))
    if "commission_per_pdf" in payload and payload["commission_per_pdf"] is not None:
        sender.commission_per_pdf = max(0.0, float(payload["commission_per_pdf"]))
    if "commission_percent" in payload and payload["commission_percent"] is not None:
        sender.commission_percent = max(0.0, float(payload["commission_percent"]))
    if "custom_material_commissions" in payload and isinstance(payload["custom_material_commissions"], dict):
        cleaned_custom = {}
        for k, v in payload["custom_material_commissions"].items():
            if v is not None and str(v).strip() != "":
                cleaned_custom[str(k)] = max(0.0, float(v))
        sender.custom_material_commissions = cleaned_custom
    if "admin_notes" in payload:
        sender.admin_notes = str(payload.get("admin_notes") or "")

    db.commit()
    db.refresh(sender)
    return {"status": "ok", "sender": serialize_sender(sender)}


@router.post("/admin/senders/{sender_id}/settle", summary="تسجيل تسوية مالية أو تعديل رصيد مع المرسل")
def admin_settle_sender_balance(
    sender_id: int,
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    sender = db.query(SenderAgent).filter(SenderAgent.id == sender_id).first()
    if not sender:
        raise HTTPException(status_code=404, detail="المرسل غير موجود")

    amount = float(payload.get("amount", 0.0))
    notes = str(payload.get("notes", "تسوية مالية مع الإدارة")).strip()
    full_clear = bool(payload.get("clear_all", False))

    if full_clear:
        amount = float(sender.net_due_to_admin or 0.0)

    if abs(amount) < 0.01:
        raise HTTPException(status_code=400, detail="المبلغ المراد تسويته يساوي صفراً بالفعل")

    sender.net_due_to_admin = round(float(sender.net_due_to_admin or 0.0) - amount, 2)
    sender.settled_amount = round(float(sender.settled_amount or 0.0) + abs(amount), 2)

    entry = SenderLedgerEntry(
        sender_id=sender.id,
        entry_type="settlement",
        gross_amount=abs(amount),
        sender_commission=0.0,
        admin_net_delta=-amount,
        notes=notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(sender)
    return {"status": "settled", "sender": serialize_sender(sender)}


@router.put("/admin/materials/{material_id}/sender-commission", summary="تحديد عمولة المرسل الخاصة لملف/مذكرة معينة")
def admin_set_material_sender_commission(
    material_id: int,
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    mat = db.query(Material).filter(Material.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="المذكرة غير موجودة")

    raw_val = payload.get("sender_commission")
    if raw_val is None or str(raw_val).strip() == "":
        mat.sender_commission = None
    else:
        mat.sender_commission = max(0.0, float(raw_val))

    db.commit()
    return {
        "status": "ok",
        "material_id": mat.id,
        "title": mat.title,
        "sender_commission": mat.sender_commission,
    }


@router.post("/admin/teachers/{user_id}/action", summary="تفعيل أو رفض أو إعادة ضبط سيريال حساب مدرس")
def admin_manage_teacher_account(
    user_id: int,
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="حساب المدرس غير موجود")

    action = str(payload.get("action", "approve")).strip()
    if action == "approve":
        user.approval_status = "approved"
        user.is_active = True
        tp = db.query(TeacherProfile).filter(TeacherProfile.user_id == user.id).first()
        if tp:
            tp.is_verified = True
    elif action == "reject":
        user.approval_status = "rejected"
        user.is_active = False
    elif action == "reset_serial":
        crud.delete_all_user_devices(db, user.id)
        user.serial_code = make_serial_code("TCH", user.phone or user.email)

    db.commit()
    return {
        "status": "ok",
        "user_id": user.id,
        "approval_status": user.approval_status,
        "serial_code": user.serial_code,
    }


@router.post("/admin/serials/generate", summary="توليد أكواد سيريال تفعيل فورية (VIP Serials) للمدرسين")
def admin_generate_vip_serials(
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    count = min(50, max(1, int(payload.get("count", 1))))
    max_uses = max(1, int(payload.get("max_uses", 1)))
    bonus_coins = max(0.0, float(payload.get("bonus_coins", 0.0)))
    note = str(payload.get("note", "")).strip() or None

    generated = []
    for _ in range(count):
        code = make_serial_code("VIP")
        sr = ActivationSerial(
            serial_code=code,
            target_role="teacher",
            max_uses=max_uses,
            used_count=0,
            bonus_coins=bonus_coins,
            note=note,
            is_active=True,
        )
        db.add(sr)
        generated.append(code)

    db.commit()
    return {"status": "ok", "codes": generated}


@router.post("/admin/bundles/{target_code}/action", summary="موافقة أو رفض سلة طلبات معلقة من لوحة الأدمن أو البوت")
def admin_bundle_action(
    target_code: str,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    action = str(payload.get("action", "approve")).strip()
    orders = (
        db.query(BotOrder)
        .filter((BotOrder.bundle_code == target_code) | (BotOrder.order_code == target_code))
        .all()
    )
    if not orders:
        raise HTTPException(status_code=404, detail="السلة غير موجودة")

    if action == "approve":
        stamped_count = 0
        for o in orders:
            if o.status == OrderFulfillmentStatus.PENDING_PAYMENT:
                o.status = OrderFulfillmentStatus.PAYMENT_CONFIRMED
                o.payment_reference = "ADMIN_DIRECT_APPROVAL"
                db.commit()
                db.refresh(o)
                _trigger_order_stamping(o, background_tasks, db)
                stamped_count += 1
                if o.telegram_chat_id and str(o.telegram_chat_id).isdigit():
                    internal_event_bus.fire_event({
                        "event": "ORDER_ADMIN_APPROVED",
                        "order_code": o.order_code,
                        "telegram_chat_id": o.telegram_chat_id,
                    })
        return {"status": "approved", "stamped_count": stamped_count}

    elif action == "reject":
        reason = str(payload.get("reason", "لم يتم التأكد من بيانات التحويل المرفقة"))
        for o in orders:
            if o.status == OrderFulfillmentStatus.PENDING_PAYMENT:
                o.status = OrderFulfillmentStatus.CANCELLED
                o.error_message = reason
                if o.purchase_id:
                    p = db.query(Purchase).filter(Purchase.id == o.purchase_id).first()
                    if p:
                        p.status = PurchaseStatus.FAILED
                if o.telegram_chat_id and str(o.telegram_chat_id).isdigit():
                    internal_event_bus.fire_event({
                        "event": "ORDER_CANCELLED",
                        "order_code": o.order_code,
                        "telegram_chat_id": o.telegram_chat_id,
                        "reason": reason,
                    })
        db.commit()
        return {"status": "rejected"}

    raise HTTPException(status_code=400, detail="إجراء غير معروف")


# ================================================================
# 5. نظام التحديث التلقائي الهوائي المباشر للتطبيقات الثلاثة (OTA Auto-Update)
# ================================================================

def _get_apk_path_for_flavor(flavor: str) -> Optional[str]:
    root_ws = os.path.dirname(settings.BASE_DIR)
    mapping = {
        "admin": [
            os.path.join(root_ws, "Z-PACT-Admin-Radar.apk"),
            os.path.join(root_ws, "android_app", "app", "build", "outputs", "apk", "admin", "debug", "app-admin-debug.apk"),
        ],
        "teacher": [
            os.path.join(root_ws, "Z-PACT-Teacher-Store.apk"),
            os.path.join(root_ws, "android_app", "app", "build", "outputs", "apk", "teacher", "debug", "app-teacher-debug.apk"),
        ],
        "sender": [
            os.path.join(root_ws, "Z-PACT-Sender-Team.apk"),
            os.path.join(root_ws, "android_app", "app", "build", "outputs", "apk", "sender", "debug", "app-sender-debug.apk"),
        ],
    }
    for candidate in mapping.get(flavor, []):
        if os.path.isfile(candidate):
            return candidate
    return None


def _ensure_and_list_app_releases(db: Session) -> List[Dict[str, Any]]:
    defaults = [
        ("admin", 10, "3.3.2", "👑 تحديث الأمان السيادي والرادار المالي الصارم v3.3.2", "✓ تنظيف شامل لكافة الأذونات غير الضرورية والتوافق التام مع Play Protect\n✓ فلترة ذكية لكافة الرسائل الترويجية والعروض وإلغاء الرسائل العشوائية\n✓ تحديث مباشر وسلس عبر المتصفح الرسمي"),
        ("teacher", 10, "3.3.2", "🎓 تحديث منصة Z-PACT النظيفة 100% v3.3.2", "✓ إزالة أذونات الحزم لتجاوز فحص Play Protect نهائياً وبدون أي تحذيرات\n✓ أذونات إنترنت عادية فقط وآمن تماماً 100%\n✓ دعم كامل لاسم السلسلة والعلامة المائية المخصصة"),
        ("sender", 10, "3.3.2", "🚀 تحديث تطبيق فريق المرسلين النظيف v3.3.2", "✓ إزالة أذونات الحزم لتجاوز فحص Play Protect نهائياً وبدون أي تحذيرات\n✓ أذونات إنترنت عادية فقط وآمن تماماً 100%\n✓ متابعة العمولات وتأكيد الطلبات السريعة"),
    ]
    out = []
    changed = False
    for flv, vcode, vname, title, notes in defaults:
        cfg = db.query(AppReleaseConfig).filter(AppReleaseConfig.flavor == flv).first()
        if not cfg:
            cfg = AppReleaseConfig(
                flavor=flv,
                version_code=vcode,
                version_name=vname,
                title=title,
                release_notes=notes,
                force_update=True,
            )
            db.add(cfg)
            changed = True
        elif int(cfg.version_code or 0) < vcode:
            cfg.version_code = vcode
            cfg.version_name = vname
            cfg.title = title
            cfg.release_notes = notes
            cfg.force_update = True
            cfg.updated_at = datetime.utcnow()
            changed = True
    if changed:
        db.commit()

    for flv, _, _, _, _ in defaults:
        cfg = db.query(AppReleaseConfig).filter(AppReleaseConfig.flavor == flv).first()
        apk_path = _get_apk_path_for_flavor(flv)
        size_mb = round(os.path.getsize(apk_path) / (1024 * 1024), 2) if apk_path else 0.0
        out.append({
            "flavor": cfg.flavor,
            "version_code": cfg.version_code,
            "version_name": cfg.version_name,
            "title": cfg.title,
            "release_notes": cfg.release_notes,
            "force_update": bool(cfg.force_update),
            "apk_ready": bool(apk_path),
            "file_size_mb": size_mb,
            "download_path": f"/download/apk/{flv}",
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        })
    return out


@ota_router.get("/check", summary="فحص وجود تحديث هوائي جديد للتطبيق (Admin / Teacher / Sender)")
def check_for_app_update(
    request: Request,
    flavor: str = Query("teacher"),
    version_code: int = Query(1),
    db: Session = Depends(get_db),
):
    flv = flavor.strip().lower()
    if flv not in ("admin", "teacher", "sender"):
        flv = "teacher"

    _ensure_and_list_app_releases(db)
    cfg = db.query(AppReleaseConfig).filter(AppReleaseConfig.flavor == flv).first()
    apk_path = _get_apk_path_for_flavor(flv)
    base_origin = str(request.base_url).rstrip("/")

    update_available = bool(cfg and apk_path and int(cfg.version_code) > int(version_code))
    size_mb = round(os.path.getsize(apk_path) / (1024 * 1024), 2) if apk_path else 0.0

    return {
        "status": "ok",
        "flavor": flv,
        "current_version_code": version_code,
        "latest_version_code": cfg.version_code if cfg else version_code,
        "latest_version_name": cfg.version_name if cfg else "3.0.0",
        "update_available": update_available,
        "force_update": bool(cfg.force_update) if cfg else False,
        "title": cfg.title if cfg else "🚀 تحديث جديد متاح",
        "release_notes": cfg.release_notes if cfg else "",
        "file_size_mb": size_mb,
        "download_url": f"{base_origin}/download/apk/{flv}?v={cfg.version_code if cfg else 3}",
    }


@router.post("/admin/app-updates/publish", summary="إصدار وبث تحديث جديد للتطبيقات الثلاثة أو تطبيق محدد فوراً")
def admin_publish_app_update(
    payload: Dict[str, Any],
    _admin: Optional[User] = Depends(require_admin_or_internal),
    db: Session = Depends(get_db),
):
    _ensure_and_list_app_releases(db)
    target_flavor = str(payload.get("flavor", "all")).strip().lower()
    flavors = ["admin", "teacher", "sender"] if target_flavor == "all" else [target_flavor]

    new_notes = str(payload.get("release_notes", "")).strip()
    new_title = str(payload.get("title", "")).strip()
    force_flag = bool(payload.get("force_update", False))
    explicit_code = payload.get("version_code")
    explicit_name = str(payload.get("version_name", "")).strip()

    updated = []
    for flv in flavors:
        cfg = db.query(AppReleaseConfig).filter(AppReleaseConfig.flavor == flv).first()
        if not cfg:
            continue
        cfg.version_code = int(explicit_code) if explicit_code is not None else (int(cfg.version_code) + 1)
        cfg.version_name = explicit_name or f"3.{cfg.version_code - 2}.0"
        if new_title:
            cfg.title = new_title
        if new_notes:
            cfg.release_notes = new_notes
        cfg.force_update = force_flag
        cfg.updated_at = datetime.utcnow()
        updated.append(flv)

    db.commit()

    internal_event_bus.fire_event({
        "event": "APP_UPDATE_PUBLISHED",
        "flavors": updated,
        "release_notes": new_notes,
    })

    return {
        "status": "published",
        "updated_flavors": updated,
        "releases": _ensure_and_list_app_releases(db),
    }


# ================================================================
# دوال مساعدة لتجميع السلال وتنسيق روابط التحميل الحية
# ================================================================

def _group_bot_orders_as_bundles(
    orders: List[BotOrder],
    db: Session,
    for_sender: Optional[SenderAgent] = None,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    mat_cache: Dict[int, Optional[Material]] = {}

    for o in orders:
        key = o.bundle_code or o.order_code
        if o.material_id not in mat_cache:
            mat_cache[o.material_id] = db.query(Material).filter(Material.id == o.material_id).first()
        mat = mat_cache[o.material_id]
        comm = calculate_sender_commission_for_material(for_sender, mat) if (for_sender and mat) else (
            float(mat.sender_commission) if (mat and mat.sender_commission is not None) else 15.0
        )

        if key not in grouped:
            grouped[key] = {
                "target_code": key,
                "bundle_code": o.bundle_code,
                "order_codes": [],
                "buyer_name_stamp": o.buyer_name_stamp,
                "buyer_phone_stamp": o.buyer_phone_stamp,
                "buyer_phone_stamp_2": o.buyer_phone_stamp_2 or "",
                "doc_name_stamp": o.doc_name_stamp or "",
                "watermark_text": o.watermark_text or "",
                "from_wallet_phone": o.claimed_sender_phone or "",
                "to_wallet_phone": o.receiver_wallet_phone or "01097671808",
                "receipt_image_path": o.receipt_image_path,
                "payment_method": o.payment_method,
                "total_amount": 0.0,
                "estimated_sender_commission": 0.0,
                "pdfs_count": 0,
                "items": [],
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }

        g = grouped[key]
        g["order_codes"].append(o.order_code)
        g["total_amount"] = round(g["total_amount"] + float(o.total_amount or 0.0), 2)
        g["estimated_sender_commission"] = round(g["estimated_sender_commission"] + comm, 2)
        g["pdfs_count"] += 1
        g["items"].append({
            "order_code": o.order_code,
            "material_id": o.material_id,
            "title": mat.title if mat else f"مذكرة #{o.material_id}",
            "price": float(o.total_amount or 0.0),
        })

    return list(grouped.values())


def _serialize_ledger_entry_with_live_links(row: SenderLedgerEntry, db: Session) -> Dict[str, Any]:
    raw_items = row.download_items_json or []
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except Exception:
            raw_items = []

    enriched_items = []
    for item in raw_items:
        ocode = item.get("order_code")
        ready = False
        if ocode:
            bo = db.query(BotOrder).filter(BotOrder.order_code == ocode).first()
            if bo:
                if bo.stamped_pdf_path and os.path.isfile(bo.stamped_pdf_path):
                    ready = True
                elif bo.purchase_id:
                    p = db.query(Purchase).filter(Purchase.id == bo.purchase_id).first()
                    if p and p.output_pdf_path and os.path.isfile(p.output_pdf_path):
                        ready = True
        enriched_items.append({
            **item,
            "is_ready": ready,
        })

    sender_obj = row.sender
    return {
        "id": row.id,
        "sender_id": row.sender_id,
        "sender_name": (sender_obj.display_name or sender_obj.phone) if sender_obj else "",
        "sender_phone": sender_obj.phone if sender_obj else "",
        "entry_type": row.entry_type,
        "bundle_code": row.bundle_code,
        "order_code": row.order_code,
        "teacher_name": row.teacher_name,
        "teacher_phone": row.teacher_phone,
        "from_wallet_phone": row.from_wallet_phone,
        "to_wallet_phone": row.to_wallet_phone,
        "collected_to_admin_wallet": bool(row.collected_to_admin_wallet),
        "pdfs_count": row.pdfs_count,
        "materials_summary": row.materials_summary,
        "gross_amount": round(float(row.gross_amount or 0.0), 2),
        "sender_commission": round(float(row.sender_commission or 0.0), 2),
        "admin_net_delta": round(float(row.admin_net_delta or 0.0), 2),
        "receipt_image_path": row.receipt_image_path,
        "download_items": enriched_items,
        "notes": row.notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
