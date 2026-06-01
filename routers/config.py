from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime
import json
from html.parser import HTMLParser
import re
import urllib.request
import urllib.parse
from database import get_db
from models.company import Company, CompanyLocation, CompanyContact
from models.user import User, UserRole, LicenseType
from models.institution import Institution
from models.email_config import EmailConfig, EmailRule
from models.recruitment_source import RecruitmentSource, SourceType, SOURCE_TYPE_LABELS
from models.job_profile import JobProfile
from models.interview import InterviewRound, InterviewQuestion
from models.job_posting import JobPosting
from services.email_service import test_imap_connection, test_smtp_connection
from config import settings
from utils import add_flash, build_template_context, require_auth, ensure_contact_user, get_app_config
from utils import get_phone_view_user_ids

router = APIRouter(prefix="/config")
templates = Jinja2Templates(directory="templates")

ADMIN_EMPLOYER = ("admin", "employer")
ADMIN_ONLY = ("admin",)


def _safe_page(value) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page > 0 else 1


def _generate_company_summary(name: str, industry: str | None = None) -> str:
    industry_text = (industry or "Manufacturing").strip()
    base = (
        f"{name} is a company operating in the {industry_text} sector with a focus on "
        "reliable delivery, product quality, and operational excellence. "
        "The organization emphasizes safety, compliance, and continuous improvement across "
        "its facilities and partner ecosystem. "
        "Its teams collaborate across engineering, production, supply chain, and service "
        "functions to deliver consistent outcomes for customers and stakeholders. "
        "The company prioritizes responsible growth, transparent governance, and the "
        "development of people and capabilities."
    )

    tail = (
        f"{name} invests in process discipline, modern tooling, and performance tracking to "
        "improve efficiency, quality, and customer satisfaction. "
        "It supports workforce readiness through structured onboarding, training, and "
        "clear role expectations. "
        "Across its operations, the company aims to build long-term relationships with "
        "clients, suppliers, and the communities it serves."
    )

    words = (base + " " + tail).split()
    if len(words) < 150:
        filler = (
            "The company values integrity, accountability, and continuous improvement in every "
            "engagement and internal process."
        ).split()
        while len(words) < 150:
            words.extend(filler)

    summary = " ".join(words[:150])
    return summary


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _extract_text_from_url(url: str) -> str:
    if not url.startswith("http://") and not url.startswith("https://"):
        raise ValueError("Source URL must start with http:// or https://")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (StaffIndiaBot)"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        html = response.read().decode("utf-8", errors="ignore")

    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _summarize_text(text: str, name: str, tone: str | None = None) -> str:
    words = text.split()
    if not words:
        return _generate_company_summary(name)

    name_lower = name.lower()
    start_idx = 0
    for i in range(0, max(0, len(words) - 10)):
        window = " ".join(words[i:i + 10]).lower()
        if name_lower in window:
            start_idx = i
            break

    excerpt = words[start_idx:start_idx + 150]
    summary = " ".join(excerpt)

    employment_sentence = (
        " It emphasizes responsible employment, skills development, and safe workplaces for its teams."
    )
    if tone and "employment" in tone.lower() and len(summary.split()) < 150:
        summary = (summary + employment_sentence).strip()

    summary_words = summary.split()
    if len(summary_words) > 150:
        summary = " ".join(summary_words[:150])

    return summary


def _ensure_interview_rounds(db: Session) -> None:
    existing = {r.round_number: r for r in db.query(InterviewRound).all()}
    created = False
    for number in (1, 2):
        if number not in existing:
            db.add(InterviewRound(round_number=number, title=f"Round {number}", allow_random=True))
            created = True
    if created:
        db.commit()


# ── App Configuration ─────────────────────────────────────────────────────────

@router.get("/app")
async def app_config_page(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_ONLY)
    if redir:
        return redir

    config = get_app_config(db)
    users = db.query(User).filter(User.is_active == True).order_by(User.full_name).all()
    selected_ids = get_phone_view_user_ids(db)

    ctx = build_template_context(
        request, db,
        config=config,
        phone_view_users=users,
        phone_view_selected=selected_ids,
        page_title="App Configuration",
    )
    return templates.TemplateResponse("config/app.html", ctx)


