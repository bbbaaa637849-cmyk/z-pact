# ================================================================
# app/routers/auth.py — نقاط نهاية التوثيق
#
# Endpoints:
#   POST /auth/register  - تسجيل مستخدم جديد
#   POST /auth/login     - تسجيل الدخول والحصول على JWT
#   GET  /auth/me        - بيانات المستخدم الحالي
# ================================================================

from collections import defaultdict
from datetime import datetime
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.auth import (
    create_access_token,
    extract_session_id_from_request,
    get_current_user,
    issue_tracked_session_token,
)
from app.database import get_db
from app.models import AdminSecurityEvent, User, UserRole, UserSession

router = APIRouter(prefix="/auth", tags=["التوثيق"])

# حماية نقطة استرداد الأدمن الأعلى من التخمين المتكرر (Rate Limiter في الذاكرة)
_RECOVERY_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_RECOVERY_MAX_FAILS = 8
_RECOVERY_WINDOW_SEC = 600


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
    limit = crud.get_effective_device_limit(user)
    
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

    token, _ = issue_tracked_session_token(
        db=db,
        user=user,
        device_id=data.device_id,
        request=request,
        login_surface="web",
    )

    user_out = schemas.UserOut.model_validate(user)
    user_out.is_supreme_admin = crud.is_supreme_admin(db, user)

    return schemas.TokenOut(
        access_token = token,
        user         = user_out,
    )


