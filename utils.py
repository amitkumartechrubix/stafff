from typing import List, Optional
from datetime import datetime, timezone, timedelta
import json
from fastapi import Request
from starlette.responses import RedirectResponse
from sqlalchemy.orm import Session


def add_flash(request: Request, message: str, category: str = "info") -> None:
    """Add a flash message to the session."""
    messages: list = request.session.get("flash_messages", [])
    messages.append({"message": message, "category": category})
    request.session["flash_messages"] = messages


def get_flash_messages(request: Request) -> List[dict]:
    """Retrieve and clear flash messages from session."""
    messages = request.session.get("flash_messages", [])
    request.session["flash_messages"] = []
    return messages


def get_session_user_id(request: Request) -> Optional[int]:
    return request.session.get("user_id")


def set_session_user(request: Request, user) -> None:
    request.session["user_id"] = user.id
    request.session["user_role"] = user.role.value
    request.session["user_name"] = user.full_name
    request.session["last_activity"] = datetime.utcnow().isoformat()


def clear_session(request: Request) -> None:
    request.session.clear()


MASKED_PHONE_ROLES = {"manager", "recruiter", "field_agent"}


def mask_phone(value: str | None, role: str | None, can_view: bool = False) -> str:
    if not value:
        return "-"
    if can_view:
        return value
    if role not in MASKED_PHONE_ROLES:
        return value
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return value
    masked = []
    for idx in range(0, len(digits), 2):
        masked.append(digits[idx])
        masked.append("X")
    return "".join(masked)


def redirect_with_flash(url: str, request: Request, message: str, category: str = "info"):
    add_flash(request, message, category)
    return RedirectResponse(url=url, status_code=302)


def build_template_context(request: Request, db: Session, **kwargs) -> dict:
    """Build the common template context with current user and flash messages."""
    from models.user import User
    user_id = get_session_user_id(request)
    current_user = None
    if user_id:
        current_user = db.query(User).filter(
            User.id == user_id, User.is_active == True
        ).first()

    def format_ist(value: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
        if not value:
            return "-"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        ist = timezone(timedelta(hours=5, minutes=30))
        return value.astimezone(ist).strftime(fmt)

    role_value = None
    if current_user:
        role_value = current_user.role.value
    else:
        role_value = request.session.get("user_role")

    exception_ids = get_phone_view_user_ids(db)
    can_view_phone = bool(current_user and current_user.id in exception_ids)

    context = {
        "request": request,
        "current_user": current_user,
        "flash_messages": get_flash_messages(request),
        "format_ist": format_ist,
        "mask_phone": lambda value: mask_phone(value, role_value, can_view_phone),
        "can_view_phone": can_view_phone,
    }
    context.update(kwargs)
    return context


def require_auth(request: Request, db: Session, *roles: str):
    """
    Returns (user, redirect_response).
    If user not logged in or doesn't have required role, redirect_response is set.
    """
    from models.user import User
    user_id = get_session_user_id(request)
    if not user_id:
        add_flash(request, "Please log in to continue.", "warning")
        return None, RedirectResponse(url="/login", status_code=302)

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        clear_session(request)
        add_flash(request, "Session expired. Please log in again.", "warning")
        return None, RedirectResponse(url="/login", status_code=302)

    if roles and user.role.value not in roles:
        return None, RedirectResponse(url="/unauthorized", status_code=302)

    return user, None


def get_app_config(db: Session):
    from models.app_config import AppConfig

    config = db.query(AppConfig).first()
    if not config:
        config = AppConfig(session_timeout_minutes=60, phone_view_user_ids="[]")
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def get_phone_view_user_ids(db: Session) -> set[int]:
    config = get_app_config(db)
    raw = config.phone_view_user_ids or "[]"
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        values = []
    ids = set()
    for item in values:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def can_view_phone(user_id: int | None, db: Session) -> bool:
    if not user_id:
        return False
    return user_id in get_phone_view_user_ids(db)


def get_session_timeout_minutes(db: Session) -> int:
    config = get_app_config(db)
    try:
        minutes = int(config.session_timeout_minutes)
    except (TypeError, ValueError):
        return 60
    return max(1, minutes)


def ensure_contact_user(
    db: Session,
    *,
    email: str | None,
    full_name: str | None,
    phone: str | None,
    role,
    license_type,
    created_by_id: int | None = None,
    default_password: str = "Welcome@123",
    seed_password: str | None = "Welcome@123",
):
    if not email:
        return None

    from models.user import User
    from services.auth import create_user

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if full_name:
            existing.full_name = full_name
        if phone:
            existing.phone = phone
        existing.role = role
        existing.license_type = license_type
        db.commit()
        return existing

    base = email.split("@", 1)[0].strip().lower() or "user"
    base = "".join(ch for ch in base if ch.isalnum() or ch in {".", "_"})
    username = base or "user"
    suffix = 1
    while db.query(User).filter(User.username == username).first():
        username = f"{base}{suffix}"
        suffix += 1

    user = create_user(
        db,
        username=username,
        email=email,
        password=default_password,
        seed_password=seed_password,
        role=role,
        license_type=license_type,
        full_name=full_name or email,
        phone=phone,
        created_by_id=created_by_id,
    )
    return user