@router.post("/app")
async def app_config_save(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_ONLY)
    if redir:
        return redir

    form = await request.form()
    timeout_value = form.get("session_timeout_minutes", "")
    try:
        timeout_minutes = int(timeout_value)
    except (TypeError, ValueError):
        add_flash(request, "Session timeout must be a whole number of minutes.", "danger")
        return RedirectResponse(url="/config/app", status_code=302)

    if timeout_minutes < 1:
        add_flash(request, "Session timeout must be at least 1 minute.", "danger")
        return RedirectResponse(url="/config/app", status_code=302)

    config = get_app_config(db)
    config.session_timeout_minutes = timeout_minutes
    db.commit()

    add_flash(request, "App configuration saved.", "success")
    return RedirectResponse(url="/config/app", status_code=302)


# ── Company Configuration ─────────────────────────────────────────────────────

def _save_company_from_form(form, company: Company, user: User, db: Session) -> None:
    company.name = form.get("name", "").strip()
    company.industry = form.get("industry", "").strip() or None
    company.location = form.get("location", "").strip() or None
    company.address = form.get("address", "").strip() or None
    company.city = form.get("city", "").strip() or None
    company.state = form.get("state", "").strip() or None
    company.pincode = form.get("pincode", "").strip() or None
    company.website = form.get("website", "").strip() or None
    company.description = form.get("description", "").strip() or None
    company.technical_contact_name = form.get("technical_contact_name", "").strip() or None
    company.technical_contact_email = form.get("technical_contact_email", "").strip() or None
    company.technical_contact_phone = form.get("technical_contact_phone", "").strip() or None
    company.hr_contact_name = form.get("hr_contact_name", "").strip() or None
    company.hr_contact_email = form.get("hr_contact_email", "").strip() or None
    company.hr_contact_phone = form.get("hr_contact_phone", "").strip() or None
    company.gst_number = form.get("gst_number", "").strip() or None
    company.cin_number = form.get("cin_number", "").strip() or None
    company.is_active = True
    if not company.created_by_id:
        company.created_by_id = user.id

    db.flush()

    city_list = form.getlist("location_city")
    state_list = form.getlist("location_state")
    address_list = form.getlist("location_address")

    db.query(CompanyLocation).filter(CompanyLocation.company_id == company.id).delete()
    for city, state, address in zip(city_list, state_list, address_list):
        city = city.strip()
        state = state.strip()
        address = address.strip()
        if not (city or state or address):
            continue
        db.add(CompanyLocation(company_id=company.id, city=city or None, state=state or None, address=address or None))

    db.query(CompanyContact).filter(CompanyContact.company_id == company.id).delete()
    tech_names = form.getlist("tech_contact_name")
    tech_emails = form.getlist("tech_contact_email")
    tech_phones = form.getlist("tech_contact_phone")
    hr_names = form.getlist("hr_contact_name_extra")
    hr_emails = form.getlist("hr_contact_email_extra")
    hr_phones = form.getlist("hr_contact_phone_extra")

    for name, email, phone in zip(tech_names, tech_emails, tech_phones):
        name = (name or "").strip()
        email = (email or "").strip()
        phone = (phone or "").strip()
        if not (name or email or phone):
            continue
        db.add(
            CompanyContact(
                company_id=company.id,
                contact_type="technical",
                name=name or None,
                email=email or None,
                phone=phone or None,
            )
        )

    for name, email, phone in zip(hr_names, hr_emails, hr_phones):
        name = (name or "").strip()
        email = (email or "").strip()
        phone = (phone or "").strip()
        if not (name or email or phone):
            continue
        db.add(
            CompanyContact(
                company_id=company.id,
                contact_type="hr",
                name=name or None,
                email=email or None,
                phone=phone or None,
            )
        )

    ensure_contact_user(
        db,
        email=company.technical_contact_email,
        full_name=company.technical_contact_name,
        phone=company.technical_contact_phone,
        role=UserRole.EMPLOYER,
        license_type=LicenseType.PROFESSIONAL,
        created_by_id=user.id,
    )
    ensure_contact_user(
        db,
        email=company.hr_contact_email,
        full_name=company.hr_contact_name,
        phone=company.hr_contact_phone,
        role=UserRole.EMPLOYER,
        license_type=LicenseType.PROFESSIONAL,
        created_by_id=user.id,
    )

    for name, email, phone in zip(tech_names, tech_emails, tech_phones):
        ensure_contact_user(
            db,
            email=(email or "").strip() or None,
            full_name=(name or "").strip() or None,
            phone=(phone or "").strip() or None,
            role=UserRole.EMPLOYER,
            license_type=LicenseType.PROFESSIONAL,
            created_by_id=user.id,
        )
    for name, email, phone in zip(hr_names, hr_emails, hr_phones):
        ensure_contact_user(
            db,
            email=(email or "").strip() or None,
            full_name=(name or "").strip() or None,
            phone=(phone or "").strip() or None,
            role=UserRole.EMPLOYER,
            license_type=LicenseType.PROFESSIONAL,
            created_by_id=user.id,
        )


