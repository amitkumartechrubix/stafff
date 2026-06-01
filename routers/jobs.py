from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime
from database import get_db
from config import settings
from utils import add_flash, build_template_context, require_auth
from models.job_posting import JobPosting
from models.user import User, UserRole
from models.candidate import Candidate
from models.job_profile import JobProfile
from models.company import Company, CompanyLocation

router = APIRouter(prefix="/jobs")
templates = Jinja2Templates(directory="templates")

ALLOWED_ROLES = ("admin", "employer", "manager", "recruiter", "field_agent")
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_CANCELLED = "cancelled"


def can_manager_approve(job: JobPosting) -> list[str]:
    missing = []
    if not (job.company_id or job.company_name or job.title):
        missing.append("Company")
    if not (job.designation or job.role_title):
        missing.append("Designation")
    if not (job.location or job.city or job.state or job.address):
        missing.append("Location")
    if not job.openings:
        missing.append("Openings")
    if not job.employment_type:
        missing.append("Employment Type")
    return missing


def get_employer_company(db: Session, user) -> Company | None:
    if user.role.value != "employer":
        return None
    return (
        db.query(Company)
        .filter(
            Company.is_active == True,
            or_(
                Company.technical_contact_email == user.email,
                Company.hr_contact_email == user.email,
            ),
        )
        .first()
    )


def apply_employer_job_scope(query, employer_company: Company | None, user: User):
    if employer_company:
        return query.filter(
            or_(
                JobPosting.company_id == employer_company.id,
                JobPosting.company_name == employer_company.name,
            )
        )
    return query.filter(JobPosting.created_by_id == user.id)


