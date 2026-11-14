# ================================================================
# app/routers/auth.py — نقاط نهاية التوثيق
#
# Endpoints:
#   POST /auth/register  - تسجيل مستخدم جديد
#   POST /auth/login     - تسجيل الدخول والحصول على JWT
#   GET  /auth/me        - بيانات المستخدم الحالي
# ================================================================

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.auth import create_access_token, get_current_user
from app.database import get_db
from app.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["التوثيق"])


@router.post(
    "/register",
    response_model = schemas.UserOut,
    status_code    = status.HTTP_201_CREATED,
    summary        = "تسجيل مستخدم جديد (Customer)",
)
def register(data: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    تسجيل مستخدم جديد بدور Customer.
    لو الإيميل موجود مسبقاً، بيرجع خطأ 400.
    """
    # التحقق إن الإيميل مش مستخدم
    existing = crud.get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail      = "الإيميل ده مسجل مسبقاً",
        )

    # التحقق من سيريال التفعيل VIP أو كود الدعوة لمنع التسجيل العشوائي غير المعتمد
    vip_serial = None
    if data.referral_code:
        code_up = data.referral_code.strip().upper()
        vip_serial = db.query(models.ActivationSerial).filter(
            models.ActivationSerial.serial_code == code_up,
            models.ActivationSerial.is_active == True,
        ).first()
        if vip_serial and vip_serial.used_count >= vip_serial.max_uses:
            vip_serial = None
        if not vip_serial:
            inviter = db.query(User).filter(User.referral_code == data.referral_code).first()
            if not inviter:
                raise HTTPException(
                    status_code = status.HTTP_400_BAD_REQUEST,
                    detail      = "كود السيريال أو الدعوة غير صحيح",
                )

    # إنشاء المستخدم (إذا كان معه سيريال VIP يتفعل فوراً، وإلا ينتظر موافقة الأدمن لمنع الدخول العشوائي)
    from app.routers.ecosystem_v3 import make_serial_code
    from app.routers.internal_bridge import internal_event_bus

    serial_val = make_serial_code("TCH", data.email)
    user = models.User(
        full_name       = data.full_name,
        email           = data.email,
        password_hash   = crud.hash_password(data.password),
        role            = UserRole.CUSTOMER,
        referral_code   = serial_val[-8:],
        serial_code     = serial_val,
        approval_status = "approved" if vip_serial else "pending",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if vip_serial:
        vip_serial.used_count += 1
        if vip_serial.used_count >= vip_serial.max_uses:
            vip_serial.is_active = False
        db.commit()
        if float(vip_serial.bonus_coins or 0) > 0:
            crud.update_user_coins(db, user.id, float(vip_serial.bonus_coins), models.TransactionType.RECHARGE, f"هدية سيريال {vip_serial.serial_code}")
            db.refresh(user)
    else:
        internal_event_bus.fire_event({
            "event": "TEACHER_APPROVAL_REQUEST",
            "user_id": user.id,
            "full_name": user.full_name,
            "phone": user.phone or user.email,
            "serial_code": serial_val,
        })

    return user



@router.post(
    "/login",
    response_model = schemas.TokenOut,
    summary        = "تسجيل الدخول",
)
def login(request: Request, data: schemas.UserLogin, db: Session = Depends(get_db)):
    """
    تسجيل الدخول بالإيميل وكلمة المرور + حماية الأجهزة.
    """
    user = crud.authenticate_user(db, data.email, data.password)

    if not user:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "بيانات الدخول غير صحيحة",
        )

    if getattr(user, "approval_status", "approved") not in (None, "approved"):
        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail      = "⏳ حساب الأدمن الإضافي بانتظار موافقة أول أدمن (المؤسس).",
            )
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail      = "⏳ حسابك بانتظار موافقة وتفعيل الإدارة (السيريال: " + str(user.serial_code or "") + ")",
        )

    # ── حماية الأجهزة (Device Locking) ──
    # الأدمن مسموح له بـ 5 أجهزة، الطالب بجهازين فقط
    limit = 5 if user.role == UserRole.ADMIN else 2
    
    devices = crud.get_user_devices(db, user.id)
    existing_device = next((d for d in devices if d.device_id == data.device_id), None)
    
    if existing_device:
        if existing_device.is_blocked:
            raise HTTPException(
                status_code=403,
                detail="⚠️ هذا الجهاز محظور من دخول المنصة. يرجى مراجعة الإدارة."
            )
        # تحديث بيانات الدخول الأخير
        existing_device.last_login = datetime.utcnow()
        existing_device.last_ip = request.client.host if request.client else None
        db.commit()
    else:
        # جهاز جديد
        if len(devices) >= limit:
            raise HTTPException(
                status_code=403,
                detail=f"⚠️ عذراً، لقد وصلت للحد الأقصى للأجهزة ({limit} أجهزة). "
                       f"يرجى تسجيل الدخول من أجهزتك السابقة أو التواصل مع الإدارة لطلب إعادة تعيين."
            )
        
        # تسجيل الجهاز الجديد
        crud.register_user_device(
            db=db,
            user_id=user.id,
            device_id=data.device_id,
            user_agent=request.headers.get("User-Agent"),
            ip=request.client.host if request.client else None
        )

    if user.role == UserRole.ADMIN:
        from app.routers.ecosystem_v3 import _record_and_alert_admin_login
        _record_and_alert_admin_login(
            db=db,
            admin_user=user,
            device_id=data.device_id,
            ip_addr=request.client.host if request.client else "unknown",
            is_native_admin_apk=str(data.device_id or "").upper().startswith("ZP-ADM-"),
        )

    token = create_access_token(user_id=user.id, role=user.role.value)

    return schemas.TokenOut(
        access_token = token,
        user         = schemas.UserOut.model_validate(user),
    )


@router.get(
    "/me",
    response_model = schemas.UserOut,
    summary        = "بيانات المستخدم الحالي",
)
def get_me(current_user: User = Depends(get_current_user)):
    """
    إرجاع بيانات المستخدم المسجل دخوله حالياً.
    يحتاج JWT token في الـ Authorization header.
    """
    return current_user
