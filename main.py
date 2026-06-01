from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Depends
from sqlalchemy import text, inspect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from config import settings
from database import Base, engine, get_db, SessionLocal
from routers.auth import router as auth_router
from routers.admin import router as admin_router
from routers.candidates import router as candidates_router
from routers.institutions import router as institutions_router
from routers.config import router as config_router
from routers.dashboard import router as dashboard_router
from routers.jobs import router as jobs_router
from routers.reports import router as reports_router
from models.recruitment_source import RecruitmentSource, SourceType
from models.job_profile import JobProfile
from models.user import User, UserRole, LicenseType
from models.field_agent_location import FieldAgentLocationLog
from models.candidate_access_log import CandidateAccessLog
from models.app_config import AppConfig
from services.auth import create_user
from utils import add_flash, clear_session, get_session_timeout_minutes

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)


@app.middleware("http")
async def log_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        import traceback
        traceback.print_exc()
        raise


@app.middleware("http")
async def session_timeout_middleware(request: Request, call_next):
    if "session" not in request.scope:
        return await call_next(request)

    user_id = request.session.get("user_id")
    if user_id:
        last_activity = request.session.get("last_activity")
        last_dt = None
        if last_activity:
            try:
                last_dt = datetime.fromisoformat(last_activity)
            except ValueError:
                last_dt = None

        db = SessionLocal()
        try:
            timeout_minutes = get_session_timeout_minutes(db)
        finally:
            db.close()

        if last_dt:
            now = datetime.utcnow()
            if now - last_dt > timedelta(minutes=timeout_minutes):
                clear_session(request)
                add_flash(request, "Session timed out due to inactivity. Please log in again.", "warning")
                return RedirectResponse(url="/login", status_code=302)

        request.session["last_activity"] = datetime.utcnow().isoformat()

    return await call_next(request)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(candidates_router)
app.include_router(institutions_router)
app.include_router(config_router)
app.include_router(jobs_router)
app.include_router(reports_router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    ensure_seed_password_column()
    ensure_job_posting_company_columns()
    ensure_app_config_phone_view_column()
    ensure_report_definition_columns()
    ensure_candidate_institution_columns()
    ensure_candidate_passing_out_year_column()
    ensure_candidate_layam_registration_column()
    ensure_candidate_employed_column()
    ensure_candidate_assigned_recruiter_column()
    ensure_candidate_lock_columns()
    ensure_candidate_round1_status_column()
    ensure_candidate_round1_reason_columns()
    ensure_candidate_aadhaar_columns()
    ensure_candidate_document_columns()
    ensure_candidate_employment_stage_columns()
    ensure_job_profile_industry_column()
    ensure_job_posting_approval_status()
    ensure_seed_job_profiles()


def ensure_seed_password_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("users")}
    except Exception:
        columns = set()

    if "seed_password" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN seed_password VARCHAR(255)"))
        conn.commit()


def ensure_job_posting_company_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("job_postings")}
    except Exception:
        return

    statements = []
    if "company_id" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN company_id INTEGER")
    if "company_name" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN company_name VARCHAR(200)")
    if "industry" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN industry VARCHAR(120)")
    if "city" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN city VARCHAR(100)")
    if "state" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN state VARCHAR(100)")
    if "address" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN address TEXT")
    if "plant_address" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN plant_address TEXT")

    if not statements:
        return

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def ensure_app_config_phone_view_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("app_configs")}
    except Exception:
        columns = set()

    if "phone_view_user_ids" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE app_configs ADD COLUMN phone_view_user_ids TEXT"))
        conn.commit()


def ensure_report_definition_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("report_definitions")}
    except Exception:
        return

    if "is_template" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE report_definitions ADD COLUMN is_template BOOLEAN DEFAULT 0"))
        conn.commit()


def ensure_candidate_institution_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    if "institution_name" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidates ADD COLUMN institution_name VARCHAR(200)"))
        conn.commit()


def ensure_candidate_passing_out_year_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    if "passing_out_year" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidates ADD COLUMN passing_out_year INTEGER"))
        conn.commit()


def ensure_candidate_layam_registration_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    if "registered_with_layam" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidates ADD COLUMN registered_with_layam BOOLEAN DEFAULT 0"))
        conn.commit()


def ensure_candidate_employed_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    if "employed" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidates ADD COLUMN employed BOOLEAN DEFAULT 0"))
        conn.commit()


def ensure_candidate_assigned_recruiter_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    if "assigned_recruiter_id" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidates ADD COLUMN assigned_recruiter_id INTEGER"))
        conn.commit()


def ensure_candidate_lock_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    statements = []
    if "locked_job_id" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN locked_job_id INTEGER")
    if "locked_at" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN locked_at DATETIME")

    if not statements:
        return

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def ensure_candidate_round1_status_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    if "round1_status" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE candidates ADD COLUMN round1_status VARCHAR(30)"))
        conn.commit()


