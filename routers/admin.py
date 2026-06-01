from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, time, timezone, timedelta
from database import get_db
from models.user import User, UserRole, LicenseType
from models.candidate import Candidate
from models.company import Company
from models.field_agent_location import FieldAgentLocationLog
from models.candidate_access_log import CandidateAccessLog
from models.institution import Institution
from services.auth import create_user, hash_password
from utils import add_flash, build_template_context, require_auth
from config import settings

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

ADMIN_ROLES = ("admin",)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    stats = {
        "total_users": db.query(func.count(User.id)).scalar(),
        "active_users": db.query(func.count(User.id)).filter(User.is_active == True).scalar(),
        "total_candidates": db.query(func.count(Candidate.id)).scalar(),
        "active_candidates": db.query(func.count(Candidate.id)).filter(
            Candidate.status == "active"
        ).scalar(),
        "total_companies": db.query(func.count(Company.id)).scalar(),
        "verified_companies": db.query(func.count(Company.id)).filter(
            Company.is_verified == True
        ).scalar(),
        "total_institutions": db.query(func.count(Institution.id)).scalar(),
    }

    role_breakdown = (
        db.query(User.role, func.count(User.id))
        .group_by(User.role)
        .all()
    )

    recent_users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .limit(5)
        .all()
    )

    recent_candidates = (
        db.query(Candidate)
        .order_by(Candidate.registered_at.desc())
        .limit(5)
        .all()
    )

    ctx = build_template_context(
        request, db,
        stats=stats,
        role_breakdown=role_breakdown,
        recent_users=recent_users,
        recent_candidates=recent_candidates,
        page_title="Admin Dashboard",
    )
    return templates.TemplateResponse("admin/dashboard.html", ctx)


# ── User Management ──────────────────────────────────────────────────────────

@router.get("/users")
async def users_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    role: str = "",
):
    user, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    query = db.query(User)
    if search:
        query = query.filter(
            (User.full_name.ilike(f"%{search}%")) |
            (User.email.ilike(f"%{search}%")) |
            (User.username.ilike(f"%{search}%"))
        )
    if role:
        query = query.filter(User.role == role)

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    users = query.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    ctx = build_template_context(
        request, db,
        users=users,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        role_filter=role,
        roles=UserRole,
        page_title="User Management",
    )
    return templates.TemplateResponse("admin/users_list.html", ctx)