@router.get(
    "/me",
    response_model = schemas.UserOut,
    summary        = "بيانات المستخدم الحالي",
)
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    إرجاع بيانات المستخدم المسجل دخوله حالياً.
    يحتاج JWT token في الـ Authorization header.
    """
    user_out = schemas.UserOut.model_validate(current_user)
    user_out.is_supreme_admin = crud.is_supreme_admin(db, current_user)
    return user_out


def _require_supreme_admin(db: Session, current_user: User) -> User:
    """التأكد من أن الطالب هو حساب الأدمن الأعلى فقط"""
    if not crud.is_supreme_admin(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="هذه الإعدادات متاحة لحساب الأدمن الأعلى فقط",
        )
    return current_user


def _build_security_overview(db: Session, supreme_admin: User, current_sid: Optional[str]) -> schemas.AdminSecurityOverviewOut:
    sessions = crud.list_active_sessions(db, supreme_admin.id)
    devices = crud.get_user_devices(db, supreme_admin.id)
    return schemas.AdminSecurityOverviewOut(
        is_supreme_admin=True,
        effective_max_devices=crud.get_effective_device_limit(supreme_admin),
        configured_max_devices=supreme_admin.max_devices,
        registered_devices_count=sum(1 for d in devices if not d.is_blocked),
        active_sessions_count=len(sessions),
        has_recovery_password=bool(supreme_admin.recovery_password_hash),
        current_session_id=current_sid,
        sessions=[
            schemas.UserSessionOut(
                session_id=s.session_id,
                device_id=s.device_id,
                user_agent=s.user_agent,
                ip_address=s.ip_address,
                login_surface=s.login_surface or "web",
                is_active=bool(s.is_active),
                is_current=bool(current_sid and s.session_id == current_sid),
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
            )
            for s in sessions
        ],
        devices=[schemas.UserDeviceOut.model_validate(d) for d in devices],
    )


@router.get(
    "/admin/security",
    response_model=schemas.AdminSecurityOverviewOut,
    summary="جلب إعدادات الأمان والجلسات والأجهزة للأدمن الأعلى",
)
def get_supreme_admin_security(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    current_sid = extract_session_id_from_request(request)
    return _build_security_overview(db, current_user, current_sid)


@router.put(
    "/admin/security/max-devices",
    summary="تعديل الحد الأقصى للأجهزة المسموح بها للأدمن الأعلى",
)
def update_supreme_admin_max_devices(
    data: schemas.MaxDevicesUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    current_user.max_devices = int(data.max_devices)
    db.commit()

    current_sid = extract_session_id_from_request(request)
    current_device_id = None
    if current_sid:
        sess = db.query(UserSession).filter(UserSession.session_id == current_sid).first()
        if sess:
            current_device_id = sess.device_id

    removed = 0
    if data.kick_excess:
        removed = crud.enforce_device_limit_on_update(
            db=db,
            user=current_user,
            new_limit=int(data.max_devices),
            current_device_id=current_device_id,
        )

    ip_addr = request.client.host if request.client else "unknown"
    db.add(AdminSecurityEvent(
        admin_id=current_user.id,
        event_type="MAX_DEVICES_UPDATED",
        admin_label=current_user.admin_label or current_user.full_name,
        device_id=current_device_id or "web",
        ip_address=ip_addr,
        details=f"تم تحديث الحد الأقصى لأجهزة الأدمن الأعلى إلى {data.max_devices} (طرد زوائد: {removed})",
    ))
    db.commit()
    db.refresh(current_user)

    overview = _build_security_overview(db, current_user, current_sid)
    return {
        "status": "ok",
        "message": f"✅ تم تعيين الحد الأقصى للأجهزة إلى {data.max_devices} جهاز بنجاح",
        "removed_excess_devices": removed,
        "overview": overview.model_dump(mode="json"),
    }


@router.post(
    "/admin/security/recovery-password",
    summary="تعيين أو تغيير كلمة سر الاسترداد الطارئة للأدمن الأعلى",
)
def set_or_update_recovery_password(
    data: schemas.RecoveryPasswordUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    if current_user.recovery_password_hash:
        if not data.current_recovery_password or not crud.verify_password(
            data.current_recovery_password, current_user.recovery_password_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="كلمة سر الاسترداد الحالية غير صحيحة — لا يمكن تغييرها بدون معرفتها",
            )
    else:
        if not data.current_password or not crud.verify_password(
            data.current_password, current_user.password_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="كلمة المرور الحالية للحساب غير صحيحة — أدخلها لتأكيد تفعيل كلمة سر الاسترداد",
            )

    current_user.recovery_password_hash = crud.hash_password(data.new_recovery_password.strip())
    ip_addr = request.client.host if request.client else "unknown"
    db.add(AdminSecurityEvent(
        admin_id=current_user.id,
        event_type="RECOVERY_PASSWORD_CONFIGURED",
        admin_label=current_user.admin_label or current_user.full_name,
        device_id="web",
        ip_address=ip_addr,
        details="تم تعيين/تحديث كلمة سر الاسترداد الطارئة للأدمن الأعلى",
    ))
    db.commit()
    return {
        "status": "ok",
        "has_recovery_password": True,
        "message": "✅ تم حفظ كلمة سر الاسترداد الطارئة بنجاح! احتفظ بها في مكان آمن.",
    }


@router.delete(
    "/admin/security/sessions/{session_id}",
    summary="إنهاء جلسة محددة لحساب الأدمن الأعلى فوراً",
)
def terminate_supreme_admin_session(
    session_id: str,
    request: Request,
    release_device: bool = Query(True),
    block_device: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    sess = crud.revoke_session(
        db=db,
        user_id=current_user.id,
        session_id=session_id,
        release_device=release_device,
        block_device=block_device,
    )
    if not sess:
        raise HTTPException(status_code=404, detail="الجلسة غير موجودة أو منتهية مسبقاً")

    ip_addr = request.client.host if request.client else "unknown"
    db.add(AdminSecurityEvent(
        admin_id=current_user.id,
        event_type="SESSION_TERMINATED",
        admin_label=current_user.admin_label or current_user.full_name,
        device_id=sess.device_id,
        ip_address=ip_addr,
        details=f"إنهاء الجلسة {session_id[:10]} (حظر الجهاز: {block_device})",
    ))
    db.commit()
    return {
        "status": "ok",
        "session_id": session_id,
        "message": "✅ تم إنهاء الجلسة وإبطال صلاحيتها فوراً",
    }


@router.post(
    "/admin/security/sessions/revoke-others",
    summary="إنهاء جميع الجلسات الأخرى ما عدا الجلسة الحالية",
)
def revoke_other_supreme_admin_sessions(
    request: Request,
    release_devices: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    current_sid = extract_session_id_from_request(request)
    current_device_id = None
    if current_sid:
        sess = db.query(UserSession).filter(UserSession.session_id == current_sid).first()
        if sess:
            current_device_id = sess.device_id

    revoked_count = crud.revoke_all_user_sessions(
        db=db,
        user_id=current_user.id,
        except_session_id=current_sid,
        release_devices=release_devices,
        except_device_id=current_device_id,
    )
    ip_addr = request.client.host if request.client else "unknown"
    db.add(AdminSecurityEvent(
        admin_id=current_user.id,
        event_type="OTHER_SESSIONS_TERMINATED",
        admin_label=current_user.admin_label or current_user.full_name,
        device_id=current_device_id or "web",
        ip_address=ip_addr,
        details=f"تم إنهاء {revoked_count} جلسة أخرى للأدمن الأعلى",
    ))
    db.commit()
    return {
        "status": "ok",
        "revoked_count": revoked_count,
        "message": f"✅ تم إنهاء {revoked_count} جلسة أخرى وطرد الأجهزة الأخرى بنجاح",
    }


@router.delete(
    "/admin/security/devices/{device_row_id}",
    summary="حذف جهاز مسجل وإنهاء جلساته",
)
def remove_supreme_admin_device(
    device_row_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    dev = (
        db.query(models.UserDevice)
        .filter(models.UserDevice.id == device_row_id, models.UserDevice.user_id == current_user.id)
        .first()
    )
    if not dev:
        raise HTTPException(status_code=404, detail="الجهاز غير موجود")
    now = datetime.utcnow()
    db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.device_id == dev.device_id,
        UserSession.is_active == True,
    ).update({"is_active": False, "revoked_at": now}, synchronize_session=False)
    db.delete(dev)
    db.commit()
    return {"status": "ok", "message": "✅ تم حذف الجهاز وإنهاء جلساته"}


@router.post(
    "/admin/security/devices/{device_row_id}/toggle-block",
    summary="حظر أو فك حظر جهاز مسجل لحساب الأدمن الأعلى",
)
def toggle_block_supreme_admin_device(
    device_row_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_supreme_admin(db, current_user)
    dev = (
        db.query(models.UserDevice)
        .filter(models.UserDevice.id == device_row_id, models.UserDevice.user_id == current_user.id)
        .first()
    )
    if not dev:
        raise HTTPException(status_code=404, detail="الجهاز غير موجود")
    dev.is_blocked = not bool(dev.is_blocked)
    if dev.is_blocked:
        now = datetime.utcnow()
        db.query(UserSession).filter(
            UserSession.user_id == current_user.id,
            UserSession.device_id == dev.device_id,
            UserSession.is_active == True,
        ).update({"is_active": False, "revoked_at": now}, synchronize_session=False)
    db.commit()
    return {
        "status": "ok",
        "is_blocked": dev.is_blocked,
        "message": "✅ تم حظر الجهاز وإنهاء جلساته" if dev.is_blocked else "✅ تم فك حظر الجهاز",
    }


@router.post(
    "/admin/recover",
    summary="استرداد حساب الأدمن الأعلى بكلمة سر الاسترداد وتغيير كلمة المرور وطرد كافة الجلسات",
)
def recover_supreme_admin_account(
    data: schemas.AdminRecoverAccountRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    نقطة طوارئ لاسترداد حساب الأدمن الأعلى عند اختراقه أو تغيير كلمة المرور من طرف خارجي.
    تتحقق من كلمة سر الاسترداد، تعيّن كلمة المرور الجديدة، ترفع token_version،
    تنهي جميع الجلسات والأجهزة الأخرى فوراً، وتسجّل دخول المالك بجلسة نظيفة.
    """
    ip_addr = request.client.host if request.client else "unknown"
    now_ts = time.time()
    recent_fails = [t for t in _RECOVERY_ATTEMPTS[ip_addr] if now_ts - t < _RECOVERY_WINDOW_SEC]
    _RECOVERY_ATTEMPTS[ip_addr] = recent_fails
    if len(recent_fails) >= _RECOVERY_MAX_FAILS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="تم تجاوز عدد محاولات الاسترداد الخاطئة. يرجى الانتظار 10 دقائق والمحاولة مجدداً.",
        )

    supreme = crud.get_supreme_admin(db)
    if not supreme:
        raise HTTPException(status_code=404, detail="حساب الأدمن الأعلى غير موجود")

    if not supreme.recovery_password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="لم يتم تفعيل كلمة سر الاسترداد لهذا الحساب بعد",
        )

    if not crud.verify_password(data.recovery_password.strip(), supreme.recovery_password_hash):
        _RECOVERY_ATTEMPTS[ip_addr].append(now_ts)
        db.add(AdminSecurityEvent(
            admin_id=supreme.id,
            event_type="RECOVERY_ATTEMPT_FAILED",
            admin_label=supreme.admin_label or supreme.full_name,
            device_id=(data.device_id or "web-recovery")[:255],
            ip_address=ip_addr,
            details="محاولة فاشلة لاستخدام كلمة سر الاسترداد",
        ))
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="كلمة سر الاسترداد غير صحيحة",
        )

    # نجاح التحقق: مسح سجل المحاولات الفاشلة وتطهير الحساب بالكامل
    _RECOVERY_ATTEMPTS.pop(ip_addr, None)

    supreme.password_hash = crud.hash_password(data.new_password.strip())
    supreme.is_active = True
    supreme.approval_status = "approved"
    supreme.token_version = int(getattr(supreme, "token_version", 1) or 1) + 1

    # طرد كافة الجلسات السابقة وإزالة كل الأجهزة المسجلة القديمة
    now = datetime.utcnow()
    revoked_sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == supreme.id, UserSession.is_active == True)
        .update({"is_active": False, "revoked_at": now}, synchronize_session=False)
    )
    removed_devices = crud.delete_all_user_devices(db, supreme.id)

    clean_dev = (data.device_id or "web-recovery").strip()[:255] or "web-recovery"
    crud.register_user_device(
        db,
        supreme.id,
        clean_dev,
        request.headers.get("User-Agent"),
        ip_addr,
    )

    token, sess = issue_tracked_session_token(
        db=db,
        user=supreme,
        device_id=clean_dev,
        request=request,
        login_surface="web_recovery",
    )

    db.add(AdminSecurityEvent(
        admin_id=supreme.id,
        event_type="ACCOUNT_RECOVERED_AND_PURGED",
        admin_label=supreme.admin_label or supreme.full_name,
        device_id=clean_dev,
        ip_address=ip_addr,
        details=(
            f"تم استرداد حساب الأدمن الأعلى، تحديث كلمة المرور، إنهاء {revoked_sessions} جلسة، "
            f"وإزالة {removed_devices} جهاز مسجل"
        ),
    ))
    db.commit()

    try:
        from app.routers.internal_bridge import internal_event_bus
        internal_event_bus.fire_event({
            "event": "ADMIN_LOGIN_ALERT",
            "admin_id": supreme.id,
            "admin_label": f"🆘 استرداد طارئ: {supreme.admin_label or supreme.full_name}",
            "is_founding_admin": True,
            "device_id": clean_dev,
            "ip_address": ip_addr,
            "surface": "🚨 استرداد بكلمة سر الاسترداد (تم طرد جميع الجلسات والأجهزة)",
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
    except Exception:
        pass

    user_out = schemas.UserOut.model_validate(supreme)
    user_out.is_supreme_admin = True

    return {
        "status": "approved",
        "access_token": token,
        "token_type": "bearer",
        "session_id": sess.session_id,
        "revoked_sessions_count": revoked_sessions,
        "removed_devices_count": removed_devices,
        "message": "✅ تم استرداد حساب الأدمن الأعلى وتغيير كلمة المرور وطرد كافة الأجهزة والجلسات الأخرى فوراً!",
        "user": user_out.model_dump(mode="json"),
    }