def ensure_candidate_round1_reason_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    statements = []
    if "round1_not_shortlisted_reason" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN round1_not_shortlisted_reason TEXT")
    if "unlock_reason" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN unlock_reason TEXT")

    if not statements:
        return

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def ensure_candidate_employment_stage_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    statements = []
    if "employment_stage" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN employment_stage VARCHAR(40)")
    if "stage_reason" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN stage_reason TEXT")
    if "stage_updated_at" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN stage_updated_at DATETIME")

    if not statements:
        return

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def ensure_candidate_aadhaar_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    statements = []
    if "aadhaar_number" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_number VARCHAR(20)")
    if "aadhaar_doc_path" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_doc_path VARCHAR(255)")
    if "aadhaar_ocr_status" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_ocr_status VARCHAR(20)")
    if "aadhaar_ocr_notes" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_ocr_notes TEXT")
    if "aadhaar_ocr_text" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_ocr_text TEXT")
    if "aadhaar_ocr_name" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_ocr_name VARCHAR(120)")
    if "aadhaar_ocr_dob" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_ocr_dob VARCHAR(20)")
    if "aadhaar_ocr_number" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN aadhaar_ocr_number VARCHAR(20)")

    if not statements:
        return

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def ensure_candidate_document_columns() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("candidates")}
    except Exception:
        return

    statements = []
    if "education_doc_path" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN education_doc_path VARCHAR(255)")
    if "bank_doc_path" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN bank_doc_path VARCHAR(255)")
    if "resume_doc_path" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN resume_doc_path VARCHAR(255)")
    if "education_docs" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN education_docs TEXT")
    if "bank_docs" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN bank_docs TEXT")
    if "resume_docs" not in columns:
        statements.append("ALTER TABLE candidates ADD COLUMN resume_docs TEXT")

    if not statements:
        return

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def ensure_job_posting_approval_status() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("job_postings")}
    except Exception:
        return

    statements = []
    if "approval_status" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN approval_status VARCHAR(30) DEFAULT 'pending'")
    if "assigned_recruiter_id" not in columns:
        statements.append("ALTER TABLE job_postings ADD COLUMN assigned_recruiter_id INTEGER")

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        if "status" in columns:
            conn.execute(text("UPDATE job_postings SET status = 'active' WHERE status IN ('pending_approval','draft')"))
        conn.execute(text("UPDATE job_postings SET approval_status = 'pending' WHERE approval_status IS NULL OR approval_status = ''"))
        conn.commit()


def ensure_job_profile_industry_column() -> None:
    inspector = inspect(engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("job_profiles")}
    except Exception:
        return

    if "industry" in columns:
        return

    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE job_profiles ADD COLUMN industry VARCHAR(120)"))
        conn.commit()