@router.get("/company")
async def company_portal(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    companies = (
        db.query(Company)
        .filter(Company.is_active == True)
        .order_by(Company.created_at.desc())
        .all()
    )
    ctx = build_template_context(
        request, db,
        companies=companies,
        total=len(companies),
        page_title="Company Configuration",
    )
    return templates.TemplateResponse("config/company_portal.html", ctx)


@router.get("/company/new")
async def company_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    ctx = build_template_context(
        request, db,
        company=None,
        locations=[],
        tech_contacts=[],
        hr_contacts=[],
        form_action="/config/company/new",
        page_title="Register Company",
        back_url="/config/company",
    )
    return templates.TemplateResponse("config/company.html", ctx)


@router.post("/company/new")
async def company_new_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    form = await request.form()
    company = Company(created_by_id=user.id)
    db.add(company)
    _save_company_from_form(form, company, user, db)
    db.commit()

    add_flash(request, "Company registered successfully.", "success")
    return RedirectResponse(url="/config/company", status_code=302)


@router.get("/company/{cid}/edit")
async def company_edit_form(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    company = db.query(Company).filter(Company.id == cid, Company.is_active == True).first()
    if not company:
        add_flash(request, "Company not found.", "danger")
        return RedirectResponse(url="/config/company", status_code=302)

    tech_contacts = [c for c in company.contacts if c.contact_type == "technical"]
    hr_contacts = [c for c in company.contacts if c.contact_type == "hr"]
    ctx = build_template_context(
        request, db,
        company=company,
        locations=company.locations,
        tech_contacts=tech_contacts,
        hr_contacts=hr_contacts,
        form_action=f"/config/company/{cid}/edit",
        page_title="Edit Company",
        back_url="/config/company",
    )
    return templates.TemplateResponse("config/company.html", ctx)


@router.post("/company/{cid}/edit")
async def company_edit_post(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    company = db.query(Company).filter(Company.id == cid, Company.is_active == True).first()
    if not company:
        add_flash(request, "Company not found.", "danger")
        return RedirectResponse(url="/config/company", status_code=302)

    form = await request.form()
    _save_company_from_form(form, company, user, db)
    db.commit()

    add_flash(request, "Company configuration saved successfully.", "success")
    return RedirectResponse(url="/config/company", status_code=302)


# ── Job Profiles ─────────────────────────────────────────────────────────────

@router.get("/job-profiles")
async def job_profiles(request: Request, db: Session = Depends(get_db), page: int = 1, search: str = ""):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    query = db.query(JobProfile)
    if search:
        query = query.filter(
            (JobProfile.role_title.ilike(f"%{search}%")) |
            (JobProfile.designation.ilike(f"%{search}%")) |
            (JobProfile.industry.ilike(f"%{search}%")) |
            (JobProfile.skills.ilike(f"%{search}%"))
        )

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    profiles = (
        query.order_by(JobProfile.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    ctx = build_template_context(
        request, db,
        profiles=profiles,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        page_title="Job Profiles",
        form_action="/config/job-profiles",
        edit_profile=None,
    )
    return templates.TemplateResponse("config/job_profiles.html", ctx)


@router.post("/job-profiles")
async def job_profiles_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    form = await request.form()
    designation = form.get("designation", "").strip()
    if not designation:
        add_flash(request, "Designation is required.", "danger")
        return RedirectResponse(url="/config/job-profiles", status_code=302)

    profile = JobProfile(
        role_title=designation,
        designation=designation,
        industry=form.get("industry", "").strip() or None,
        min_experience=int(form.get("min_experience") or 0) or None,
        max_experience=int(form.get("max_experience") or 0) or None,
        skills=form.get("skills", "").strip() or None,
        jd_summary=form.get("jd_summary", "").strip() or None,
        technical_responsibilities=form.get("technical_responsibilities", "").strip() or None,
        functional_responsibilities=form.get("functional_responsibilities", "").strip() or None,
        is_active=True,
        created_by_id=user.id,
    )
    db.add(profile)
    db.commit()

    add_flash(request, "Job profile created.", "success")
    return RedirectResponse(url="/config/job-profiles", status_code=302)


@router.get("/job-profiles/{pid}/edit")
async def job_profiles_edit(pid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    profile = db.query(JobProfile).filter(JobProfile.id == pid).first()
    if not profile:
        add_flash(request, "Job profile not found.", "danger")
        return RedirectResponse(url="/config/job-profiles", status_code=302)

    ctx = build_template_context(
        request, db,
        profiles=[],
        total=0,
        page=1,
        per_page=settings.ITEMS_PER_PAGE,
        total_pages=1,
        search="",
        page_title=f"Edit Job Profile — {profile.designation or profile.role_title}",
        form_action=f"/config/job-profiles/{pid}/edit",
        edit_profile=profile,
    )
    return templates.TemplateResponse("config/job_profiles.html", ctx)


@router.post("/job-profiles/{pid}/edit")
async def job_profiles_edit_post(pid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    profile = db.query(JobProfile).filter(JobProfile.id == pid).first()
    if not profile:
        add_flash(request, "Job profile not found.", "danger")
        return RedirectResponse(url="/config/job-profiles", status_code=302)

    form = await request.form()
    designation = form.get("designation", "").strip()
    if not designation:
        add_flash(request, "Designation is required.", "danger")
        return RedirectResponse(url=f"/config/job-profiles/{pid}/edit", status_code=302)
    profile.role_title = designation
    profile.designation = designation
    profile.industry = form.get("industry", "").strip() or None
    profile.min_experience = int(form.get("min_experience") or 0) or None
    profile.max_experience = int(form.get("max_experience") or 0) or None
    profile.skills = form.get("skills", "").strip() or None
    profile.jd_summary = form.get("jd_summary", "").strip() or None
    profile.technical_responsibilities = form.get("technical_responsibilities", "").strip() or None
    profile.functional_responsibilities = form.get("functional_responsibilities", "").strip() or None
    profile.is_active = form.get("is_active") == "1"
    db.commit()

    add_flash(request, "Job profile updated.", "success")
    return RedirectResponse(url="/config/job-profiles", status_code=302)


@router.post("/job-profiles/{pid}/toggle")
async def job_profiles_toggle(pid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    profile = db.query(JobProfile).filter(JobProfile.id == pid).first()
    if not profile:
        add_flash(request, "Job profile not found.", "danger")
        return RedirectResponse(url="/config/job-profiles", status_code=302)

    profile.is_active = not profile.is_active
    db.commit()
    return RedirectResponse(url="/config/job-profiles", status_code=302)


@router.post("/job-profiles/{pid}/delete")
async def job_profiles_delete(pid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    profile = db.query(JobProfile).filter(JobProfile.id == pid).first()
    if profile:
        db.delete(profile)
        db.commit()
    return RedirectResponse(url="/config/job-profiles", status_code=302)


# ── Interview Questions ─────────────────────────────────────────────────────

@router.get("/interview-questions")
async def interview_questions(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    _ensure_interview_rounds(db)
    rounds = db.query(InterviewRound).order_by(InterviewRound.round_number).all()

    ctx = build_template_context(
        request, db,
        rounds=rounds,
        page_title="Interview Questions",
    )
    return templates.TemplateResponse("config/interview_questions.html", ctx)


@router.post("/interview-questions/round/{rid}/toggle-random")
async def interview_toggle_random(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    round_obj = db.query(InterviewRound).filter(InterviewRound.id == rid).first()
    if not round_obj:
        add_flash(request, "Interview round not found.", "danger")
        return RedirectResponse(url="/config/interview-questions", status_code=302)

    round_obj.allow_random = not round_obj.allow_random
    db.commit()
    return RedirectResponse(url="/config/interview-questions", status_code=302)


@router.post("/interview-questions/round/{rid}/questions/add")
async def interview_add_question(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    round_obj = db.query(InterviewRound).filter(InterviewRound.id == rid).first()
    if not round_obj:
        add_flash(request, "Interview round not found.", "danger")
        return RedirectResponse(url="/config/interview-questions", status_code=302)

    form = await request.form()
    question_text = form.get("question_text", "").strip()
    if not question_text:
        add_flash(request, "Question text is required.", "danger")
        return RedirectResponse(url="/config/interview-questions", status_code=302)

    db.add(InterviewQuestion(round_id=rid, question_text=question_text, is_active=True))
    db.commit()
    return RedirectResponse(url="/config/interview-questions", status_code=302)


@router.post("/interview-questions/question/{qid}/delete")
async def interview_delete_question(qid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    question = db.query(InterviewQuestion).filter(InterviewQuestion.id == qid).first()
    if question:
        db.delete(question)
        db.commit()
    return RedirectResponse(url="/config/interview-questions", status_code=302)


@router.get("/company/ai-summary")
async def company_ai_summary(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    name = request.query_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Company name is required."}, status_code=400)

    industry = request.query_params.get("industry", "").strip() or None
    source_url = request.query_params.get("source_url", "").strip()
    tone = request.query_params.get("tone", "").strip() or None

    if source_url:
        try:
            text = _extract_text_from_url(source_url)
            summary = _summarize_text(text, name, tone)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    else:
        summary = _generate_company_summary(name, industry)

    return {"summary": summary}


@router.get("/seed-contact-users")
async def seed_contact_users(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    created = []
    updated = []

    companies = db.query(Company).filter(Company.is_active == True).all()
    for company in companies:
        for email, name, phone in [
            (company.technical_contact_email, company.technical_contact_name, company.technical_contact_phone),
            (company.hr_contact_email, company.hr_contact_name, company.hr_contact_phone),
        ]:
            if not email:
                continue
            existing = db.query(User).filter(User.email == email).first()
            ensure_contact_user(
                db,
                email=email,
                full_name=name,
                phone=phone,
                role=UserRole.EMPLOYER,
                license_type=LicenseType.PROFESSIONAL,
                created_by_id=user.id,
            )
            if existing:
                updated.append(email)
            else:
                created.append(email)

    institutions = db.query(Institution).filter(Institution.is_active == True).all()
    for inst in institutions:
        email = inst.poc_email
        if not email:
            continue
        existing = db.query(User).filter(User.email == email).first()
        ensure_contact_user(
            db,
            email=email,
            full_name=inst.poc_name,
            phone=inst.poc_phone,
            role=UserRole.INSTITUTION,
            license_type=LicenseType.PROFESSIONAL,
            created_by_id=user.id,
        )
        if existing:
            updated.append(email)
        else:
            created.append(email)

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "counts": {"created": len(created), "updated": len(updated)},
    }


# ── Email Configuration ───────────────────────────────────────────────────────

@router.get("/email")
async def email_config(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    inbound_page = _safe_page(request.query_params.get("inbound_page", 1))
    outbound_page = _safe_page(request.query_params.get("outbound_page", 1))
    per_page = settings.ITEMS_PER_PAGE

    config = db.query(EmailConfig).filter(EmailConfig.is_active == True).first()
    inbound_rules = []
    outbound_rules = []
    inbound_total = 0
    outbound_total = 0
    if config:
        inbound_query = (
            db.query(EmailRule)
            .filter(EmailRule.config_id == config.id, EmailRule.rule_type == "inbound")
            .order_by(EmailRule.priority)
        )
        outbound_query = (
            db.query(EmailRule)
            .filter(EmailRule.config_id == config.id, EmailRule.rule_type == "outbound")
            .order_by(EmailRule.priority)
        )

        inbound_total = inbound_query.count()
        outbound_total = outbound_query.count()

        inbound_rules = (
            inbound_query
            .offset((inbound_page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        outbound_rules = (
            outbound_query
            .offset((outbound_page - 1) * per_page)
            .limit(per_page)
            .all()
        )

    ctx = build_template_context(
        request, db,
        config=config,
        inbound_rules=inbound_rules,
        outbound_rules=outbound_rules,
        inbound_page=inbound_page,
        outbound_page=outbound_page,
        inbound_total=inbound_total,
        outbound_total=outbound_total,
        per_page=per_page,
        inbound_total_pages=(inbound_total + per_page - 1) // per_page if per_page else 1,
        outbound_total_pages=(outbound_total + per_page - 1) // per_page if per_page else 1,
        page_title="Email Configuration",
    )
    return templates.TemplateResponse("config/email.html", ctx)


@router.post("/email")
async def email_config_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    form = await request.form()
    config = db.query(EmailConfig).filter(EmailConfig.is_active == True).first()
    if not config:
        config = EmailConfig()
        db.add(config)

    config.config_name = form.get("config_name", "Primary").strip()
    config.imap_host = form.get("imap_host", "").strip() or None
    config.imap_port = int(form.get("imap_port") or 993)
    config.imap_username = form.get("imap_username", "").strip() or None
    if form.get("imap_password"):
        config.imap_password = form.get("imap_password").strip()
    selected_ids = []
    for raw in form.getlist("phone_view_user_ids"):
        try:
            selected_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    config.imap_use_ssl = form.get("imap_use_ssl") == "1"
    config.imap_folder = form.get("imap_folder", "INBOX").strip()
    config.smtp_host = form.get("smtp_host", "").strip() or None
    config.phone_view_user_ids = json.dumps(selected_ids)
    config.smtp_port = int(form.get("smtp_port") or 587)
    config.smtp_username = form.get("smtp_username", "").strip() or None
    if form.get("smtp_password"):
        config.smtp_password = form.get("smtp_password").strip()
    config.smtp_use_tls = form.get("smtp_use_tls") == "1"
    config.smtp_from_name = form.get("smtp_from_name", "").strip() or None
    config.smtp_from_email = form.get("smtp_from_email", "").strip() or None
    db.commit()

    add_flash(request, "Email configuration saved.", "success")
    return RedirectResponse(url="/config/email", status_code=302)


@router.post("/email/test-imap")
async def test_imap(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    config = db.query(EmailConfig).filter(EmailConfig.is_active == True).first()
    if not config:
        add_flash(request, "No email configuration found. Please save settings first.", "warning")
        return RedirectResponse(url="/config/email", status_code=302)

    ok, msg = test_imap_connection(config)
    config.last_imap_test = datetime.utcnow()
    config.imap_test_status = "ok" if ok else "fail"
    db.commit()

    add_flash(request, msg, "success" if ok else "danger")
    return RedirectResponse(url="/config/email", status_code=302)


@router.post("/email/test-smtp")
async def test_smtp(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    config = db.query(EmailConfig).filter(EmailConfig.is_active == True).first()
    if not config:
        add_flash(request, "No email configuration found. Please save settings first.", "warning")
        return RedirectResponse(url="/config/email", status_code=302)

    ok, msg = test_smtp_connection(config)
    config.last_smtp_test = datetime.utcnow()
    config.smtp_test_status = "ok" if ok else "fail"
    db.commit()

    add_flash(request, msg, "success" if ok else "danger")
    return RedirectResponse(url="/config/email", status_code=302)


@router.post("/email/rules/add")
async def add_email_rule(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    form = await request.form()
    config = db.query(EmailConfig).filter(EmailConfig.is_active == True).first()
    if not config:
        add_flash(request, "Save email config first before adding rules.", "warning")
        return RedirectResponse(url="/config/email", status_code=302)

    rule = EmailRule(
        config_id=config.id,
        rule_type=form.get("rule_type", "inbound"),
        rule_name=form.get("rule_name", "").strip(),
        condition=form.get("condition", "").strip() or None,
        action=form.get("action", "").strip() or None,
        priority=int(form.get("priority") or 10),
    )
    db.add(rule)
    db.commit()

    add_flash(request, "Email rule added.", "success")
    return RedirectResponse(url="/config/email", status_code=302)


@router.post("/email/rules/{rule_id}/delete")
async def delete_email_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    rule = db.query(EmailRule).filter(EmailRule.id == rule_id).first()
    if rule:
        db.delete(rule)
        db.commit()
        add_flash(request, "Rule deleted.", "success")
    return RedirectResponse(url="/config/email", status_code=302)


# ── Recruitment Sources ───────────────────────────────────────────────────────

@router.get("/recruitment-sources")
async def recruitment_sources(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    show_inactive = request.query_params.get("show_inactive") == "1"
    page = _safe_page(request.query_params.get("page", 1))
    per_page = settings.ITEMS_PER_PAGE
    query = db.query(RecruitmentSource)
    if not show_inactive:
        query = query.filter(RecruitmentSource.is_active == True)

    query = query.order_by(
        RecruitmentSource.source_type, RecruitmentSource.name
    )
    total = query.count()
    sources = (
        query
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    ctx = build_template_context(
        request, db,
        sources=sources,
        source_types=SourceType,
        source_type_labels=SOURCE_TYPE_LABELS,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page if per_page else 1,
        show_inactive=show_inactive,
        page_title="Recruitment Sources",
    )
    return templates.TemplateResponse("config/recruitment_sources.html", ctx)


@router.post("/recruitment-sources/add")
async def add_recruitment_source(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    form = await request.form()
    name = form.get("name", "").strip()
    source_type = form.get("source_type", "")
    if not name or not source_type:
        add_flash(request, "Name and source type are required.", "danger")
        return RedirectResponse(url="/config/recruitment-sources", status_code=302)

    source = RecruitmentSource(
        name=name,
        source_type=SourceType(source_type),
        description=form.get("description", "").strip() or None,
        contact_info=form.get("contact_info", "").strip() or None,
    )
    db.add(source)
    db.commit()

    add_flash(request, f"Recruitment source '{name}' added.", "success")
    return RedirectResponse(url="/config/recruitment-sources", status_code=302)


@router.post("/recruitment-sources/{sid}/toggle")
async def toggle_source(sid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    source = db.query(RecruitmentSource).filter(RecruitmentSource.id == sid).first()
    if source:
        source.is_active = not source.is_active
        db.commit()
        status = "activated" if source.is_active else "deactivated"
        add_flash(request, f"Source '{source.name}' {status}.", "success")
    return RedirectResponse(url="/config/recruitment-sources", status_code=302)


@router.post("/recruitment-sources/{sid}/delete")
async def delete_source(sid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ADMIN_EMPLOYER)
    if redir:
        return redir

    source = db.query(RecruitmentSource).filter(RecruitmentSource.id == sid).first()
    if source:
        db.delete(source)
        db.commit()
        add_flash(request, "Recruitment source deleted.", "success")
    return RedirectResponse(url="/config/recruitment-sources", status_code=302)