@router.get("/field-agent-locations")
async def field_agent_locations(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    agent_id: str = "",
    date_from: str = "",
    date_to: str = "",
):
    user, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    query = (
        db.query(FieldAgentLocationLog, User)
        .join(User, FieldAgentLocationLog.user_id == User.id)
        .filter(User.role == UserRole.FIELD_AGENT)
    )
    if search:
        query = query.filter(
            (User.full_name.ilike(f"%{search}%")) |
            (User.email.ilike(f"%{search}%"))
        )
    if agent_id and agent_id.isdigit():
        query = query.filter(User.id == int(agent_id))

    ist = timezone(timedelta(hours=5, minutes=30))
    if date_from:
        try:
            start_date = date.fromisoformat(date_from)
            start_dt = datetime.combine(start_date, time.min, tzinfo=ist).astimezone(timezone.utc)
            query = query.filter(FieldAgentLocationLog.recorded_at >= start_dt)
        except ValueError:
            pass
    if date_to:
        try:
            end_date = date.fromisoformat(date_to)
            end_dt = datetime.combine(end_date, time.max, tzinfo=ist).astimezone(timezone.utc)
            query = query.filter(FieldAgentLocationLog.recorded_at <= end_dt)
        except ValueError:
            pass

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    rows = (
        query
        .order_by(FieldAgentLocationLog.recorded_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    logs = [{"log": log, "user": log_user} for log, log_user in rows]

    agents = (
        db.query(User)
        .filter(User.role == UserRole.FIELD_AGENT, User.is_active == True)
        .order_by(User.full_name)
        .all()
    )

    ctx = build_template_context(
        request, db,
        logs=logs,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        agents=agents,
        page_title="Field Agent Location Logs",
    )
    return templates.TemplateResponse("admin/field_agent_locations.html", ctx)


# ── Candidate Access Logs ───────────────────────────────────────────────────

@router.get("/candidate-access-logs")
async def candidate_access_logs(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
):
    user, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    query = (
        db.query(CandidateAccessLog, User, Candidate)
        .join(User, CandidateAccessLog.user_id == User.id)
        .outerjoin(Candidate, CandidateAccessLog.candidate_id == Candidate.id)
    )

    if search:
        query = query.filter(
            (User.full_name.ilike(f"%{search}%")) |
            (User.email.ilike(f"%{search}%")) |
            (Candidate.full_name.ilike(f"%{search}%")) |
            (Candidate.phone.ilike(f"%{search}%")) |
            (CandidateAccessLog.action.ilike(f"%{search}%"))
        )

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    rows = (
        query
        .order_by(CandidateAccessLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    logs = [
        {
            "log": log,
            "user": log_user,
            "candidate": candidate,
        }
        for log, log_user, candidate in rows
    ]

    ctx = build_template_context(
        request, db,
        logs=logs,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        page_title="Candidate Access Logs",
    )
    return templates.TemplateResponse("admin/candidate_access_logs.html", ctx)


@router.get("/users/new")
async def user_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir
    ctx = build_template_context(
        request, db,
        roles=UserRole,
        license_types=LicenseType,
        page_title="Add New User",
        form_action="/admin/users/new",
        edit_user=None,
    )
    return templates.TemplateResponse("admin/user_form.html", ctx)


@router.get("/users/{user_id}")
async def user_view_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    view_user = db.query(User).filter(User.id == user_id).first()
    if not view_user:
        add_flash(request, "User not found.", "danger")
        return RedirectResponse(url="/admin/users", status_code=302)

    ctx = build_template_context(
        request, db,
        view_user=view_user,
        page_title=f"User - {view_user.full_name}",
        back_url="/admin/users",
        back_label="Back to Users",
        edit_url=f"/admin/users/{user_id}/edit",
    )
    return templates.TemplateResponse("admin/user_view.html", ctx)


@router.post("/users/new")
async def user_new_post(request: Request, db: Session = Depends(get_db)):
    admin, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    form = await request.form()
    username = form.get("username", "").strip()
    email = form.get("email", "").strip()
    password = form.get("password", "")
    role = form.get("role", "")
    full_name = form.get("full_name", "").strip()

    errors = []
    if not username:
        errors.append("Username is required.")
    if not email:
        errors.append("Email is required.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not role:
        errors.append("Role is required.")
    if not full_name:
        errors.append("Full name is required.")

    if db.query(User).filter(User.username == username).first():
        errors.append("Username already exists.")
    if db.query(User).filter(User.email == email).first():
        errors.append("Email already registered.")

    if errors:
        for e in errors:
            add_flash(request, e, "danger")
        return RedirectResponse(url="/admin/users/new", status_code=302)

    from datetime import datetime
    dob_str = form.get("date_of_birth", "")
    dob = datetime.strptime(dob_str, "%Y-%m-%d") if dob_str else None
    jdate_str = form.get("joining_date", "")
    jdate = datetime.strptime(jdate_str, "%Y-%m-%d") if jdate_str else None

    exp_str = form.get("experience_years", "")
    exp = int(exp_str) if exp_str and exp_str.isdigit() else None

    create_user(
        db,
        username=username,
        email=email,
        password=password,
        seed_password=password,
        role=UserRole(role),
        license_type=LicenseType(form.get("license_type", "basic")),
        full_name=full_name,
        phone=form.get("phone", "").strip() or None,
        employee_id=form.get("employee_id", "").strip() or None,
        date_of_birth=dob,
        gender=form.get("gender", "").strip() or None,
        address=form.get("address", "").strip() or None,
        qualification=form.get("qualification", "").strip() or None,
        experience_years=exp,
        department=form.get("department", "").strip() or None,
        emergency_contact=form.get("emergency_contact", "").strip() or None,
        joining_date=jdate,
        reporting_manager=form.get("reporting_manager", "").strip() or None,
        created_by_id=admin.id,
    )

    add_flash(request, f"User '{username}' created successfully.", "success")
    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/users/{user_id}/edit")
async def user_edit_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        add_flash(request, "User not found.", "danger")
        return RedirectResponse(url="/admin/users", status_code=302)

    ctx = build_template_context(
        request, db,
        roles=UserRole,
        license_types=LicenseType,
        page_title=f"Edit User — {edit_user.full_name}",
        form_action=f"/admin/users/{user_id}/edit",
        edit_user=edit_user,
    )
    return templates.TemplateResponse("admin/user_form.html", ctx)


@router.post("/users/{user_id}/edit")
async def user_edit_post(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        add_flash(request, "User not found.", "danger")
        return RedirectResponse(url="/admin/users", status_code=302)

    form = await request.form()
    from datetime import datetime

    edit_user.full_name = form.get("full_name", edit_user.full_name).strip()
    edit_user.email = form.get("email", edit_user.email).strip()
    edit_user.phone = form.get("phone", "").strip() or None
    edit_user.license_type = LicenseType(form.get("license_type", edit_user.license_type.value))
    edit_user.is_active = form.get("is_active") == "1"
    edit_user.gender = form.get("gender", "").strip() or None
    edit_user.address = form.get("address", "").strip() or None
    edit_user.qualification = form.get("qualification", "").strip() or None
    edit_user.department = form.get("department", "").strip() or None
    edit_user.employee_id = form.get("employee_id", "").strip() or None
    edit_user.emergency_contact = form.get("emergency_contact", "").strip() or None
    edit_user.reporting_manager = form.get("reporting_manager", "").strip() or None

    exp_str = form.get("experience_years", "")
    edit_user.experience_years = int(exp_str) if exp_str and exp_str.isdigit() else None

    dob_str = form.get("date_of_birth", "")
    edit_user.date_of_birth = datetime.strptime(dob_str, "%Y-%m-%d") if dob_str else None

    jdate_str = form.get("joining_date", "")
    edit_user.joining_date = datetime.strptime(jdate_str, "%Y-%m-%d") if jdate_str else None

    new_password = form.get("new_password", "")
    if new_password:
        if len(new_password) < 8:
            add_flash(request, "New password must be at least 8 characters.", "danger")
            return RedirectResponse(url=f"/admin/users/{user_id}/edit", status_code=302)
        edit_user.hashed_password = hash_password(new_password)

    db.commit()
    add_flash(request, f"User '{edit_user.username}' updated successfully.", "success")
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/toggle-status")
async def toggle_user_status(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin, redir = require_auth(request, db, *ADMIN_ROLES)
    if redir:
        return redir

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        add_flash(request, "User not found.", "danger")
        return RedirectResponse(url="/admin/users", status_code=302)

    if target.id == admin.id:
        add_flash(request, "You cannot deactivate your own account.", "warning")
        return RedirectResponse(url="/admin/users", status_code=302)

    target.is_active = not target.is_active
    db.commit()
    status_text = "activated" if target.is_active else "deactivated"
    add_flash(request, f"User '{target.username}' has been {status_text}.", "success")
    return RedirectResponse(url="/admin/users", status_code=302)
