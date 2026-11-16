# ================================================================
# app/auth.py — نظام التوثيق (JWT)
#
# المسؤوليات:
#   - إنشاء JWT tokens بعد تسجيل الدخول
#   - التحقق من الـ token في كل Request محمي
#   - Dependencies جاهزة للاستخدام في الـ routers:
#       get_current_user  → أي مستخدم مسجل دخول
#       require_admin     → Admin فقط
# ================================================================

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app import crud, models
from app.database import get_db

from app.config import settings

# -----------------------------------------------------------
# إعدادات الـ JWT — تأتي من المتغيرات البيئية عبر config.py
# -----------------------------------------------------------
SECRET_KEY = settings.SECRET_KEY
ALGORITHM  = settings.ALGORITHM
TOKEN_EXPIRE_H = settings.TOKEN_EXPIRE_H

if SECRET_KEY == "change-me-in-production-please":
    from app.logger import logger
    logger.warning("⚠️ [Security Warning] Using default JWT_SECRET_KEY. Please set a strong one in environment variables.")

# HTTPBearer بيقرأ الـ token من header: Authorization: Bearer <token>
bearer_scheme = HTTPBearer()


# ================================================================
# إنشاء وفك الـ JWT
# ================================================================

def create_access_token(
    user_id: int,
    role: str,
    expire_hours: Optional[int] = None,
    session_id: Optional[str] = None,
    token_version: Optional[int] = None,
) -> str:
    """
    إنشاء JWT token جديد.
    الـ payload فيه:
      sub  → user_id (الـ subject)
      role → دور المستخدم
      tv   → token_version (لإبطال التوكنات عند تغيير كلمة السر أو الاسترداد)
      sid  → معرف الجلسة الحية (اختياري لإدارة الجلسات)
      exp  → وقت انتهاء الصلاحية
    """
    hours = expire_hours if expire_hours is not None else max(int(TOKEN_EXPIRE_H), 24 * 90)
    payload = {
        "sub":  str(user_id),
        "role": role,
        "tv":   int(token_version or 1),
        "exp":  datetime.utcnow() + timedelta(hours=hours),
        "iat":  datetime.utcnow(),  # وقت الإنشاء
    }
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """
    فك تشفير الـ JWT والتحقق من صلاحيته.
    بيرجع الـ payload لو صحيح، أو None لو منتهي أو تالف.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def extract_session_id_from_token(token: Optional[str]) -> Optional[str]:
    """استخراج معرف الجلسة sid من التوكن بدون استعلام قاعدة البيانات"""
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    sid = payload.get("sid")
    return str(sid) if sid else None


def extract_session_id_from_request(request) -> Optional[str]:
    """استخراج sid من هيدر Authorization في الطلب الحالي"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return extract_session_id_from_token(auth_header.split(" ", 1)[1].strip())
    return None


