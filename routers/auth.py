from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models.user import UserRole
from services.auth import authenticate_user, update_last_login
from services.auth import hash_password
from models.company import Company
from models.institution import Institution
from utils import (
    add_flash, build_template_context, set_session_user,
    clear_session, get_session_user_id
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/login")
async def login_page(request: Request, db: Session = Depends(get_db)):
    if get_session_user_id(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    ctx = build_template_context(request, db)
    return templates.TemplateResponse("auth/login.html", ctx)


@router.post("/login")
async def login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username_or_email = form.get("username", "").strip()
    password = form.get("password", "")

    if not username_or_email or not password:
        add_flash(request, "Please enter your username/email and password.", "danger")
        return RedirectResponse(url="/login", status_code=302)

    user = authenticate_user(db, username_or_email, password)
    if not user:
        add_flash(request, "Invalid credentials or account is inactive.", "danger")
        return RedirectResponse(url="/login", status_code=302)

    set_session_user(request, user)
    update_last_login(db, user)
    add_flash(request, f"Welcome back, {user.full_name}!", "success")
    return RedirectResponse(url="/dashboard", status_code=302)


# ── Logout ────────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout(request: Request):
    clear_session(request)
    add_flash(request, "You have been logged out.", "info")
    return RedirectResponse(url="/login", status_code=302)


# ── Self-Registration: Company ────────────────────────────────────────────────

@router.get("/register/company")
async def register_company_page(request: Request, db: Session = Depends(get_db)):
    ctx = build_template_context(request, db)
    return templates.TemplateResponse("auth/register_company.html", ctx)


@router.post("/register/company")
async def register_company_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    name = form.get("name", "").strip()
    if not name:
        add_flash(request, "Company name is required.", "danger")
        return RedirectResponse(url="/register/company", status_code=302)

    existing = db.query(Company).filter(Company.name == name).first()
    if existing:
        add_flash(request, "A company with this name is already registered.", "warning")
        return RedirectResponse(url="/register/company", status_code=302)

    company = Company(
        name=name,
        industry=form.get("industry", "").strip() or None,
        location=form.get("location", "").strip() or None,
        address=form.get("address", "").strip() or None,
        city=form.get("city", "").strip() or None,
        state=form.get("state", "").strip() or None,
        pincode=form.get("pincode", "").strip() or None,
        website=form.get("website", "").strip() or None,
        description=form.get("description", "").strip() or None,
        technical_contact_name=form.get("technical_contact_name", "").strip() or None,
        technical_contact_email=form.get("technical_contact_email", "").strip() or None,
        technical_contact_phone=form.get("technical_contact_phone", "").strip() or None,
        hr_contact_name=form.get("hr_contact_name", "").strip() or None,
        hr_contact_email=form.get("hr_contact_email", "").strip() or None,
        hr_contact_phone=form.get("hr_contact_phone", "").strip() or None,
    )
    db.add(company)
    db.commit()

    add_flash(
        request,
        "Company registration submitted successfully! Our team will verify and activate your account.",
        "success"
    )
    return RedirectResponse(url="/login", status_code=302)


# ── Self-Registration: Institution ────────────────────────────────────────────

@router.get("/register/institution")
async def register_institution_page(request: Request, db: Session = Depends(get_db)):
    ctx = build_template_context(request, db)
    return templates.TemplateResponse("auth/register_institution.html", ctx)


@router.post("/register/institution")
async def register_institution_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    name = form.get("name", "").strip()
    if not name:
        add_flash(request, "Institution name is required.", "danger")
        return RedirectResponse(url="/register/institution", status_code=302)

    institution = Institution(
        name=name,
        institution_type=form.get("institution_type", "").strip() or None,
        location=form.get("location", "").strip() or None,
        address=form.get("address", "").strip() or None,
        city=form.get("city", "").strip() or None,
        state=form.get("state", "").strip() or None,
        pincode=form.get("pincode", "").strip() or None,
        courses_offered=form.get("courses_offered", "").strip() or None,
        years_of_operation=int(form.get("years_of_operation") or 0) or None,
        affiliation=form.get("affiliation", "").strip() or None,
        poc_name=form.get("poc_name", "").strip() or None,
        poc_designation=form.get("poc_designation", "").strip() or None,
        poc_email=form.get("poc_email", "").strip() or None,
        poc_phone=form.get("poc_phone", "").strip() or None,
    )
    db.add(institution)
    db.commit()

    add_flash(
        request,
        "Institution registration submitted! Our team will verify and activate your account.",
        "success"
    )
    return RedirectResponse(url="/login", status_code=302)


# ── Unauthorized ──────────────────────────────────────────────────────────────

@router.get("/unauthorized")
async def unauthorized(request: Request, db: Session = Depends(get_db)):
    ctx = build_template_context(request, db)
    return templates.TemplateResponse("errors/403.html", ctx, status_code=403)
