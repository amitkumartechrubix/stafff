from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from database import get_db
from models.user import User, UserRole
from models.candidate import Candidate, CandidateStatus
from models.job_posting import JobPosting
from models.company import Company
from models.institution import Institution
from models.recruitment_source import RecruitmentSource
from models.field_agent_location import FieldAgentLocationLog
from datetime import date, datetime, timedelta
from utils import add_flash, build_template_context, require_auth, get_session_user_id

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/")
async def index(request: Request):
    if get_session_user_id(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/dashboard")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db)
    if redir:
        return redir

    role = user.role.value

    if role == "admin":
        return RedirectResponse(url="/admin/dashboard", status_code=302)

    # Common stats for non-admin roles
    my_candidates_count = db.query(func.count(Candidate.id)).filter(
        Candidate.registered_by_id == user.id
    ).scalar()

    job_total = None
    job_active = None
    job_draft = None
    job_closed = None
    if role == "employer":
        base_jobs = db.query(JobPosting).filter(JobPosting.created_by_id == user.id)
        job_total = base_jobs.count()
        job_active = base_jobs.filter(
            JobPosting.status == "active",
            JobPosting.approval_status == "approved",
        ).count()
        job_draft = base_jobs.filter(JobPosting.approval_status == "pending").count()
        job_closed = base_jobs.filter(JobPosting.status == "closed").count()

    institution_total = None
    institution_current = None
    institution_past = None
    institution_layam = None
    institution_employed = None
    if role == "institution":
        institution = db.query(Institution).filter(
            Institution.is_active == True,
            Institution.poc_email == user.email,
        ).first()
        if institution:
            current_year = date.today().year
            base = db.query(Candidate).filter(Candidate.institution_name == institution.name)
            institution_total = base.count()
            institution_current = base.filter(
                or_(Candidate.passing_out_year.is_(None), Candidate.passing_out_year >= current_year)
            ).count()
            institution_past = base.filter(
                Candidate.passing_out_year.is_not(None),
                Candidate.passing_out_year < current_year,
            ).count()
            institution_layam = base.filter(Candidate.registered_with_layam == True).count()
            institution_employed = base.filter(Candidate.employed == True).count()

    field_today = None
    field_week = None
    field_qr = None
    field_active = None
    field_minors = None
    field_followups = None
    if role == "field_agent":
        field_base = db.query(Candidate).filter(Candidate.registered_by_id == user.id)
        start_today = datetime.combine(date.today(), datetime.min.time())
        field_today = field_base.filter(Candidate.registered_at >= start_today).count()
        field_week = field_base.filter(Candidate.registered_at >= start_today - timedelta(days=6)).count()
        field_active = field_base.filter(Candidate.status == CandidateStatus.ACTIVE).count()
        field_minors = field_base.filter(Candidate.age.is_not(None), Candidate.age < 18).count()
        field_followups = field_base.filter(
            Candidate.status == CandidateStatus.ACTIVE,
            (Candidate.notes.is_(None)) | (Candidate.notes == ""),
        ).count()
        field_qr = (
            field_base.join(RecruitmentSource, Candidate.source_id == RecruitmentSource.id)
            .filter(RecruitmentSource.name.ilike("Field Agent -%"))
            .count()
        )

    manager_active_candidates = None
    manager_approved_jobs = None
    manager_assigned_jobs = []
    manager_pending_jobs = None
    manager_closed_jobs = []
    manager_registrations_by_source = []
    if role == "manager":
        manager_active_candidates = db.query(func.count(Candidate.id)).filter(
            Candidate.status == CandidateStatus.ACTIVE
        ).scalar()
        manager_approved_jobs = db.query(func.count(JobPosting.id)).filter(
            JobPosting.status == "active",
            JobPosting.approval_status == "approved",
        ).scalar()
        manager_pending_jobs = db.query(func.count(JobPosting.id)).filter(
            JobPosting.approval_status == "pending"
        ).scalar()

        manager_assigned_jobs = (
            db.query(User.full_name, func.count(JobPosting.id))
            .outerjoin(JobPosting, JobPosting.assigned_recruiter_id == User.id)
            .filter(User.role == UserRole.RECRUITER)
            .group_by(User.id)
            .order_by(func.count(JobPosting.id).desc())
            .all()
        )

        manager_closed_jobs = (
            db.query(User.full_name, func.count(JobPosting.id))
            .outerjoin(
                JobPosting,
                (JobPosting.created_by_id == User.id) & (JobPosting.status == "closed"),
            )
            .filter(User.role == UserRole.RECRUITER)
            .group_by(User.id)
            .order_by(func.count(JobPosting.id).desc())
            .all()
        )

        manager_registrations_by_source = (
            db.query(RecruitmentSource.name, func.count(Candidate.id))
            .outerjoin(Candidate, Candidate.source_id == RecruitmentSource.id)
            .filter(RecruitmentSource.is_active == True)
            .group_by(RecruitmentSource.id)
            .order_by(func.count(Candidate.id).desc())
            .all()
        )

    ctx = build_template_context(
        request, db,
        my_candidates_count=my_candidates_count,
        job_total=job_total,
        job_active=job_active,
        job_draft=job_draft,
        job_closed=job_closed,
        institution_total=institution_total,
        institution_current=institution_current,
        institution_past=institution_past,
        institution_layam=institution_layam,
        institution_employed=institution_employed,
        field_today=field_today,
        field_week=field_week,
        field_qr=field_qr,
        field_active=field_active,
        field_minors=field_minors,
        field_followups=field_followups,
        manager_active_candidates=manager_active_candidates,
        manager_approved_jobs=manager_approved_jobs,
        manager_assigned_jobs=manager_assigned_jobs,
        manager_pending_jobs=manager_pending_jobs,
        manager_closed_jobs=manager_closed_jobs,
        manager_registrations_by_source=manager_registrations_by_source,
        page_title="Dashboard",
    )

    template_map = {
        "employer": "dashboards/employer.html",
        "institution": "dashboards/institution.html",
        "manager": "dashboards/manager.html",
        "recruiter": "dashboards/recruiter.html",
        "field_agent": "dashboards/field_agent.html",
    }
    template = template_map.get(role, "dashboards/employer.html")
    return templates.TemplateResponse(template, ctx)


@router.post("/field-agent/location")
async def field_agent_location(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, UserRole.FIELD_AGENT)
    if redir:
        return redir

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    def parse_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    lat = parse_float(data.get("latitude"))
    lon = parse_float(data.get("longitude"))
    if lat is None or lon is None:
        return JSONResponse({"ok": False, "error": "Missing coordinates"}, status_code=400)

    accuracy = parse_float(data.get("accuracy_m"))
    address = (data.get("address") or "").strip() or None
    setup_type = (data.get("setup_type") or "").strip() or None

    db.add(
        FieldAgentLocationLog(
            user_id=user.id,
            latitude=lat,
            longitude=lon,
            accuracy_m=accuracy,
            address=address,
            setup_type=setup_type,
        )
    )
    db.commit()

    return JSONResponse({"ok": True})