def ensure_seed_job_profiles() -> None:
    db = SessionLocal()
    try:
        seed_profiles = [
            {
                "industry": "Manufacturing - Automotive",
                "designation": "Assembly Line Operator",
                "min_experience": 0,
                "max_experience": 2,
                "skills": "Assembly, torque tools, SOP adherence, 5S",
                "jd_summary": "- Assemble automotive components as per line SOPs\n- Follow takt time and line balance targets\n- Perform in-process quality checks\n- Handle tools and fixtures safely\n- Maintain 5S and station discipline",
                "technical_responsibilities": "- Use torque tools and gauges to spec\n- Fit sub-assemblies and fasteners correctly\n- Verify part orientation and clearances\n- Record defects and rework data\n- Calibrate basic tools as instructed",
                "functional_responsibilities": "- Follow shift plans and start-up checks\n- Escalate quality or safety issues quickly\n- Keep station clean and organized\n- Support cross-training on nearby stations\n- Adhere to PPE and safety protocols",
            },
            {
                "industry": "Manufacturing - Automotive",
                "designation": "Quality Inspector",
                "min_experience": 1,
                "max_experience": 4,
                "skills": "Quality inspection, gauges, SPC, defect reporting",
                "jd_summary": "- Inspect incoming, in-process, and final parts\n- Ensure compliance with drawing tolerances\n- Track defect trends and containment\n- Support line audits and quality gates\n- Maintain accurate inspection records",
                "technical_responsibilities": "- Measure parts using gauges and calipers\n- Perform visual and dimensional inspections\n- Record SPC readings and check charts\n- Verify tool calibration status\n- Raise NCRs for non-conforming parts",
                "functional_responsibilities": "- Communicate defects to line and supervisors\n- Coordinate rework and re-inspection\n- Maintain inspection logs and traceability\n- Support customer or internal audits\n- Train operators on quality checkpoints",
            },
            {
                "industry": "Manufacturing - Automotive",
                "designation": "CNC Machine Operator",
                "min_experience": 2,
                "max_experience": 5,
                "skills": "CNC setup, tooling, blueprint reading, offsets",
                "jd_summary": "- Operate CNC machines for precision parts\n- Read drawings and interpret tolerances\n- Maintain quality and reduce scrap\n- Achieve output as per shift targets\n- Follow safe machine operating practices",
                "technical_responsibilities": "- Set up tools, fixtures, and offsets\n- Run programs and monitor cycles\n- Inspect first-off and in-process parts\n- Adjust parameters within limits\n- Maintain tooling and coolant checks",
                "functional_responsibilities": "- Record production and inspection data\n- Escalate machine issues promptly\n- Follow preventive maintenance checklists\n- Maintain tool life and usage logs\n- Support continuous improvement tasks",
            },
            {
                "industry": "Manufacturing - Automotive",
                "designation": "Maintenance Technician",
                "min_experience": 3,
                "max_experience": 6,
                "skills": "Preventive maintenance, PLC basics, hydraulics, pneumatics",
                "jd_summary": "- Maintain production equipment and utilities\n- Reduce downtime through timely PM\n- Troubleshoot mechanical and electrical issues\n- Support line changeovers and setup\n- Ensure compliance with safety standards",
                "technical_responsibilities": "- Execute preventive maintenance schedules\n- Diagnose PLC, sensor, and drive faults\n- Repair hydraulics and pneumatics\n- Replace spares and align components\n- Verify machine safety interlocks",
                "functional_responsibilities": "- Coordinate shutdown windows with production\n- Maintain maintenance logs and spares usage\n- Escalate repeat issues for RCA\n- Train operators on basic checks\n- Follow lockout-tagout procedures",
            },
            {
                "industry": "Manufacturing - Automotive",
                "designation": "Production Supervisor",
                "min_experience": 4,
                "max_experience": 8,
                "skills": "Line supervision, shift planning, OEE, team leadership",
                "jd_summary": "- Lead production shifts to meet output and quality\n- Manage manpower and line balance\n- Drive OEE and defect reduction\n- Ensure compliance with safety rules\n- Coordinate with QA and maintenance",
                "technical_responsibilities": "- Monitor hourly output and downtime\n- Enforce SOP adherence and quality gates\n- Analyze OEE, scrap, and rework data\n- Validate changeovers and setup checks\n- Implement corrective actions on defects",
                "functional_responsibilities": "- Plan shift staffing and assignments\n- Coach operators and conduct briefings\n- Manage shift handovers and reporting\n- Escalate material or equipment issues\n- Drive continuous improvement tasks",
            },
            {
                "industry": "Manufacturing - Mobile Devices",
                "designation": "SMT Line Operator",
                "min_experience": 0,
                "max_experience": 2,
                "skills": "SMT, ESD handling, feeder setup, visual inspection",
                "jd_summary": "- Operate SMT line for PCB assembly\n- Follow ESD and cleanroom practices\n- Maintain consistent line throughput\n- Inspect boards for visible defects\n- Support smooth changeovers",
                "technical_responsibilities": "- Load feeders and verify placements\n- Monitor solder paste and stencil quality\n- Inspect AOI results and mark defects\n- Perform basic machine checks\n- Replace reels and validate part numbers",
                "functional_responsibilities": "- Maintain line logs and shift records\n- Escalate component shortages quickly\n- Follow ESD compliance and PPE\n- Keep workstation organized and clean\n- Support training and line balance",
            },
            {
                "industry": "Manufacturing - Mobile Devices",
                "designation": "PCB Repair Technician",
                "min_experience": 2,
                "max_experience": 5,
                "skills": "Soldering, rework, microscopes, hot air station",
                "jd_summary": "- Diagnose PCB faults and perform repairs\n- Improve repair yield and turnaround time\n- Follow rework SOPs and ESD rules\n- Support quality with defect analysis\n- Maintain rework traceability",
                "technical_responsibilities": "- Rework SMD components using hot air\n- Use microscope for inspection and alignment\n- Validate repairs with functional tests\n- Reball or replace ICs if required\n- Document repair steps and results",
                "functional_responsibilities": "- Track repair yields and rework counts\n- Communicate recurring defects to QA\n- Maintain rework tools and consumables\n- Follow safety and ESD compliance\n- Support training for junior techs",
            },
            {
                "industry": "Manufacturing - Mobile Devices",
                "designation": "Final Assembly Technician",
                "min_experience": 1,
                "max_experience": 3,
                "skills": "Precision assembly, ESD handling, torque tools",
                "jd_summary": "- Assemble device subcomponents with precision\n- Maintain cosmetic quality standards\n- Meet takt time and output targets\n- Follow ESD safety and handling rules\n- Support final packaging checks",
                "technical_responsibilities": "- Install displays, batteries, and housings\n- Tighten fasteners to torque specs\n- Run basic function checks post-assembly\n- Verify cosmetic defects and alignment\n- Use approved fixtures and tools",
                "functional_responsibilities": "- Maintain station cleanliness and 5S\n- Report defect trends to line lead\n- Ensure packaging and labeling accuracy\n- Follow shift handover routines\n- Support training and rotation",
            },
            {
                "industry": "Manufacturing - Mobile Devices",
                "designation": "Functional Test Technician",
                "min_experience": 2,
                "max_experience": 5,
                "skills": "Functional testing, test fixtures, diagnostics",
                "jd_summary": "- Execute functional tests on finished devices\n- Diagnose test failures and route for rework\n- Maintain test equipment and fixtures\n- Ensure accurate test documentation\n- Improve first-pass yield",
                "technical_responsibilities": "- Operate test stations and fixtures\n- Analyze failure codes and logs\n- Validate firmware or calibration steps\n- Perform retests after rework\n- Maintain calibration status of testers",
                "functional_responsibilities": "- Update test records and traceability\n- Coordinate with repair and QA teams\n- Escalate recurring issues to engineering\n- Follow ESD and safety rules\n- Support shift reports and metrics",
            },
            {
                "industry": "Manufacturing - Mobile Devices",
                "designation": "Line Lead",
                "min_experience": 3,
                "max_experience": 6,
                "skills": "Line balancing, throughput, people management",
                "jd_summary": "- Lead production line to meet output goals\n- Balance stations and reduce bottlenecks\n- Ensure quality and ESD compliance\n- Track KPIs and corrective actions\n- Coach and develop operators",
                "technical_responsibilities": "- Monitor takt time and throughput\n- Validate process checkpoints and audits\n- Analyze defects and rework data\n- Support changeovers and setup readiness\n- Maintain line tooling and fixtures",
                "functional_responsibilities": "- Assign manpower and manage breaks\n- Conduct shift briefs and handovers\n- Escalate material or equipment issues\n- Maintain production and quality logs\n- Drive continuous improvement",
            },
            {
                "industry": "Manufacturing - Electronics",
                "designation": "Electronics Technician",
                "min_experience": 1,
                "max_experience": 3,
                "skills": "Component testing, multimeter, soldering",
                "jd_summary": "- Support electronics assembly and testing\n- Troubleshoot basic circuit issues\n- Maintain quality and safety standards\n- Follow IPC and ESD requirements\n- Document test outcomes accurately",
                "technical_responsibilities": "- Test components using multimeter tools\n- Verify solder joints and continuity\n- Rework minor defects as per SOP\n- Use test fixtures and jigs\n- Perform basic functional checks",
                "functional_responsibilities": "- Maintain test logs and traceability\n- Coordinate with QA on failure trends\n- Keep tools and benches organized\n- Follow ESD and safety protocols\n- Support line audits and checks",
            },
            {
                "industry": "Manufacturing - Electronics",
                "designation": "Wiring Harness Assembler",
                "min_experience": 0,
                "max_experience": 2,
                "skills": "Crimping, wiring, schematics, cable routing",
                "jd_summary": "- Build wiring harnesses to drawings\n- Maintain crimp and routing quality\n- Follow color codes and labeling\n- Meet production targets and takt\n- Maintain 5S at the workstation",
                "technical_responsibilities": "- Cut and crimp wires to length\n- Assemble connectors and terminals\n- Route harnesses per layout boards\n- Apply labels and sleeves correctly\n- Perform continuity checks",
                "functional_responsibilities": "- Follow work instructions precisely\n- Record output and defect data\n- Communicate shortages or issues\n- Maintain tool calibration checks\n- Support cross-training tasks",
            },
            {
                "industry": "Manufacturing - Electronics",
                "designation": "Test & Calibration Technician",
                "min_experience": 2,
                "max_experience": 5,
                "skills": "Calibration, test equipment, documentation",
                "jd_summary": "- Calibrate electronic products and instruments\n- Ensure compliance with standards\n- Maintain accurate calibration records\n- Support audit readiness\n- Improve test throughput and yield",
                "technical_responsibilities": "- Run calibration procedures and routines\n- Verify outputs against reference tools\n- Adjust settings within allowed limits\n- Maintain calibration fixtures and tools\n- Diagnose test failures and retest",
                "functional_responsibilities": "- Record results and certificates\n- Maintain calibration schedules\n- Coordinate with QA and production\n- Follow ESD and safety guidelines\n- Report recurring deviations",
            },
            {
                "industry": "Manufacturing - Electronics",
                "designation": "Quality Engineer",
                "min_experience": 3,
                "max_experience": 6,
                "skills": "Quality systems, audits, root cause analysis",
                "jd_summary": "- Drive quality improvement initiatives\n- Manage audits and compliance\n- Reduce defects and rework\n- Analyze customer and internal issues\n- Maintain quality metrics reporting",
                "technical_responsibilities": "- Conduct process audits and checks\n- Perform root cause analysis and CAPA\n- Review SPC and defect trend data\n- Validate process changes and controls\n- Approve quality documentation",
                "functional_responsibilities": "- Train teams on quality procedures\n- Coordinate with production and QA\n- Maintain customer complaint tracking\n- Report weekly quality KPIs\n- Support continuous improvement projects",
            },
            {
                "industry": "Manufacturing - Electronics",
                "designation": "Stores & Inventory Coordinator",
                "min_experience": 1,
                "max_experience": 4,
                "skills": "Inventory control, ERP, FIFO",
                "jd_summary": "- Manage component inventory and issuance\n- Ensure FIFO and traceability\n- Maintain stock accuracy and bin control\n- Support line feeding and kitting\n- Track critical component availability",
                "technical_responsibilities": "- Update ERP/WMS stock movements\n- Manage FIFO and batch tracking\n- Prepare kits for production lines\n- Perform cycle counts and reconciliation\n- Maintain labeling and storage standards",
                "functional_responsibilities": "- Coordinate with procurement on shortages\n- Communicate delays to production\n- Maintain inventory accuracy reports\n- Support audits and stock verifications\n- Follow safety and handling guidelines",
            },
            {
                "industry": "Logistics & Warehousing",
                "designation": "Warehouse Associate",
                "min_experience": 0,
                "max_experience": 2,
                "skills": "Picking, packing, barcode scanning, safety",
                "jd_summary": "- Pick, pack, and stage orders accurately\n- Maintain speed and accuracy targets\n- Follow packing and labeling standards\n- Handle goods safely and efficiently\n- Keep storage areas organized",
                "technical_responsibilities": "- Scan items using handheld devices\n- Pack to spec with correct materials\n- Label cartons and verify shipment IDs\n- Perform basic quality checks\n- Update WMS status for orders",
                "functional_responsibilities": "- Maintain aisle discipline and 5S\n- Report damages and discrepancies\n- Support inventory counts\n- Follow safety and lifting guidelines\n- Communicate issues to supervisors",
            },
            {
                "industry": "Logistics & Warehousing",
                "designation": "Forklift Operator",
                "min_experience": 1,
                "max_experience": 4,
                "skills": "Forklift operation, load handling, safety",
                "jd_summary": "- Operate forklifts to move pallets safely\n- Load and unload inbound shipments\n- Stack goods per storage guidelines\n- Perform daily equipment checks\n- Support dispatch and receiving flow",
                "technical_responsibilities": "- Inspect forklift before each shift\n- Move pallets to assigned locations\n- Use safe loading and stacking methods\n- Verify pallet labels and conditions\n- Follow speed limits and aisle rules",
                "functional_responsibilities": "- Maintain operation logs and checklists\n- Report equipment issues immediately\n- Follow safety and PPE guidelines\n- Assist with inventory movements\n- Coordinate with warehouse associates",
            },
            {
                "industry": "Logistics & Warehousing",
                "designation": "Inventory Controller",
                "min_experience": 2,
                "max_experience": 5,
                "skills": "Cycle counting, inventory accuracy, WMS",
                "jd_summary": "- Ensure inventory accuracy across locations\n- Plan and execute cycle counts\n- Reconcile variances with WMS\n- Improve inventory control processes\n- Support audit readiness",
                "technical_responsibilities": "- Perform daily/weekly cycle counts\n- Investigate and adjust variances\n- Maintain location and bin accuracy\n- Audit receiving and dispatch records\n- Prepare count reports",
                "functional_responsibilities": "- Coordinate counts with operations\n- Train teams on scanning accuracy\n- Improve stock accuracy KPIs\n- Communicate findings to leadership\n- Support physical inventory audits",
            },
            {
                "industry": "Logistics & Warehousing",
                "designation": "Dispatch Coordinator",
                "min_experience": 2,
                "max_experience": 5,
                "skills": "Dispatch planning, documentation, carrier coordination",
                "jd_summary": "- Plan dispatch schedules and allocations\n- Ensure accurate documentation for outbound\n- Coordinate with carriers and transporters\n- Meet OTIF and SLA requirements\n- Manage loading priorities",
                "technical_responsibilities": "- Prepare dispatch manifests and labels\n- Verify shipment quantities and SKUs\n- Coordinate truck arrival and loading\n- Update WMS and shipment statuses\n- Handle POD and document filing",
                "functional_responsibilities": "- Communicate delays and reschedules\n- Resolve dispatch issues with teams\n- Ensure compliance with safety rules\n- Track carrier performance\n- Maintain dispatch reports",
            },
            {
                "industry": "Logistics & Warehousing",
                "designation": "Logistics Supervisor",
                "min_experience": 4,
                "max_experience": 7,
                "skills": "Team leadership, SLA management, process improvement",
                "jd_summary": "- Lead warehouse teams to meet SLA targets\n- Monitor throughput and accuracy KPIs\n- Optimize layout and material flow\n- Ensure safety and compliance\n- Drive continuous improvement",
                "technical_responsibilities": "- Review daily KPIs and bottlenecks\n- Optimize pick paths and storage\n- Implement process improvements\n- Monitor equipment utilization\n- Validate operational reports",
                "functional_responsibilities": "- Plan shifts and manpower allocation\n- Coach team leads and associates\n- Handle escalation and issue resolution\n- Maintain stakeholder communication\n- Run daily performance meetings",
            },
            {
                "industry": "Healthcare Support",
                "designation": "Hospital Attendant",
                "min_experience": 0,
                "max_experience": 2,
                "skills": "Patient transport, hygiene protocols, basic care",
                "jd_summary": "- Support patient movement and ward assistance\n- Maintain hygiene and infection control\n- Assist with basic patient needs\n- Follow safety and privacy guidelines\n- Coordinate with nursing staff",
                "technical_responsibilities": "- Transfer patients using safe techniques\n- Assist in bed making and sanitation\n- Follow disinfection and hygiene SOPs\n- Handle basic equipment safely\n- Report patient support requirements",
                "functional_responsibilities": "- Coordinate with nurses and attendants\n- Maintain cleanliness in assigned areas\n- Follow shift schedules and handovers\n- Document completed tasks as required\n- Adhere to patient confidentiality",
            },
            {
                "industry": "Healthcare Support",
                "designation": "Patient Care Assistant",
                "min_experience": 1,
                "max_experience": 3,
                "skills": "Vital monitoring, basic care, documentation",
                "jd_summary": "- Assist nurses with routine patient care\n- Monitor vitals and basic observations\n- Support mobility, feeding, and hygiene\n- Maintain accurate patient records\n- Follow care plans and safety rules",
                "technical_responsibilities": "- Record temperature, BP, and pulse\n- Assist in patient mobility and repositioning\n- Support feeding and hygiene needs\n- Use basic equipment safely\n- Escalate abnormal readings promptly",
                "functional_responsibilities": "- Update patient charts and logs\n- Coordinate with nursing staff\n- Follow infection control protocols\n- Maintain patient dignity and privacy\n- Support shift handovers",
            },
            {
                "industry": "Healthcare Support",
                "designation": "Phlebotomy Technician",
                "min_experience": 1,
                "max_experience": 3,
                "skills": "Blood collection, labeling, patient handling",
                "jd_summary": "- Collect blood samples for diagnostics\n- Ensure correct labeling and handling\n- Follow patient safety and comfort protocols\n- Maintain sample integrity and hygiene\n- Support lab processing requirements",
                "technical_responsibilities": "- Perform venipuncture and sample collection\n- Label and verify patient identifiers\n- Handle collection kits and supplies\n- Maintain cold chain if required\n- Dispose sharps safely",
                "functional_responsibilities": "- Explain procedures to patients\n- Follow infection control guidelines\n- Maintain sample logs and records\n- Coordinate with lab for pickups\n- Report collection issues promptly",
            },
            {
                "industry": "Healthcare Support",
                "designation": "Sterilization Technician",
                "min_experience": 2,
                "max_experience": 4,
                "skills": "Sterilization, equipment handling, infection control",
                "jd_summary": "- Sterilize medical instruments and packs\n- Operate autoclaves and sterilizers\n- Maintain sterilization cycle records\n- Ensure infection control compliance\n- Support OT and ward supply needs",
                "technical_responsibilities": "- Run autoclave cycles to specifications\n- Inspect instruments before and after cycles\n- Package and label sterile packs\n- Monitor temperature and pressure logs\n- Perform routine equipment checks",
                "functional_responsibilities": "- Maintain compliance documentation\n- Coordinate with OT and wards on demand\n- Ensure proper storage of sterile items\n- Follow safety and PPE protocols\n- Report equipment faults quickly",
            },
            {
                "industry": "Healthcare Support",
                "designation": "Facility Supervisor",
                "min_experience": 4,
                "max_experience": 7,
                "skills": "Facility management, vendor coordination, compliance",
                "jd_summary": "- Oversee facility services and support staff\n- Ensure housekeeping and hygiene standards\n- Manage vendors and service contracts\n- Track compliance and safety checks\n- Drive service quality improvements",
                "technical_responsibilities": "- Monitor housekeeping and sanitation audits\n- Schedule maintenance and service routines\n- Verify compliance checklists and reports\n- Manage vendor SLAs and performance\n- Track equipment and facility issues",
                "functional_responsibilities": "- Lead facility teams and supervisors\n- Coordinate with hospital administration\n- Handle escalations and urgent requests\n- Maintain compliance documentation\n- Conduct team training and briefings",
            },
        ]

        for item in seed_profiles:
            existing = (
                db.query(JobProfile)
                .filter(
                    JobProfile.designation == item["designation"],
                    JobProfile.industry == item["industry"],
                )
                .first()
            )
            if existing:
                existing.role_title = item["designation"]
                existing.designation = item["designation"]
                existing.industry = item["industry"]
                existing.min_experience = item["min_experience"]
                existing.max_experience = item["max_experience"]
                existing.skills = item["skills"]
                existing.jd_summary = item["jd_summary"]
                existing.technical_responsibilities = item["technical_responsibilities"]
                existing.functional_responsibilities = item["functional_responsibilities"]
                continue
            db.add(
                JobProfile(
                    role_title=item["designation"],
                    designation=item["designation"],
                    industry=item["industry"],
                    min_experience=item["min_experience"],
                    max_experience=item["max_experience"],
                    skills=item["skills"],
                    jd_summary=item["jd_summary"],
                    technical_responsibilities=item["technical_responsibilities"],
                    functional_responsibilities=item["functional_responsibilities"],
                    is_active=True,
                )
            )
        db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/seed-admin")
