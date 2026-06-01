from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from datetime import date
import csv
import io
import openpyxl
import xlrd
from typing import Dict, Iterable
from database import get_db
from models.institution import Institution
from models.candidate import Candidate, CandidateStatus
from models.recruitment_source import RecruitmentSource
from utils import add_flash, build_template_context, require_auth, ensure_contact_user
from models.user import UserRole, LicenseType
from config import settings

router = APIRouter(prefix="/institutions")
templates = Jinja2Templates(directory="templates")

ALLOWED_ROLES = ("admin", "employer")
INSTITUTION_ROLES = ("institution",)


def get_institution_for_user(db: Session, user) -> Institution | None:
    if user.role.value != "institution":
        return None
    return db.query(Institution).filter(
        Institution.is_active == True,
        Institution.poc_email == user.email,
    ).first()


@router.get("")
async def institutions_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    verified: str = "",
):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    query = db.query(Institution)
    if search:
        query = query.filter(
            (Institution.name.ilike(f"%{search}%")) |
            (Institution.city.ilike(f"%{search}%")) |
            (Institution.poc_name.ilike(f"%{search}%"))
        )
    if verified == "1":
        query = query.filter(Institution.is_verified == True)
    elif verified == "0":
        query = query.filter(Institution.is_verified == False)

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    institutions = (
        query.order_by(Institution.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    ctx = build_template_context(
        request, db,
        institutions=institutions,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        verified_filter=verified,
        page_title="Institutions",
    )
    return templates.TemplateResponse("institutions/list.html", ctx)


@router.get("/new")
async def institution_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir
    ctx = build_template_context(
        request, db,
        page_title="Add Institution",
        form_action="/institutions/new",
        edit_institution=None,
    )
    return templates.TemplateResponse("institutions/form.html", ctx)


@router.post("/new")
async def institution_new_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    form = await request.form()
    name = form.get("name", "").strip()
    if not name:
        add_flash(request, "Institution name is required.", "danger")
        return RedirectResponse(url="/institutions/new", status_code=302)

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
        student_strength=int(form.get("student_strength") or 0) or None,
        poc_name=form.get("poc_name", "").strip() or None,
        poc_designation=form.get("poc_designation", "").strip() or None,
        poc_email=form.get("poc_email", "").strip() or None,
        poc_phone=form.get("poc_phone", "").strip() or None,
        is_verified=form.get("is_verified") == "1",
    )
    db.add(institution)
    db.commit()

    ensure_contact_user(
        db,
        email=institution.poc_email,
        full_name=institution.poc_name,
        phone=institution.poc_phone,
        role=UserRole.INSTITUTION,
        license_type=LicenseType.PROFESSIONAL,
        created_by_id=user.id,
    )

    add_flash(request, f"Institution '{name}' added successfully.", "success")
    return RedirectResponse(url="/institutions", status_code=302)


@router.get("/{iid}")
async def institution_view(iid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    inst = db.query(Institution).filter(Institution.id == iid).first()
    if not inst:
        add_flash(request, "Institution not found.", "danger")
        return RedirectResponse(url="/institutions", status_code=302)

    ctx = build_template_context(
        request, db,
        institution=inst,
        page_title=f"Institution - {inst.name}",
        back_url="/institutions",
        back_label="Back to Institutions",
        edit_url=f"/institutions/{iid}/edit",
    )
    return templates.TemplateResponse("institutions/view.html", ctx)


@router.get("/{iid}/edit")
async def institution_edit_form(iid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    inst = db.query(Institution).filter(Institution.id == iid).first()
    if not inst:
        add_flash(request, "Institution not found.", "danger")
        return RedirectResponse(url="/institutions", status_code=302)

    ctx = build_template_context(
        request, db,
        page_title=f"Edit Institution — {inst.name}",
        form_action=f"/institutions/{iid}/edit",
        edit_institution=inst,
    )
    return templates.TemplateResponse("institutions/form.html", ctx)


@router.post("/{iid}/edit")
async def institution_edit_post(iid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    inst = db.query(Institution).filter(Institution.id == iid).first()
    if not inst:
        add_flash(request, "Institution not found.", "danger")
        return RedirectResponse(url="/institutions", status_code=302)

    form = await request.form()
    inst.name = form.get("name", inst.name).strip()
    inst.institution_type = form.get("institution_type", "").strip() or None
    inst.location = form.get("location", "").strip() or None
    inst.address = form.get("address", "").strip() or None
    inst.city = form.get("city", "").strip() or None
    inst.state = form.get("state", "").strip() or None
    inst.pincode = form.get("pincode", "").strip() or None
    inst.courses_offered = form.get("courses_offered", "").strip() or None
    inst.years_of_operation = int(form.get("years_of_operation") or 0) or None
    inst.affiliation = form.get("affiliation", "").strip() or None
    inst.student_strength = int(form.get("student_strength") or 0) or None
    inst.poc_name = form.get("poc_name", "").strip() or None
    inst.poc_designation = form.get("poc_designation", "").strip() or None
    inst.poc_email = form.get("poc_email", "").strip() or None
    inst.poc_phone = form.get("poc_phone", "").strip() or None
    inst.is_verified = form.get("is_verified") == "1"
    inst.is_active = form.get("is_active") == "1"
    db.commit()

    ensure_contact_user(
        db,
        email=inst.poc_email,
        full_name=inst.poc_name,
        phone=inst.poc_phone,
        role=UserRole.INSTITUTION,
        license_type=LicenseType.PROFESSIONAL,
        created_by_id=user.id,
    )

    add_flash(request, "Institution updated successfully.", "success")
    return RedirectResponse(url="/institutions", status_code=302)


# ── Institution Portal: Current Students ─────────────────────────────────────

@router.get("/students")
async def institution_students_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    status: str = "",
    state: str = "",
    city: str = "",
):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    current_year = date.today().year
    query = db.query(Candidate).filter(
        Candidate.institution_name == institution.name,
        ((Candidate.passing_out_year.is_(None)) | (Candidate.passing_out_year >= current_year)),
    )
    if search:
        query = query.filter(
            (Candidate.full_name.ilike(f"%{search}%")) |
            (Candidate.phone.ilike(f"%{search}%")) |
            (Candidate.email.ilike(f"%{search}%"))
        )
    if status:
        query = query.filter(Candidate.status == status)
    if state:
        query = query.filter(Candidate.state.ilike(f"%{state}%"))
    if city:
        query = query.filter(Candidate.city.ilike(f"%{city}%"))

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    candidates = (
        query.order_by(Candidate.registered_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    ctx = build_template_context(
        request, db,
        candidates=candidates,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        status_filter=status,
        state_filter=state,
        city_filter=city,
        statuses=CandidateStatus,
        current_year=current_year,
        page_title="Current Students",
    )
    return templates.TemplateResponse("institutions/students_list.html", ctx)


@router.get("/past-students")
async def institution_past_students_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    status: str = "",
    state: str = "",
    city: str = "",
):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    current_year = date.today().year
    query = db.query(Candidate).filter(
        Candidate.institution_name == institution.name,
        Candidate.passing_out_year.is_not(None),
        Candidate.passing_out_year < current_year,
    )
    if search:
        query = query.filter(
            (Candidate.full_name.ilike(f"%{search}%")) |
            (Candidate.phone.ilike(f"%{search}%")) |
            (Candidate.email.ilike(f"%{search}%"))
        )
    if status:
        query = query.filter(Candidate.status == status)
    if state:
        query = query.filter(Candidate.state.ilike(f"%{state}%"))
    if city:
        query = query.filter(Candidate.city.ilike(f"%{city}%"))

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    candidates = (
        query.order_by(Candidate.registered_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    ctx = build_template_context(
        request, db,
        candidates=candidates,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        status_filter=status,
        state_filter=state,
        city_filter=city,
        statuses=CandidateStatus,
        page_title="Past Students",
    )
    return templates.TemplateResponse("institutions/past_students_list.html", ctx)


@router.get("/bulk-upload")
async def institution_bulk_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    ctx = build_template_context(
        request, db,
        page_title="Bulk Upload to Layam",
    )
    return templates.TemplateResponse("institutions/bulk_upload.html", ctx)


@router.get("/bulk-template")
async def institution_bulk_template(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=students_template.csv"},
    )


@router.post("/bulk-upload")
async def institution_bulk_upload(
    request: Request,
    db: Session = Depends(get_db),
    upload_file: UploadFile = File(...),
):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    if not upload_file.filename:
        add_flash(request, "Please select a file to upload.", "danger")
        return RedirectResponse(url="/institutions/bulk-upload", status_code=302)

    content = await upload_file.read()
    try:
        rows = list(iter_upload_rows(upload_file.filename, content))
    except ValueError as exc:
        add_flash(request, str(exc), "danger")
        return RedirectResponse(url="/institutions/bulk-upload", status_code=302)

    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    source_map = {s.name.strip().lower(): s.id for s in sources if s.name}
    default_source = next((s for s in sources if s.name and s.name.lower() == "college / campus"), None)
    default_source_id = default_source.id if default_source else None

    created = 0
    skipped = 0
    for row in rows:
        full_name = row.get("full_name", "").strip()
        phone = row.get("phone", "").strip()
        if not full_name or not phone:
            skipped += 1
            continue

        age_val = row.get("age", "").strip()
        age = int(age_val) if age_val.isdigit() else None

        exp_val = row.get("experience_years", "").strip()
        exp = int(exp_val) if exp_val.isdigit() else 0

        source_id_val = row.get("source_id", "").strip()
        source_id = int(source_id_val) if source_id_val.isdigit() else None
        if not source_id:
            source_val = row.get("source", "").strip().lower()
            source_id = source_map.get(source_val) if source_val else None
        if not source_id:
            source_id = default_source_id

        passing_val = row.get("passing_out_year", "").strip()
        passing_out_year = int(passing_val) if passing_val.isdigit() else None

        dob = parse_date(row.get("date_of_birth", "")) if row.get("date_of_birth") else None
        candidate = Candidate(
            full_name=full_name,
            phone=phone,
            email=row.get("email", "").strip() or None,
            location=row.get("location", "").strip() or None,
            city=row.get("city", "").strip() or None,
            state=row.get("state", "").strip() or None,
            qualification=row.get("qualification", "").strip() or None,
            age=compute_age(dob) if dob else age,
            date_of_birth=dob,
            gender=row.get("gender", "").strip() or None,
            skills=row.get("skills", "").strip() or None,
            experience_years=exp,
            preferred_job_type=row.get("preferred_job_type", "").strip() or None,
            expected_salary=row.get("expected_salary", "").strip() or None,
            passing_out_year=passing_out_year,
            source_id=source_id,
            notes=row.get("notes", "").strip() or None,
            institution_name=institution.name,
            registered_by_id=user.id,
            registered_with_layam=True,
        )
        db.add(candidate)
        created += 1

    if created:
        db.commit()

    add_flash(request, f"Bulk upload complete. Created: {created}, Skipped: {skipped}.", "success")
    return RedirectResponse(url="/institutions/students", status_code=302)

@router.get("/students/new")
async def institution_students_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    default_source = next((s for s in sources if s.name and s.name.lower() == "college / campus"), None)
    ctx = build_template_context(
        request, db,
        sources=sources,
        default_source_id=default_source.id if default_source else None,
        institution_name=institution.name,
        page_title="Register Current Student",
        form_action="/institutions/students/new",
        edit_candidate=None,
    )
    return templates.TemplateResponse("candidates/form.html", ctx)


@router.post("/students/new")
async def institution_students_new_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    form = await request.form()
    full_name = form.get("full_name", "").strip()
    phone = form.get("phone", "").strip()

    if not full_name or not phone:
        add_flash(request, "Full name and phone are required.", "danger")
        return RedirectResponse(url="/institutions/students/new", status_code=302)

    dob_str = form.get("date_of_birth", "")
    dob = date.fromisoformat(dob_str) if dob_str else None

    age = None
    if dob:
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 0:
            age = None

    exp_str = form.get("experience_years", "0")
    exp = int(exp_str) if exp_str and exp_str.isdigit() else 0

    source_id_str = form.get("source_id", "")
    source_id = int(source_id_str) if source_id_str and source_id_str.isdigit() else None
    if not source_id:
        default_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.is_active == True,
            RecruitmentSource.name.ilike("%College / Campus%"),
        ).first()
        source_id = default_source.id if default_source else None

    passing_year_str = form.get("passing_out_year", "")
    passing_out_year = int(passing_year_str) if passing_year_str and passing_year_str.isdigit() else None

    candidate = Candidate(
        full_name=full_name,
        phone=phone,
        email=form.get("email", "").strip() or None,
        location=form.get("location", "").strip() or None,
        city=form.get("city", "").strip() or None,
        state=form.get("state", "").strip() or None,
        qualification=form.get("qualification", "").strip() or None,
        age=age,
        date_of_birth=dob,
        gender=form.get("gender", "").strip() or None,
        skills=form.get("skills", "").strip() or None,
        experience_years=exp,
        preferred_job_type=form.get("preferred_job_type", "").strip() or None,
        expected_salary=form.get("expected_salary", "").strip() or None,
        passing_out_year=passing_out_year,
        source_id=source_id,
        notes=form.get("notes", "").strip() or None,
        institution_name=institution.name,
        registered_by_id=user.id,
    )
    db.add(candidate)
    db.commit()

    add_flash(request, f"Student '{full_name}' registered successfully.", "success")
    return RedirectResponse(url="/institutions/students", status_code=302)


@router.get("/students/{cid}")
async def institution_students_view(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    candidate = db.query(Candidate).filter(
        Candidate.id == cid,
        Candidate.institution_name == institution.name,
    ).first()
    if not candidate:
        add_flash(request, "Student not found.", "danger")
        return RedirectResponse(url="/institutions/students", status_code=302)

    from_page = request.query_params.get("from")
    back_url = "/institutions/past-students" if from_page == "past" else "/institutions/students"
    back_label = "Back to Past Students" if from_page == "past" else "Back to Current Students"

    ctx = build_template_context(
        request, db,
        candidate=candidate,
        page_title=f"Student - {candidate.full_name}",
        back_url=back_url,
        back_label=back_label,
        show_layam_register=True,
        current_year=date.today().year,
        edit_url=f"/institutions/students/{cid}/edit",
    )
    return templates.TemplateResponse("candidates/view.html", ctx)


@router.post("/students/{cid}/register-layam")
async def institution_students_register_layam(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    candidate = db.query(Candidate).filter(
        Candidate.id == cid,
        Candidate.institution_name == institution.name,
    ).first()
    if not candidate:
        add_flash(request, "Student not found.", "danger")
        return RedirectResponse(url="/institutions/students", status_code=302)

    if candidate.registered_with_layam:
        add_flash(request, "Student is already registered with Layam.", "info")
        return RedirectResponse(url="/institutions/students", status_code=302)

    candidate.registered_with_layam = True
    db.commit()

    add_flash(request, f"'{candidate.full_name}' registered with Layam.", "success")
    return RedirectResponse(url="/institutions/students", status_code=302)


@router.get("/students/{cid}/edit")
async def institution_students_edit_form(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    candidate = db.query(Candidate).filter(
        Candidate.id == cid,
        Candidate.institution_name == institution.name,
    ).first()
    if not candidate:
        add_flash(request, "Student not found.", "danger")
        return RedirectResponse(url="/institutions/students", status_code=302)

    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    ctx = build_template_context(
        request, db,
        sources=sources,
        institution_name=institution.name,
        page_title=f"Edit Student — {candidate.full_name}",
        form_action=f"/institutions/students/{cid}/edit",
        edit_candidate=candidate,
        statuses=CandidateStatus,
        current_year=date.today().year,
        show_layam_register=True,
    )
    return templates.TemplateResponse("candidates/form.html", ctx)


@router.post("/students/{cid}/edit")
async def institution_students_edit_post(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *INSTITUTION_ROLES)
    if redir:
        return redir

    institution = get_institution_for_user(db, user)
    if not institution:
        add_flash(request, "Institution profile not linked to this account.", "danger")
        return RedirectResponse(url="/dashboard", status_code=302)

    candidate = db.query(Candidate).filter(
        Candidate.id == cid,
        Candidate.institution_name == institution.name,
    ).first()
    if not candidate:
        add_flash(request, "Student not found.", "danger")
        return RedirectResponse(url="/institutions/students", status_code=302)

    form = await request.form()
    candidate.full_name = form.get("full_name", candidate.full_name).strip()
    candidate.phone = form.get("phone", candidate.phone).strip()
    candidate.email = form.get("email", "").strip() or None
    candidate.location = form.get("location", "").strip() or None
    candidate.city = form.get("city", "").strip() or None
    candidate.state = form.get("state", "").strip() or None
    candidate.qualification = form.get("qualification", "").strip() or None
    candidate.gender = form.get("gender", "").strip() or None
    candidate.skills = form.get("skills", "").strip() or None
    candidate.preferred_job_type = form.get("preferred_job_type", "").strip() or None
    candidate.expected_salary = form.get("expected_salary", "").strip() or None
    passing_year_str = form.get("passing_out_year", "")
    candidate.passing_out_year = int(passing_year_str) if passing_year_str and passing_year_str.isdigit() else None
    candidate.notes = form.get("notes", "").strip() or None

    exp_str = form.get("experience_years", "0")
    candidate.experience_years = int(exp_str) if exp_str and exp_str.isdigit() else 0

    dob_str = form.get("date_of_birth", "")
    candidate.date_of_birth = date.fromisoformat(dob_str) if dob_str else None
    if candidate.date_of_birth:
        today = date.today()
        candidate.age = today.year - candidate.date_of_birth.year - (
            (today.month, today.day) < (candidate.date_of_birth.month, candidate.date_of_birth.day)
        )

    source_id_str = form.get("source_id", "")
    candidate.source_id = int(source_id_str) if source_id_str and source_id_str.isdigit() else None
    if not candidate.source_id:
        default_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.is_active == True,
            RecruitmentSource.name.ilike("%College / Campus%"),
        ).first()
        candidate.source_id = default_source.id if default_source else None

    status_val = form.get("status", "")
    if status_val:
        candidate.status = CandidateStatus(status_val)

    candidate.institution_name = institution.name
    db.commit()
    add_flash(request, "Student updated successfully.", "success")
    return RedirectResponse(url="/institutions/students", status_code=302)