def _normalize_industry(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def _dedupe_profiles(profiles: list[JobProfile]) -> list[JobProfile]:
    unique = {}
    for profile in profiles:
        key = (
            _normalize_industry(profile.industry),
            _normalize_text(profile.designation or profile.role_title),
            profile.min_experience or 0,
            profile.max_experience or 0,
        )
        if key not in unique:
            unique[key] = profile
    return list(unique.values())


@router.get("")
async def jobs_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    status: str = "",
    view: str = "",
):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if status == "draft":
        status = "pending_approval"

    if not view and status == STATUS_ACTIVE:
        view = "open"

    query = db.query(JobPosting)
    if user.role.value == "employer":
        employer_company = get_employer_company(db, user)
        query = apply_employer_job_scope(query, employer_company, user)
    if user.role.value == "recruiter":
        query = query.filter(JobPosting.assigned_recruiter_id == user.id)
    if search:
        query = query.filter(
            (JobPosting.title.ilike(f"%{search}%")) |
            (JobPosting.role_title.ilike(f"%{search}%")) |
            (JobPosting.designation.ilike(f"%{search}%")) |
            (JobPosting.location.ilike(f"%{search}%"))
        )
    if status:
        if status == "pending_approval":
            query = query.filter(JobPosting.approval_status == APPROVAL_PENDING)
        elif status == STATUS_ACTIVE:
            if user.role.value == "employer":
                query = query.filter(JobPosting.status == STATUS_ACTIVE)
            else:
                query = query.filter(
                    JobPosting.status == STATUS_ACTIVE,
                    JobPosting.approval_status == APPROVAL_APPROVED,
                )
        else:
            query = query.filter(JobPosting.status == status)

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    jobs = (
        query.order_by(JobPosting.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    job_stats = {}
    if jobs:
        job_ids = [job.id for job in jobs]
        stats_map = {jid: {"shortlisted": 0, "placed": 0} for jid in job_ids}
        candidates = (
            db.query(Candidate.locked_job_id, Candidate.employment_stage)
            .filter(Candidate.locked_job_id.in_(job_ids))
            .all()
        )
        for locked_job_id, stage in candidates:
            if locked_job_id not in stats_map:
                continue
            if stage == "shortlisted":
                stats_map[locked_job_id]["shortlisted"] += 1
            if stage == "placed":
                stats_map[locked_job_id]["placed"] += 1

        auto_closed = False
        for job in jobs:
            openings = job.openings or 0
            placed = stats_map.get(job.id, {}).get("placed", 0)
            percent = int(min(100, (placed / openings) * 100)) if openings > 0 else 0
            job_stats[job.id] = {
                "shortlisted": stats_map.get(job.id, {}).get("shortlisted", 0),
                "placed": placed,
                "openings": openings,
                "percent": percent,
            }
            if openings > 0 and placed >= openings and job.status != STATUS_CLOSED:
                job.status = STATUS_CLOSED
                job.is_active = False
                auto_closed = True

        if auto_closed:
            db.commit()

    ctx = build_template_context(
        request, db,
        jobs=jobs,
        job_stats=job_stats,
        view_mode=view,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        status_filter=status,
        page_title="Job Postings",
    )
    return templates.TemplateResponse("jobs/list.html", ctx)


@router.get("/new")
async def job_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    profiles = db.query(JobProfile).filter(JobProfile.is_active == True).order_by(JobProfile.role_title).all()
    employer_company = get_employer_company(db, user)
    if user.role.value == "employer" and not employer_company:
        add_flash(request, "No company is linked to this employer account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    if employer_company:
        companies = [employer_company]
    else:
        companies = db.query(Company).filter(Company.is_active == True).order_by(Company.name).all()

    if employer_company and employer_company.industry:
        normalized_company_industry = _normalize_industry(employer_company.industry)
        matched = [
            profile for profile in profiles
            if _normalize_industry(profile.industry) == normalized_company_industry
        ]
        if matched:
            profiles = matched

    profiles = _dedupe_profiles(profiles)

    company_locations = db.query(CompanyLocation).filter(
        CompanyLocation.company_id.in_([c.id for c in companies])
    ).all()
    company_locations_map = {}
    for company in companies:
        if company.city or company.state or company.address:
            company_locations_map.setdefault(company.id, []).append({
                "id": f"primary-{company.id}",
                "city": company.city or "",
                "state": company.state or "",
                "address": company.address or "",
                "label": "Main Location",
            })
    for loc in company_locations:
        company_locations_map.setdefault(loc.company_id, []).append({
            "id": loc.id,
            "city": loc.city or "",
            "state": loc.state or "",
            "address": loc.address or "",
        })
    ctx = build_template_context(
        request, db,
        profiles=profiles,
        companies=companies,
        company_locations_map=company_locations_map,
        employer_company_id=employer_company.id if employer_company else None,
        employer_company_lock=bool(employer_company),
        employer_company_industry=employer_company.industry if employer_company else None,
        page_title="Create Job Posting",
        form_action="/jobs/new",
        edit_job=None,
    )
    return templates.TemplateResponse("jobs/form.html", ctx)


@router.get("/{jid}")
async def job_view(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    recruiters = []
    if user.role.value == "manager":
        recruiters = (
            db.query(User)
            .filter(User.role == UserRole.RECRUITER, User.is_active == True)
            .order_by(User.full_name)
            .all()
        )

    ctx = build_template_context(
        request, db,
        job=job,
        page_title=f"Job - {job.designation or job.role_title or job.title}",
        back_url="/jobs",
        back_label="Back to Jobs",
        edit_url=f"/jobs/{jid}/edit",
        recruiters=recruiters,
    )
    return templates.TemplateResponse("jobs/view.html", ctx)


@router.get("/{jid}/staffing")
async def job_staffing(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value not in ("recruiter", "manager", "admin"):
        return RedirectResponse(url="/unauthorized", status_code=302)

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    assigned_candidates = []
    assigned_positions = []
    if user.role.value == "recruiter":
        assigned_candidates = (
            db.query(Candidate)
            .filter(
                Candidate.assigned_recruiter_id == user.id,
                or_(Candidate.locked_job_id.is_(None), Candidate.locked_job_id == jid),
            )
            .order_by(Candidate.registered_at.desc())
            .all()
        )
        assigned_positions = (
            db.query(JobPosting)
            .filter(
                JobPosting.assigned_recruiter_id == user.id,
                JobPosting.status == STATUS_ACTIVE,
                JobPosting.approval_status == APPROVAL_APPROVED,
            )
            .order_by(JobPosting.created_at.desc())
            .all()
        )

    ctx = build_template_context(
        request, db,
        job=job,
        assigned_candidates=assigned_candidates,
        assigned_positions=assigned_positions,
        page_title=f"Start Staffing — {job.designation or job.role_title or job.title}",
    )
    return templates.TemplateResponse("jobs/staffing.html", ctx)


@router.post("/{jid}/lock-candidate")
async def job_lock_candidate(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "recruiter":
        add_flash(request, "Only recruiters can lock candidates.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    form = await request.form()
    candidate_id = (form.get("candidate_id") or "").strip()
    position_id = (form.get("position_id") or "").strip()
    if not candidate_id.isdigit():
        add_flash(request, "Invalid candidate selection.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)
    if not position_id.isdigit():
        add_flash(request, "Please select a position.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == int(candidate_id)).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)

    if candidate.assigned_recruiter_id != user.id:
        add_flash(request, "You can only lock candidates assigned to you.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)

    selected_job = (
        db.query(JobPosting)
        .filter(
            JobPosting.id == int(position_id),
            JobPosting.assigned_recruiter_id == user.id,
            JobPosting.status == STATUS_ACTIVE,
            JobPosting.approval_status == APPROVAL_APPROVED,
        )
        .first()
    )
    if not selected_job:
        add_flash(request, "Selected position is not available.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)

    if candidate.locked_job_id and candidate.locked_job_id != selected_job.id:
        add_flash(request, "Candidate is locked to another position.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)

    candidate.locked_job_id = selected_job.id
    candidate.locked_at = datetime.utcnow()
    db.commit()
    add_flash(request, "Candidate locked to selected position.", "success")
    return RedirectResponse(url=f"/jobs/{jid}/staffing", status_code=302)


@router.post("/new")
async def job_new_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    form = await request.form()
    company_not_registered = form.get("company_not_registered") == "1"
    company_id_str = form.get("company_id", "")
    company_id = int(company_id_str) if company_id_str and company_id_str.isdigit() else None
    company_name = form.get("company_name", "").strip()

    employer_company = get_employer_company(db, user)
    if user.role.value == "employer":
        if not employer_company:
            add_flash(request, "No company is linked to this employer account.", "danger")
            return RedirectResponse(url="/jobs/new", status_code=302)
        company_not_registered = False
        company_id = employer_company.id
        company_name = employer_company.name

    if company_not_registered:
        if not company_name:
            add_flash(request, "Company name is required.", "danger")
            return RedirectResponse(url="/jobs/new", status_code=302)
        company_id = None
    else:
        if not company_id:
            add_flash(request, "Please select a registered company.", "danger")
            return RedirectResponse(url="/jobs/new", status_code=302)
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            add_flash(request, "Selected company not found.", "danger")
            return RedirectResponse(url="/jobs/new", status_code=302)
        company_name = company.name

    title = company_name

    profile_id_str = form.get("profile_id", "")
    profile_id = int(profile_id_str) if profile_id_str and profile_id_str.isdigit() else None

    designation = form.get("designation", "").strip() or None
    industry = form.get("industry", "").strip() or None
    if user.role.value == "employer" and employer_company:
        industry = employer_company.industry or industry
    city = form.get("location_city", "").strip() or None
    state = form.get("location_state", "").strip() or None
    address = form.get("location_address", "").strip() or None
    plant_address = form.get("plant_address", "").strip() or None
    if city or state:
        location = ", ".join([part for part in [city, state] if part])
    else:
        location = None
    status_value = STATUS_ACTIVE
    is_active_value = True
    approval_value = APPROVAL_PENDING

    openings_val = (form.get("openings") or "").strip()
    if not openings_val.isdigit() or int(openings_val) <= 0:
        add_flash(request, "Openings is required and must be greater than 0.", "danger")
        return RedirectResponse(url="/jobs/new", status_code=302)

    job = JobPosting(
        title=title,
        company_id=company_id,
        company_name=company_name if company_not_registered else None,
        profile_id=profile_id,
        role_title=designation,
        designation=designation,
        industry=industry,
        min_experience=int(form.get("min_experience") or 0) or None,
        max_experience=int(form.get("max_experience") or 0) or None,
        skills=form.get("skills", "").strip() or None,
        jd_summary=form.get("jd_summary", "").strip() or None,
        technical_responsibilities=form.get("technical_responsibilities", "").strip() or None,
        functional_responsibilities=form.get("functional_responsibilities", "").strip() or None,
        location=location,
        city=city,
        state=state,
        address=address,
        plant_address=plant_address,
        openings=int(openings_val),
        employment_type=form.get("employment_type", "").strip() or None,
        salary_range=form.get("salary_range", "").strip() or None,
        status=status_value,
        is_active=is_active_value,
        approval_status=approval_value,
        created_by_id=user.id,
    )
    db.add(job)
    db.commit()

    add_flash(request, "Job posting created.", "success")
    return RedirectResponse(url="/jobs", status_code=302)


@router.get("/{jid}/edit")
async def job_edit_form(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    profiles = db.query(JobProfile).filter(JobProfile.is_active == True).order_by(JobProfile.role_title).all()
    companies = db.query(Company).filter(Company.is_active == True).order_by(Company.name).all()
    company_locations = db.query(CompanyLocation).filter(
        CompanyLocation.company_id.in_([c.id for c in companies])
    ).all()
    company_locations_map = {}
    for company in companies:
        if company.city or company.state or company.address:
            company_locations_map.setdefault(company.id, []).append({
                "id": f"primary-{company.id}",
                "city": company.city or "",
                "state": company.state or "",
                "address": company.address or "",
                "label": "Main Location",
            })
    for loc in company_locations:
        company_locations_map.setdefault(loc.company_id, []).append({
            "id": loc.id,
            "city": loc.city or "",
            "state": loc.state or "",
            "address": loc.address or "",
        })
    ctx = build_template_context(
        request, db,
        profiles=profiles,
        companies=companies,
        company_locations_map=company_locations_map,
        page_title=f"Edit Job — {job.title}",
        form_action=f"/jobs/{jid}/edit",
        edit_job=job,
    )
    return templates.TemplateResponse("jobs/form.html", ctx)


@router.post("/{jid}/edit")
async def job_edit_post(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    form = await request.form()
    company_not_registered = form.get("company_not_registered") == "1"
    company_id_str = form.get("company_id", "")
    company_id = int(company_id_str) if company_id_str and company_id_str.isdigit() else None
    company_name = form.get("company_name", "").strip()

    if company_not_registered:
        if not company_name:
            add_flash(request, "Company name is required.", "danger")
            return RedirectResponse(url=f"/jobs/{jid}/edit", status_code=302)
        job.company_id = None
        job.company_name = company_name
    else:
        if not company_id:
            add_flash(request, "Please select a registered company.", "danger")
            return RedirectResponse(url=f"/jobs/{jid}/edit", status_code=302)
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            add_flash(request, "Selected company not found.", "danger")
            return RedirectResponse(url=f"/jobs/{jid}/edit", status_code=302)
        job.company_id = company_id
        job.company_name = None
        company_name = company.name

    job.title = company_name
    profile_id_str = form.get("profile_id", "")
    job.profile_id = int(profile_id_str) if profile_id_str and profile_id_str.isdigit() else None
    designation = form.get("designation", "").strip() or None
    job.role_title = designation
    job.designation = designation
    job.industry = form.get("industry", "").strip() or None
    job.city = form.get("location_city", "").strip() or None
    job.state = form.get("location_state", "").strip() or None
    job.address = form.get("location_address", "").strip() or None
    job.plant_address = form.get("plant_address", "").strip() or None
    if job.city or job.state:
        job.location = ", ".join([part for part in [job.city, job.state] if part])
    else:
        job.location = None
    job.min_experience = int(form.get("min_experience") or 0) or None
    job.max_experience = int(form.get("max_experience") or 0) or None
    job.skills = form.get("skills", "").strip() or None
    job.jd_summary = form.get("jd_summary", "").strip() or None
    job.technical_responsibilities = form.get("technical_responsibilities", "").strip() or None
    job.functional_responsibilities = form.get("functional_responsibilities", "").strip() or None
    job.location = form.get("location", "").strip() or None
    openings_val = (form.get("openings") or "").strip()
    if not openings_val.isdigit() or int(openings_val) <= 0:
        add_flash(request, "Openings is required and must be greater than 0.", "danger")
        return RedirectResponse(url=f"/jobs/{jid}/edit", status_code=302)
    job.openings = int(openings_val)
    job.employment_type = form.get("employment_type", "").strip() or None
    job.salary_range = form.get("salary_range", "").strip() or None
    if user.role.value == "manager":
        job.status = form.get("status", job.status).strip() or job.status
        job.is_active = form.get("is_active") == "1"
    else:
        job.status = job.status or STATUS_ACTIVE

    if user.role.value in ("manager", "recruiter"):
        approval = form.get("approval_status", job.approval_status).strip().lower()
        if approval in (APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_CANCELLED):
            job.approval_status = approval
    db.commit()

    add_flash(request, "Job posting updated.", "success")
    return RedirectResponse(url="/jobs", status_code=302)


@router.post("/{jid}/toggle")
async def job_toggle(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "manager":
        add_flash(request, "Only managers can activate or deactivate jobs.", "warning")
        return RedirectResponse(url="/jobs", status_code=302)

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    job.is_active = not job.is_active
    db.commit()
    return RedirectResponse(url="/jobs", status_code=302)


@router.post("/{jid}/close")
async def job_force_close(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value not in ("manager", "employer", "admin"):
        add_flash(request, "Only managers or employers can close job postings.", "warning")
        return RedirectResponse(url="/jobs", status_code=302)

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    job.status = STATUS_CLOSED
    job.is_active = False
    db.commit()
    add_flash(request, "Job posting closed.", "success")
    return RedirectResponse(url="/jobs", status_code=302)


@router.post("/{jid}/approve")
async def job_approve(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value not in ("manager", "recruiter"):
        add_flash(request, "Only managers or recruiters can approve job postings.", "warning")
        return RedirectResponse(url="/jobs", status_code=302)

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    if job.approval_status != APPROVAL_PENDING:
        add_flash(request, "Only pending jobs can be approved.", "warning")
        return RedirectResponse(url="/jobs", status_code=302)

    missing = can_manager_approve(job)
    if missing:
        add_flash(
            request,
            "Cannot approve. Missing: " + ", ".join(missing) + ".",
            "warning",
        )
        return RedirectResponse(url=f"/jobs/{jid}", status_code=302)

    job.approval_status = APPROVAL_APPROVED
    job.status = STATUS_ACTIVE
    job.is_active = True
    db.commit()
    add_flash(request, "Job posting approved and activated.", "success")
    return RedirectResponse(url="/jobs", status_code=302)


@router.post("/{jid}/assign")
async def job_assign(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "manager":
        add_flash(request, "Only managers can assign recruiters.", "warning")
        return RedirectResponse(url="/jobs", status_code=302)

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if not job:
        add_flash(request, "Job posting not found.", "danger")
        return RedirectResponse(url="/jobs", status_code=302)

    if job.approval_status != APPROVAL_APPROVED:
        add_flash(request, "Approve the job before assigning a recruiter.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}", status_code=302)

    form = await request.form()
    recruiter_id_str = (form.get("assigned_recruiter_id") or "").strip()
    if not recruiter_id_str:
        job.assigned_recruiter_id = None
        db.commit()
        add_flash(request, "Recruiter assignment cleared.", "info")
        return RedirectResponse(url=f"/jobs/{jid}", status_code=302)

    if not recruiter_id_str.isdigit():
        add_flash(request, "Invalid recruiter selection.", "warning")
        return RedirectResponse(url=f"/jobs/{jid}", status_code=302)

    recruiter = db.query(User).filter(
        User.id == int(recruiter_id_str),
        User.role == UserRole.RECRUITER,
        User.is_active == True,
    ).first()
    if not recruiter:
        add_flash(request, "Recruiter not found.", "danger")
        return RedirectResponse(url=f"/jobs/{jid}", status_code=302)

    job.assigned_recruiter_id = recruiter.id
    db.commit()
    add_flash(request, f"Assigned to {recruiter.full_name}.", "success")
    return RedirectResponse(url=f"/jobs/{jid}", status_code=302)


@router.post("/{jid}/delete")
async def job_delete(jid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    job = db.query(JobPosting).filter(JobPosting.id == jid).first()
    if job:
        db.delete(job)
        db.commit()
    return RedirectResponse(url="/jobs", status_code=302)