def seed_admin(db: Session = Depends(get_db)):
    """Create initial admin if none exists. Remove this in production."""
    existing = db.query(User).filter(User.role == UserRole.ADMIN).first()
    if existing:
        return {"status": "exists", "username": existing.username}

    admin = create_user(
        db,
        username="admin",
        email="admin@staffindia.com",
        password="Admin@123",
        role=UserRole.ADMIN,
        license_type=LicenseType.ENTERPRISE,
        full_name="Super Admin",
    )
    return {"status": "created", "username": admin.username, "password": "Admin@123"}


@app.get("/seed-staff")
def seed_staff(db: Session = Depends(get_db)):
    """Seed managers, recruiters, and field agents. Remove this in production."""
    admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
    created_by_id = admin.id if admin else None

    staff = [
        {
            "username": "mgr.priya",
            "email": "priya.sharma@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.MANAGER,
            "license_type": LicenseType.PROFESSIONAL,
            "full_name": "Priya Sharma",
            "phone": "9876543210",
            "employee_id": "MGR001",
            "date_of_birth": datetime(1986, 4, 12),
            "gender": "Female",
            "address": "B-12, Sector 62, Noida, Uttar Pradesh",
            "qualification": "MBA (HR)",
            "experience_years": 12,
            "department": "Operations",
            "emergency_contact": "Amit Sharma - 9810011122",
            "joining_date": datetime(2019, 6, 10),
            "reporting_manager": "Super Admin",
            "created_by_id": created_by_id,
        },
        {
            "username": "mgr.arjun",
            "email": "arjun.mehta@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.MANAGER,
            "license_type": LicenseType.PROFESSIONAL,
            "full_name": "Arjun Mehta",
            "phone": "9812345678",
            "employee_id": "MGR002",
            "date_of_birth": datetime(1984, 11, 3),
            "gender": "Male",
            "address": "22, Linking Road, Bandra West, Mumbai, Maharashtra",
            "qualification": "MBA (Finance)",
            "experience_years": 14,
            "department": "Recruitment",
            "emergency_contact": "Neha Mehta - 9820099988",
            "joining_date": datetime(2018, 3, 15),
            "reporting_manager": "Super Admin",
            "created_by_id": created_by_id,
        },
        {
            "username": "mgr.nidhi",
            "email": "nidhi.gupta@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.MANAGER,
            "license_type": LicenseType.PROFESSIONAL,
            "full_name": "Nidhi Gupta",
            "phone": "9898989898",
            "employee_id": "MGR003",
            "date_of_birth": datetime(1988, 8, 21),
            "gender": "Female",
            "address": "Plot 7, Banjara Hills, Hyderabad, Telangana",
            "qualification": "MBA (HR)",
            "experience_years": 11,
            "department": "Delivery",
            "emergency_contact": "Ravi Gupta - 9848012345",
            "joining_date": datetime(2020, 1, 20),
            "reporting_manager": "Super Admin",
            "created_by_id": created_by_id,
        },
        {
            "username": "rec.karan",
            "email": "karan.singh@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.RECRUITER,
            "license_type": LicenseType.BASIC,
            "full_name": "Karan Singh",
            "phone": "9876501234",
            "employee_id": "REC001",
            "date_of_birth": datetime(1991, 2, 14),
            "gender": "Male",
            "address": "41, MG Road, Bengaluru, Karnataka",
            "qualification": "BBA",
            "experience_years": 6,
            "department": "IT Hiring",
            "emergency_contact": "Rita Singh - 9886001122",
            "joining_date": datetime(2021, 7, 5),
            "reporting_manager": "Priya Sharma",
            "created_by_id": created_by_id,
        },
        {
            "username": "rec.ananya",
            "email": "ananya.iyer@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.RECRUITER,
            "license_type": LicenseType.BASIC,
            "full_name": "Ananya Iyer",
            "phone": "9900011223",
            "employee_id": "REC002",
            "date_of_birth": datetime(1993, 9, 9),
            "gender": "Female",
            "address": "12, Anna Nagar East, Chennai, Tamil Nadu",
            "qualification": "MBA (HR)",
            "experience_years": 5,
            "department": "BFSI Hiring",
            "emergency_contact": "Vikram Iyer - 9940012233",
            "joining_date": datetime(2022, 2, 10),
            "reporting_manager": "Arjun Mehta",
            "created_by_id": created_by_id,
        },
        {
            "username": "rec.rohit",
            "email": "rohit.verma@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.RECRUITER,
            "license_type": LicenseType.BASIC,
            "full_name": "Rohit Verma",
            "phone": "9811122233",
            "employee_id": "REC003",
            "date_of_birth": datetime(1990, 5, 30),
            "gender": "Male",
            "address": "55, Gomti Nagar, Lucknow, Uttar Pradesh",
            "qualification": "B.Com",
            "experience_years": 7,
            "department": "Sales Hiring",
            "emergency_contact": "Sonal Verma - 9935011223",
            "joining_date": datetime(2020, 11, 18),
            "reporting_manager": "Nidhi Gupta",
            "created_by_id": created_by_id,
        },
        {
            "username": "rec.sana",
            "email": "sana.khan@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.RECRUITER,
            "license_type": LicenseType.BASIC,
            "full_name": "Sana Khan",
            "phone": "9922003344",
            "employee_id": "REC004",
            "date_of_birth": datetime(1992, 12, 1),
            "gender": "Female",
            "address": "9, Koregaon Park, Pune, Maharashtra",
            "qualification": "MBA (HR)",
            "experience_years": 6,
            "department": "Healthcare Hiring",
            "emergency_contact": "Imran Khan - 9890011223",
            "joining_date": datetime(2021, 9, 27),
            "reporting_manager": "Priya Sharma",
            "created_by_id": created_by_id,
        },
        {
            "username": "rec.akash",
            "email": "akash.patel@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.RECRUITER,
            "license_type": LicenseType.BASIC,
            "full_name": "Akash Patel",
            "phone": "9890012345",
            "employee_id": "REC005",
            "date_of_birth": datetime(1994, 6, 17),
            "gender": "Male",
            "address": "18, Vastrapur, Ahmedabad, Gujarat",
            "qualification": "BBA",
            "experience_years": 4,
            "department": "Engineering Hiring",
            "emergency_contact": "Meera Patel - 9909901122",
            "joining_date": datetime(2022, 8, 8),
            "reporting_manager": "Arjun Mehta",
            "created_by_id": created_by_id,
        },
        {
            "username": "fa.ravi",
            "email": "ravi.nair@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.FIELD_AGENT,
            "license_type": LicenseType.BASIC,
            "full_name": "Ravi Nair",
            "phone": "9877001122",
            "employee_id": "FA001",
            "date_of_birth": datetime(1995, 3, 12),
            "gender": "Male",
            "address": "3, MG Road, Kochi, Kerala",
            "qualification": "BA",
            "experience_years": 3,
            "department": "Field Ops",
            "emergency_contact": "Anu Nair - 9895007788",
            "joining_date": datetime(2022, 4, 15),
            "reporting_manager": "Priya Sharma",
            "created_by_id": created_by_id,
        },
        {
            "username": "fa.kavya",
            "email": "kavya.reddy@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.FIELD_AGENT,
            "license_type": LicenseType.BASIC,
            "full_name": "Kavya Reddy",
            "phone": "9849007788",
            "employee_id": "FA002",
            "date_of_birth": datetime(1996, 7, 25),
            "gender": "Female",
            "address": "11, Jubilee Hills, Hyderabad, Telangana",
            "qualification": "B.Sc",
            "experience_years": 3,
            "department": "Field Ops",
            "emergency_contact": "Suresh Reddy - 9849001122",
            "joining_date": datetime(2022, 6, 20),
            "reporting_manager": "Nidhi Gupta",
            "created_by_id": created_by_id,
        },
        {
            "username": "fa.manjunath",
            "email": "manjunath.s@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.FIELD_AGENT,
            "license_type": LicenseType.BASIC,
            "full_name": "Manjunath S",
            "phone": "9886112233",
            "employee_id": "FA003",
            "date_of_birth": datetime(1994, 1, 5),
            "gender": "Male",
            "address": "27, Indiranagar, Bengaluru, Karnataka",
            "qualification": "Diploma",
            "experience_years": 4,
            "department": "Field Ops",
            "emergency_contact": "Lakshmi S - 9886003344",
            "joining_date": datetime(2021, 12, 1),
            "reporting_manager": "Arjun Mehta",
            "created_by_id": created_by_id,
        },
        {
            "username": "fa.simran",
            "email": "simran.kaur@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.FIELD_AGENT,
            "license_type": LicenseType.BASIC,
            "full_name": "Simran Kaur",
            "phone": "9911002233",
            "employee_id": "FA004",
            "date_of_birth": datetime(1997, 10, 10),
            "gender": "Female",
            "address": "24, Sector 35, Chandigarh",
            "qualification": "BA",
            "experience_years": 2,
            "department": "Field Ops",
            "emergency_contact": "Gurpreet Kaur - 9872001122",
            "joining_date": datetime(2023, 1, 12),
            "reporting_manager": "Priya Sharma",
            "created_by_id": created_by_id,
        },
        {
            "username": "fa.rahul",
            "email": "rahul.joshi@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.FIELD_AGENT,
            "license_type": LicenseType.BASIC,
            "full_name": "Rahul Joshi",
            "phone": "9822012345",
            "employee_id": "FA005",
            "date_of_birth": datetime(1995, 9, 19),
            "gender": "Male",
            "address": "16, Kothrud, Pune, Maharashtra",
            "qualification": "B.Com",
            "experience_years": 3,
            "department": "Field Ops",
            "emergency_contact": "Seema Joshi - 9822007788",
            "joining_date": datetime(2022, 10, 3),
            "reporting_manager": "Arjun Mehta",
            "created_by_id": created_by_id,
        },
        {
            "username": "fa.isha",
            "email": "isha.das@staffindia.com",
            "password": "Welcome@123",
            "seed_password": "Welcome@123",
            "role": UserRole.FIELD_AGENT,
            "license_type": LicenseType.BASIC,
            "full_name": "Isha Das",
            "phone": "9830011223",
            "employee_id": "FA006",
            "date_of_birth": datetime(1996, 4, 28),
            "gender": "Female",
            "address": "8, Salt Lake, Kolkata, West Bengal",
            "qualification": "B.Sc",
            "experience_years": 2,
            "department": "Field Ops",
            "emergency_contact": "Anil Das - 9830013344",
            "joining_date": datetime(2023, 3, 5),
            "reporting_manager": "Nidhi Gupta",
            "created_by_id": created_by_id,
        },
    ]

    created = []
    skipped = []
    for payload in staff:
        existing = db.query(User).filter(
            (User.username == payload["username"]) |
            (User.email == payload["email"]) |
            (User.employee_id == payload["employee_id"])
        ).first()
        if existing:
            skipped.append(payload["username"])
            continue
        create_user(db, **payload)
        created.append(payload["username"])

    return {
        "status": "ok",
        "created": created,
        "skipped": skipped,
        "default_password": "Welcome@123",
        "counts": {"created": len(created), "skipped": len(skipped)},
    }