def authenticate_token(
    db: Session,
    token: str,
    touch_session: bool = True,
) -> tuple[Optional[models.User], int, Optional[str]]:
    """
    التحقق الموحد من التوكن وإصداره وحالة الجلسة والجهاز.
    يُرجع (User, 200, None) عند النجاح، أو (None, status_code, detail) عند الرفض.
    """
    payload = decode_access_token(token)
    if payload is None:
        return None, status.HTTP_401_UNAUTHORIZED, "التوكن غير صالح أو منتهي الصلاحية"

    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        return None, status.HTTP_401_UNAUTHORIZED, "التوكن غير صالح"

    user = crud.get_user_by_id(db, user_id)
    if not user or not user.is_active:
        return None, status.HTTP_401_UNAUTHORIZED, "المستخدم غير موجود أو محظور"

    if getattr(user, "approval_status", "approved") not in (None, "approved"):
        if user.role == models.UserRole.ADMIN:
            return None, status.HTTP_403_FORBIDDEN, "⏳ حساب الأدمن الإضافي بانتظار موافقة الأدمن الأول (المؤسس)."
        return None, status.HTTP_403_FORBIDDEN, "⏳ حسابك قيد المراجعة والتفعيل من قبل إدارة المنصة."

    user_tv = int(getattr(user, "token_version", 1) or 1)
    try:
        token_tv = int(payload.get("tv", 1) or 1)
    except (TypeError, ValueError):
        token_tv = 0
    if token_tv < user_tv:
        return None, status.HTTP_401_UNAUTHORIZED, "تم إنهاء صلاحية الجلسة، يرجى تسجيل الدخول مجدداً"

    sid = payload.get("sid")
    if sid:
        sess = (
            db.query(models.UserSession)
            .filter(models.UserSession.session_id == str(sid), models.UserSession.user_id == user.id)
            .first()
        )
        if not sess or not sess.is_active:
            return None, status.HTTP_401_UNAUTHORIZED, "تم إنهاء هذه الجلسة من إعدادات الحساب، يرجى تسجيل الدخول مجدداً"

        blocked_dev = (
            db.query(models.UserDevice)
            .filter(
                models.UserDevice.user_id == user.id,
                models.UserDevice.device_id == sess.device_id,
                models.UserDevice.is_blocked == True,
            )
            .first()
        )
        if blocked_dev:
            return None, status.HTTP_401_UNAUTHORIZED, "تم حظر هذا الجهاز من دخول الحساب"

        if touch_session:
            now = datetime.utcnow()
            if not sess.last_seen_at or (now - sess.last_seen_at).total_seconds() >= 60:
                sess.last_seen_at = now
                try:
                    db.commit()
                except Exception:
                    db.rollback()

    return user, 200, None


def issue_tracked_session_token(
    db: Session,
    user: models.User,
    device_id: str,
    request=None,
    login_surface: str = "web",
    expire_hours: Optional[int] = None,
):
    """إنشاء جلسة نشطة في قاعدة البيانات وإصدار JWT مرتبط بها"""
    user_agent = request.headers.get("User-Agent") if request is not None else None
    ip_addr = (request.client.host if (request is not None and request.client) else "unknown")
    sess = crud.create_user_session(
        db=db,
        user=user,
        device_id=device_id,
        user_agent=user_agent,
        ip_address=ip_addr,
        login_surface=login_surface,
    )
    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        expire_hours=expire_hours,
        session_id=sess.session_id,
        token_version=int(getattr(user, "token_version", 1) or 1),
    )
    return token, sess


# ================================================================
# FastAPI Dependencies — بتُحقن في الـ endpoints
# ================================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db:          Session                      = Depends(get_db),
) -> models.User:
    """
    Dependency — يتحقق من الـ JWT ويرجع المستخدم الحالي.
    يُستخدم في كل endpoint يحتاج تسجيل دخول:
        user: User = Depends(get_current_user)
    """
    token = credentials.credentials
    user, err_status, err_detail = authenticate_token(db, token, touch_session=True)
    if not user:
        headers = {"WWW-Authenticate": "Bearer"} if err_status == status.HTTP_401_UNAUTHORIZED else None
        raise HTTPException(
            status_code = err_status,
            detail      = err_detail or "التوكن غير صالح أو منتهي الصلاحية",
            headers     = headers,
        )
    return user


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """
    Dependency — يتحقق إن المستخدم Admin.
    يُستخدم في endpoints إدارة المتجر:
        admin: User = Depends(require_admin)
    """
    if current_user.role != models.UserRole.ADMIN:
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail      = "هذه العملية متاحة للمديرين فقط",
        )
    return current_user


async def get_current_user_ws(token: str) -> Optional[models.User]:
    """
    Dependency — يتحقق من الـ JWT ويرجع المستخدم في حالة الـ WebSocket.
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        user, _, _ = authenticate_token(db, token, touch_session=False)
        return user
    finally:
        db.close()