@app.get("/seed-recruitment-sources")
def seed_recruitment_sources(db: Session = Depends(get_db)):
    """Seed default recruitment sources for candidate registration."""
    defaults = [
        {"name": "College / Campus", "source_type": SourceType.COLLEGE},
        {"name": "Social Media", "source_type": SourceType.SOCIAL_MEDIA},
        {"name": "Site In-Charge", "source_type": SourceType.SITE_IN_CHARGE},
        {"name": "Job Portal", "source_type": SourceType.JOB_PORTAL},
        {"name": "Website Inquiry", "source_type": SourceType.WEBSITE_INQUIRY},
        {"name": "Field Representative", "source_type": SourceType.FIELD_REPRESENTATIVE},
        {"name": "Direct Call / Walk-in", "source_type": SourceType.DIRECT_CALL},
    ]

    created = []
    skipped = []
    for item in defaults:
        existing = db.query(RecruitmentSource).filter(
            RecruitmentSource.name == item["name"],
            RecruitmentSource.source_type == item["source_type"],
        ).first()
        if existing:
            skipped.append(item["name"])
            continue
        db.add(RecruitmentSource(**item))
        created.append(item["name"])

    db.commit()

    return {
        "status": "ok",
        "created": created,
        "skipped": skipped,
        "counts": {"created": len(created), "skipped": len(skipped)},
    }
