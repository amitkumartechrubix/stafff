from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from database import get_db
from models.candidate import Candidate, CandidateStatus
from models.user import User, UserRole
from models.recruitment_source import RecruitmentSource, SourceType
from models.interview import (
    InterviewRound,
    InterviewQuestion,
    CandidateInterviewResponse,
    CandidateCustomQuestionResponse,
)
from models.job_posting import JobPosting
from utils import add_flash, build_template_context, require_auth
from utils import add_flash, build_template_context, require_auth, mask_phone
from config import settings
from datetime import datetime, date, timedelta
import json
import os
import re
from uuid import uuid4
from difflib import SequenceMatcher
from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse
import csv
import io
import openpyxl
import xlrd
from typing import Dict, Iterable
from PIL import Image
import pytesseract

FILTER_FIELDS = {
    "full_name": {"label": "Name", "type": "string"},
    "phone": {"label": "Phone", "type": "string"},
    "email": {"label": "Email", "type": "string"},
    "age": {"label": "Age", "type": "number"},
    "qualification": {"label": "Qualification", "type": "string"},
    "city": {"label": "City", "type": "string"},
    "state": {"label": "State", "type": "string"},
    "status": {"label": "Status", "type": "enum"},
    "source_name": {"label": "Source", "type": "string"},
}

OPERATOR_OPTIONS = [
    ("eq", "Equals"),
    ("neq", "Not equals"),
    ("contains", "Contains"),
    ("startswith", "Starts with"),
    ("endswith", "Ends with"),
    ("gt", "Greater than"),
    ("gte", "Greater or equal"),
    ("lt", "Less than"),
    ("lte", "Less or equal"),
    ("between", "Between"),
    ("is_null", "Is empty"),
    ("not_null", "Is not empty"),
]

AADHAAR_UPLOAD_DIR = os.path.join("static", "uploads", "aadhaar")
DOC_UPLOAD_DIR = os.path.join("static", "uploads", "candidate_docs")
AADHAAR_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DOC_ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if os.path.exists(TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
OCR_NAME_STOPWORDS = {
    "government", "india", "dob", "date", "birth", "year", "male", "female",
    "aadhaar", "authority", "uidai", "unique",
    "authentication", "scan", "scanning", "qr", "code", "offline", "xml",
    "download", "print", "enrollment",
}


def parse_number(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    return None


def apply_custom_filters(query, filters: Iterable[Dict[str, str]]):
    needs_source_join = False
    for fil in filters:
        field = (fil.get("field") or "").strip()
        operator = (fil.get("operator") or "").strip()
        value = (fil.get("value") or "").strip()
        value_to = (fil.get("value_to") or "").strip()

        if field not in FILTER_FIELDS or operator not in dict(OPERATOR_OPTIONS):
            continue

        column = getattr(Candidate, field, None)
        if field == "source_name":
            needs_source_join = True
            column = RecruitmentSource.name

        if column is None:
            continue

        if operator == "is_null":
            query = query.filter(column.is_(None) | (column == ""))
            continue
        if operator == "not_null":
            query = query.filter(column.isnot(None) & (column != ""))
            continue

        field_type = FILTER_FIELDS[field]["type"]
        parsed = value
        parsed_to = value_to
        if field_type == "number":
            parsed = parse_number(value)
            parsed_to = parse_number(value_to)
        if field_type == "enum" and value:
            parsed = value.lower()

        if operator == "eq" and value:
            query = query.filter(column == parsed)
        elif operator == "neq" and value:
            query = query.filter(column != parsed)
        elif operator == "contains" and value:
            query = query.filter(column.ilike(f"%{value}%"))
        elif operator == "startswith" and value:
            query = query.filter(column.ilike(f"{value}%"))
        elif operator == "endswith" and value:
            query = query.filter(column.ilike(f"%{value}"))
        elif operator == "gt" and parsed is not None:
            query = query.filter(column > parsed)
        elif operator == "gte" and parsed is not None:
            query = query.filter(column >= parsed)
        elif operator == "lt" and parsed is not None:
            query = query.filter(column < parsed)
        elif operator == "lte" and parsed is not None:
            query = query.filter(column <= parsed)
        elif operator == "between" and parsed is not None and parsed_to is not None:
            query = query.filter(column.between(parsed, parsed_to))

    if needs_source_join:
        query = query.join(RecruitmentSource, Candidate.source_id == RecruitmentSource.id, isouter=True)
    return query

router = APIRouter(prefix="/candidates")
templates = Jinja2Templates(directory="templates")

ALLOWED_ROLES = ("admin", "employer", "manager", "recruiter", "field_agent")
RESTRICTED_PHONE_ROLES = ("manager", "recruiter", "field_agent")
from models.candidate_access_log import CandidateAccessLog
STATUS_ACTIVE = "active"
APPROVAL_APPROVED = "approved"
ROUND_ONE = 1
ROUND1_SHORTLISTED = "shortlisted"
ROUND1_NOT_SHORTLISTED = "not_shortlisted"
STAGE_ORDER = [
    "available",
    "locked",
    "shortlisted",
    "employer_interview",
    "document_collection",
    "placed",
    "rejected",
]
STAGE_LABELS = {
    "available": "Available",
    "locked": "Locked",
    "shortlisted": "Shortlisted",
    "employer_interview": "Employer Interview",
    "document_collection": "Document Collection",
    "placed": "Placed",
    "rejected": "Rejected",
}
REJECTED_RELEASE_AFTER_DAYS = 1


@router.post("/{cid}/phone/reveal")
async def reveal_candidate_phone(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        return {"ok": False, "error": "Candidate not found."}

    ip_address = _client_ip(request)
    user_agent = request.headers.get("user-agent")
    log_candidate_access(
        db,
        user_id=user.id,
        candidate_id=candidate.id,
        action="reveal_phone",
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return {
        "ok": True,
        "phone": candidate.phone,
        "masked": mask_phone(candidate.phone, user.role.value),
        "expires_in": 10,
    }


@router.post("/phone/screenshot-log")
async def log_screenshot_attempt(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    candidate_id = payload.get("candidate_id")
    if candidate_id is not None:
        try:
            candidate_id = int(candidate_id)
        except (TypeError, ValueError):
            candidate_id = None

    ip_address = _client_ip(request)
    user_agent = request.headers.get("user-agent")
    log_candidate_access(
        db,
        user_id=user.id,
        candidate_id=candidate_id,
        action="screenshot_attempt",
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return {"ok": True}


def _parse_doc_list(raw: str | None, fallback: str | None = None) -> list[str]:
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        except json.JSONDecodeError:
            pass
    if fallback:
        return [fallback]
    return []


def _serialize_doc_list(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    return json.dumps(cleaned)


@router.post("/{cid}/stage")
async def candidate_stage_update(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value not in {"admin", "manager", "recruiter"}:
        return RedirectResponse(url="/unauthorized", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    form = await request.form()
    stage = (form.get("stage") or "").strip().lower()
    reason = (form.get("reason") or "").strip()
    if stage not in STAGE_ORDER:
        add_flash(request, "Invalid stage selected.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    if stage == "rejected" and not reason:
        add_flash(request, "Reason is required for rejection.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    candidate.employment_stage = stage
    candidate.stage_reason = reason if stage == "rejected" else None
    candidate.stage_updated_at = datetime.utcnow()

    if stage in {"rejected", "available"}:
        candidate.assigned_recruiter_id = None
        candidate.locked_job_id = None
        candidate.locked_at = None
    if stage == "placed":
        candidate.status = CandidateStatus.PLACED
        candidate.employed = True

    db.commit()
    add_flash(request, "Employment stage updated.", "success")
    return RedirectResponse(url=f"/candidates/{cid}", status_code=302)


@router.post("/{cid}/documents")
async def candidate_documents_upload(
    cid: int,
    request: Request,
    db: Session = Depends(get_db),
    education_doc: UploadFile | list[UploadFile] | None = File(None),
    bank_doc: UploadFile | list[UploadFile] | None = File(None),
    resume_doc: UploadFile | list[UploadFile] | None = File(None),
    aadhaar_doc: UploadFile | None = File(None),
):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value not in {"admin", "manager", "recruiter"}:
        return RedirectResponse(url="/unauthorized", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if resolve_stage(candidate) != "document_collection":
        add_flash(request, "Documents can only be uploaded during Document Collection.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    os.makedirs(DOC_UPLOAD_DIR, exist_ok=True)
    saved_any = False

    async def save_doc(upload: UploadFile) -> str | None:
        if not upload or not getattr(upload, "filename", None):
            return None
        _, ext = os.path.splitext(upload.filename)
        ext = ext.lower()
        if ext not in DOC_ALLOWED_EXTENSIONS:
            return "__invalid__"
        filename = f"{uuid4().hex}{ext}"
        file_path = os.path.join(DOC_UPLOAD_DIR, filename)
        content = await upload.read()
        with open(file_path, "wb") as target:
            target.write(content)
        return f"/static/uploads/candidate_docs/{filename}"

    def normalize_uploads(value: UploadFile | list[UploadFile] | None) -> list[UploadFile]:
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [value]

    doc_fields = {
        "education": normalize_uploads(education_doc),
        "bank": normalize_uploads(bank_doc),
        "resume": normalize_uploads(resume_doc),
    }

    existing = {
        "education": _parse_doc_list(candidate.education_docs, candidate.education_doc_path),
        "bank": _parse_doc_list(candidate.bank_docs, candidate.bank_doc_path),
        "resume": _parse_doc_list(candidate.resume_docs, candidate.resume_doc_path),
    }

    if aadhaar_doc and getattr(aadhaar_doc, "filename", None):
        _, ext = os.path.splitext(aadhaar_doc.filename)
        ext = ext.lower()
        if ext not in AADHAAR_ALLOWED_EXTENSIONS:
            add_flash(request, "Aadhaar upload must be a JPG or PNG image.", "danger")
            return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

        os.makedirs(AADHAAR_UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid4().hex}{ext}"
        file_path = os.path.join(AADHAAR_UPLOAD_DIR, filename)
        content = await aadhaar_doc.read()
        with open(file_path, "wb") as target:
            target.write(content)

        candidate.aadhaar_doc_path = f"/static/uploads/aadhaar/{filename}"
        ocr_data = extract_aadhaar_data(file_path)
        status, notes = evaluate_aadhaar_match(
            candidate_name=candidate.full_name,
            candidate_dob=candidate.date_of_birth,
            aadhaar_number=candidate.aadhaar_number,
            ocr_data=ocr_data,
        )
        candidate.aadhaar_ocr_status = status
        candidate.aadhaar_ocr_notes = notes
        candidate.aadhaar_ocr_text = ocr_data.get("text") if ocr_data else None
        candidate.aadhaar_ocr_name = ocr_data.get("name") if ocr_data else None
        candidate.aadhaar_ocr_dob = ocr_data.get("dob") if ocr_data else None
        candidate.aadhaar_ocr_number = ocr_data.get("number") if ocr_data else None
        if not candidate.aadhaar_number and ocr_data and ocr_data.get("number"):
            candidate.aadhaar_number = normalize_digits(ocr_data.get("number"))
        saved_any = True

    for field_name, uploads in doc_fields.items():
        for upload in uploads:
            if not upload or not getattr(upload, "filename", None):
                continue
            result = await save_doc(upload)
            if result == "__invalid__":
                add_flash(request, f"Unsupported file type for {field_name} document.", "danger")
                return RedirectResponse(url=f"/candidates/{cid}", status_code=302)
            if result:
                saved_any = True
                existing[field_name].append(result)

    if saved_any:
        candidate.education_docs = _serialize_doc_list(existing["education"])
        candidate.bank_docs = _serialize_doc_list(existing["bank"])
        candidate.resume_docs = _serialize_doc_list(existing["resume"])
        candidate.education_doc_path = existing["education"][0] if existing["education"] else None
        candidate.bank_doc_path = existing["bank"][0] if existing["bank"] else None
        candidate.resume_doc_path = existing["resume"][0] if existing["resume"] else None
        db.commit()
        add_flash(request, "Documents uploaded successfully.", "success")
    else:
        add_flash(request, "Please choose at least one document to upload.", "warning")

    return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

CSV_HEADERS = [
    "full_name",
    "phone",
    "email",
    "age",
    "date_of_birth",
    "gender",
    "qualification",
    "location",
    "city",
    "state",
    "skills",
    "experience_years",
    "preferred_job_type",
    "expected_salary",
    "source",
    "source_id",
    "notes",
]

HEADER_ALIASES = {
    "name": "full_name",
    "full name": "full_name",
    "candidate name": "full_name",
    "phone": "phone",
    "phone number": "phone",
    "mobile": "phone",
    "email": "email",
    "email id": "email",
    "age": "age",
    "dob": "date_of_birth",
    "date of birth": "date_of_birth",
    "gender": "gender",
    "qualification": "qualification",
    "location": "location",
    "city": "city",
    "state": "state",
    "skills": "skills",
    "experience": "experience_years",
    "experience years": "experience_years",
    "preferred job type": "preferred_job_type",
    "job type": "preferred_job_type",
    "expected salary": "expected_salary",
    "source": "source",
    "recruitment source": "source",
    "source id": "source_id",
    "notes": "notes",
}


def normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def parse_date(value: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for sep in ("/", "-"):
        parts = value.split(sep)
        if len(parts) == 3:
            day, month, year = parts
            if len(year) == 2:
                year = f"20{year}"
            try:
                return date(int(year), int(month), int(day))
            except ValueError:
                return None
    return None


def compute_age(dob: date | None) -> int | None:
    if not dob:
        return None
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age if age >= 0 else None


def normalize_digits(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch.lower() for ch in value if ch.isalpha() or ch.isspace()).strip()


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def log_candidate_access(
    db: Session,
    *,
    user_id: int,
    candidate_id: int | None,
    action: str,
    ip_address: str | None,
    user_agent: str | None,
):
    db.add(
        CandidateAccessLog(
            user_id=user_id,
            candidate_id=candidate_id,
            action=action,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
    db.commit()


def resolve_stage(candidate: Candidate) -> str:
    if candidate.employment_stage:
        if candidate.employment_stage == "selected":
            return "shortlisted"
        if candidate.employment_stage in {"cancelled", "cancelled_rejected"}:
            return "rejected"
        if candidate.employment_stage == "screening_completed":
            return "shortlisted"
        if candidate.employment_stage == "hired":
            return "placed"
        return candidate.employment_stage
    if candidate.locked_job_id:
        return "locked"
    return "available"


def maybe_release_rejected(candidate: Candidate) -> bool:
    if candidate.employment_stage != "rejected":
        return False
    if not candidate.stage_updated_at:
        return False
    cutoff = datetime.utcnow() - timedelta(days=REJECTED_RELEASE_AFTER_DAYS)
    if candidate.stage_updated_at > cutoff:
        return False

    candidate.employment_stage = "available"
    candidate.stage_reason = None
    candidate.stage_updated_at = datetime.utcnow()
    candidate.assigned_recruiter_id = None
    candidate.locked_job_id = None
    candidate.locked_at = None
    return True


def fuzzy_name_match(name_a: str | None, name_b: str | None, threshold: float = 0.78) -> bool:
    a = normalize_name(name_a)
    b = normalize_name(name_b)
    if not a or not b:
        return False
    if a == b:
        return True

    a_tokens = [tok for tok in a.split() if tok]
    b_tokens = [tok for tok in b.split() if tok]
    if not a_tokens or not b_tokens:
        return False

    if all(tok in b_tokens for tok in a_tokens) or all(tok in a_tokens for tok in b_tokens):
        return True

    a_sorted = " ".join(sorted(a_tokens))
    b_sorted = " ".join(sorted(b_tokens))
    if SequenceMatcher(a=a_sorted, b=b_sorted).ratio() >= threshold:
        return True

    ratio = SequenceMatcher(a=a, b=b).ratio()
    return ratio >= threshold


def extract_aadhaar_name(lines: list[str]) -> str | None:
    for line in lines:
        match = re.search(r"\bname\b[:\s-]*(.+)", line, re.IGNORECASE)
        if not match:
            continue
        candidate = re.sub(r"[^A-Za-z ]", " ", match.group(1)).strip()
        if candidate and len(candidate.split()) >= 2:
            return candidate

    candidates = []
    for line in lines:
        cleaned = re.sub(r"[^A-Za-z ]", " ", line).strip()
        if not cleaned or len(cleaned.split()) < 2:
            continue
        lower = cleaned.lower()
        if any(stop in lower for stop in OCR_NAME_STOPWORDS):
            continue
        candidates.append(cleaned)
    if not candidates:
        return None
    return max(candidates, key=len)


def extract_aadhaar_data(image_path: str) -> dict:
    try:
        text = pytesseract.image_to_string(Image.open(image_path))
    except Exception as exc:
        return {"error": str(exc)}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text_flat = " ".join(lines)

    number_match = re.search(r"\b(\d{4})\s?(\d{4})\s?(\d{4})\b", text_flat)
    aadhaar_number = "".join(number_match.groups()) if number_match else None

    dob_match = re.search(
        r"(DOB|Date of Birth|Year of Birth)[:\s-]*([0-9]{2}[/-][0-9]{2}[/-][0-9]{4}|[0-9]{4})",
        text_flat,
        re.IGNORECASE,
    )
    aadhaar_dob = dob_match.group(2) if dob_match else None

    aadhaar_name = extract_aadhaar_name(lines)

    return {
        "text": text,
        "name": aadhaar_name,
        "dob": aadhaar_dob,
        "number": aadhaar_number,
    }


def evaluate_aadhaar_match(
    *,
    candidate_name: str | None,
    candidate_dob: date | None,
    aadhaar_number: str | None,
    ocr_data: dict,
) -> tuple[str, str]:
    if not ocr_data or ocr_data.get("error"):
        return "error", ocr_data.get("error", "OCR failed")

    notes = []
    status = "partial"

    ocr_number = normalize_digits(ocr_data.get("number"))
    input_number = normalize_digits(aadhaar_number)
    if input_number and ocr_number:
        if input_number == ocr_number:
            status = "partial"
        else:
            notes.append("Aadhaar number mismatch")
            status = "mismatch"
    elif input_number or ocr_number:
        notes.append("Aadhaar number missing on one side")

    ocr_name = ocr_data.get("name")
    if candidate_name and ocr_name:
        if not fuzzy_name_match(candidate_name, ocr_name):
            cand_norm = normalize_name(candidate_name)
            ocr_norm = normalize_name(ocr_name)
            cand_tokens = [tok for tok in cand_norm.split() if tok]
            ocr_tokens = [tok for tok in ocr_norm.split() if tok]
            ratio = SequenceMatcher(a=cand_norm, b=ocr_norm).ratio() if cand_norm and ocr_norm else 0.0
            notes.append(
                "Name mismatch (candidate='{}' ocr='{}' tokens={} vs {} ratio={:.2f})".format(
                    cand_norm, ocr_norm, cand_tokens, ocr_tokens, ratio
                )
            )
            status = "mismatch"
    elif candidate_name or ocr_name:
        notes.append("Name missing on one side")

    ocr_dob = ocr_data.get("dob")
    if candidate_dob and ocr_dob:
        dob_match = False
        try:
            if re.fullmatch(r"\d{4}", ocr_dob):
                dob_match = candidate_dob.year == int(ocr_dob)
            else:
                parsed = parse_date(ocr_dob)
                dob_match = parsed == candidate_dob if parsed else False
        except Exception:
            dob_match = False
        if not dob_match:
            notes.append("DOB mismatch")
            status = "mismatch"
    elif candidate_dob or ocr_dob:
        notes.append("DOB missing on one side")

    if status != "mismatch" and input_number and ocr_number and candidate_name and ocr_name:
        if not notes:
            status = "verified"
    if not notes and status == "partial":
        notes.append("OCR completed")

    return status, "; ".join(notes)


def resolve_aadhaar_path(doc_path: str) -> str:
    safe_path = doc_path.lstrip("/")
    return os.path.normpath(safe_path)


def map_row(row: Dict[str, str]) -> Dict[str, str]:
    mapped: Dict[str, str] = {}
    for key, val in row.items():
        if key is None:
            continue
        normalized = normalize_header(str(key))
        target = HEADER_ALIASES.get(normalized, normalized)
        mapped[target] = str(val).strip() if val is not None else ""
    return mapped


def iter_csv_rows(content: bytes) -> Iterable[Dict[str, str]]:
    text = content.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        yield map_row(row)


def iter_xlsx_rows(content: bytes) -> Iterable[Dict[str, str]]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    headers = next(rows, None)
    if not headers:
        return
    header_list = [str(h or "") for h in headers]
    for row in rows:
        row_dict = {header_list[idx]: (row[idx] if idx < len(row) else "") for idx in range(len(header_list))}
        yield map_row(row_dict)


def iter_xls_rows(content: bytes) -> Iterable[Dict[str, str]]:
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    if sheet.nrows < 1:
        return
    headers = [str(sheet.cell_value(0, col) or "") for col in range(sheet.ncols)]
    for row_idx in range(1, sheet.nrows):
        row_dict = {headers[col]: sheet.cell_value(row_idx, col) for col in range(sheet.ncols)}
        yield map_row(row_dict)


def iter_upload_rows(filename: str, content: bytes) -> Iterable[Dict[str, str]]:
    name = filename.lower()
    if name.endswith(".csv"):
        return iter_csv_rows(content)
    if name.endswith(".xlsx"):
        return iter_xlsx_rows(content)
    if name.endswith(".xls"):
        return iter_xls_rows(content)
    raise ValueError("Unsupported file format. Please upload CSV, XLS, or XLSX.")


@router.get("")
async def candidates_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    search: str = "",
    status: str = "",
    state: str = "",
    city: str = "",
):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    query = db.query(Candidate).filter(
        or_(
            Candidate.registered_with_layam == True,
            Candidate.institution_name.is_(None),
            Candidate.institution_name == "",
        )
    )
    if user.role == UserRole.FIELD_AGENT:
        query = query.filter(Candidate.registered_by_id == user.id)
    if user.role == UserRole.RECRUITER:
        query = query.filter(Candidate.assigned_recruiter_id == user.id)
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

    custom_filters = []
    if user.role == UserRole.MANAGER:
        fields = request.query_params.getlist("filter_field")
        operators = request.query_params.getlist("filter_operator")
        values = request.query_params.getlist("filter_value")
        values_to = request.query_params.getlist("filter_value_to")
        for idx, field in enumerate(fields):
            custom_filters.append({
                "field": field or "",
                "operator": operators[idx] if idx < len(operators) else "eq",
                "value": values[idx] if idx < len(values) else "",
                "value_to": values_to[idx] if idx < len(values_to) else "",
            })
        query = apply_custom_filters(query, custom_filters)

    total = query.count()
    per_page = settings.ITEMS_PER_PAGE
    candidates = (
        query.order_by(Candidate.registered_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    released = False
    for cand in candidates:
        if maybe_release_rejected(cand):
            released = True
    if released:
        db.commit()

    round_one_completed_ids = set()
    if candidates:
        round_one = (
            db.query(InterviewRound)
            .filter(InterviewRound.round_number == ROUND_ONE)
            .first()
        )
        if round_one:
            candidate_ids = [c.id for c in candidates]
            base_ids = {
                row[0]
                for row in (
                    db.query(CandidateInterviewResponse.candidate_id)
                    .filter(
                        CandidateInterviewResponse.round_id == round_one.id,
                        CandidateInterviewResponse.candidate_id.in_(candidate_ids),
                    )
                    .distinct()
                    .all()
                )
            }
            custom_ids = {
                row[0]
                for row in (
                    db.query(CandidateCustomQuestionResponse.candidate_id)
                    .filter(
                        CandidateCustomQuestionResponse.round_id == round_one.id,
                        CandidateCustomQuestionResponse.candidate_id.in_(candidate_ids),
                    )
                    .distinct()
                    .all()
                )
            }
            round_one_completed_ids = base_ids | custom_ids

    recruiters = []
    if user.role in (UserRole.MANAGER, UserRole.ADMIN):
        recruiters = (
            db.query(User)
            .filter(User.role == UserRole.RECRUITER, User.is_active == True)
            .order_by(User.full_name)
            .all()
        )

    stage_map = {cand.id: resolve_stage(cand) for cand in candidates}

    ctx = build_template_context(
        request, db,
        candidates=candidates,
        stage_map=stage_map,
        stage_labels=STAGE_LABELS,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        search=search,
        status_filter=status,
        state_filter=state,
        city_filter=city,
        statuses=CandidateStatus,
        filter_fields=FILTER_FIELDS,
        operator_options=OPERATOR_OPTIONS,
        custom_filters=custom_filters,
        recruiters=recruiters,
        round_one_completed_ids=round_one_completed_ids,
        page_title="Candidate Register",
    )
    return templates.TemplateResponse("candidates/list.html", ctx)


@router.post("/assign")
async def candidates_assign(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role not in (UserRole.MANAGER, UserRole.ADMIN):
        add_flash(request, "Only managers or admins can assign candidates.", "warning")
        return RedirectResponse(url="/candidates", status_code=302)

    form = await request.form()
    candidate_ids = [cid for cid in form.getlist("candidate_ids") if cid and cid.isdigit()]
    recruiter_id = (form.get("assigned_recruiter_id") or "").strip()

    if not candidate_ids:
        add_flash(request, "Select at least one candidate to assign.", "warning")
        return RedirectResponse(url="/candidates", status_code=302)

    recruiter = None
    if recruiter_id:
        if not recruiter_id.isdigit():
            add_flash(request, "Invalid recruiter selection.", "warning")
            return RedirectResponse(url="/candidates", status_code=302)
        recruiter = db.query(User).filter(
            User.id == int(recruiter_id),
            User.role == UserRole.RECRUITER,
            User.is_active == True,
        ).first()
        if not recruiter:
            add_flash(request, "Recruiter not found.", "danger")
            return RedirectResponse(url="/candidates", status_code=302)

    db.query(Candidate).filter(Candidate.id.in_(candidate_ids)).update(
        {Candidate.assigned_recruiter_id: recruiter.id if recruiter else None},
        synchronize_session=False,
    )
    db.commit()

    if recruiter:
        add_flash(request, f"Assigned {len(candidate_ids)} candidate(s) to {recruiter.full_name}.", "success")
    else:
        add_flash(request, f"Cleared assignment for {len(candidate_ids)} candidate(s).", "info")
    return RedirectResponse(url="/candidates", status_code=302)


@router.get("/new")
async def candidate_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir
    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    default_source_id = None
    if user.role == UserRole.FIELD_AGENT:
        field_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.name == "Field Representative",
            RecruitmentSource.is_active == True,
        ).first()
        if not field_source:
            field_source = RecruitmentSource(
                name="Field Representative",
                source_type=SourceType.FIELD_REPRESENTATIVE,
                is_active=True,
            )
            db.add(field_source)
            db.commit()
            sources.append(field_source)
        default_source_id = field_source.id
    ctx = build_template_context(
        request, db,
        sources=sources,
        default_source_id=default_source_id,
        page_title="Register New Candidate",
        form_action="/candidates/new",
        edit_candidate=None,
    )
    return templates.TemplateResponse("candidates/form.html", ctx)


@router.get("/register")
async def candidate_public_register_form(request: Request, db: Session = Depends(get_db)):
    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    default_source = next((s for s in sources if s.name and s.name.lower() == "field agent qr"), None)
    ref = request.query_params.get("ref")
    setup_type = request.query_params.get("setup_type", "").strip()
    setup_location = request.query_params.get("setup_location", "").strip()
    if setup_type:
        source_name = f"Field Representative - {setup_type}"
        existing_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.name == source_name,
            RecruitmentSource.is_active == True,
        ).first()
        if not existing_source:
            existing_source = RecruitmentSource(
                name=source_name,
                source_type=SourceType.FIELD_REPRESENTATIVE,
                is_active=True,
            )
            db.add(existing_source)
            db.commit()
        default_source = existing_source
    ctx = build_template_context(
        request, db,
        sources=sources,
        default_source_id=default_source.id if default_source else None,
        page_title="Candidate Registration",
        form_action="/candidates/register",
        ref=ref,
        setup_type=setup_type,
        setup_location=setup_location,
    )
    return templates.TemplateResponse("candidates/public_register.html", ctx)


@router.post("/register")
async def candidate_public_register_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    full_name = form.get("full_name", "").strip()
    phone = form.get("phone", "").strip()

    if not full_name or not phone:
        add_flash(request, "Full name and phone are required.", "danger")
        return RedirectResponse(url="/candidates/register", status_code=302)

    existing = db.query(Candidate).filter(Candidate.phone == phone).first()
    if existing:
        add_flash(request, "Phone number already registered.", "danger")
        return RedirectResponse(url="/candidates/register", status_code=302)

    dob_str = form.get("date_of_birth", "")
    dob = date.fromisoformat(dob_str) if dob_str else None
    age = compute_age(dob)

    exp_str = form.get("experience_years", "0")
    exp = int(exp_str) if exp_str and exp_str.isdigit() else 0

    source_id_str = form.get("source_id", "")
    source_id = int(source_id_str) if source_id_str and source_id_str.isdigit() else None
    setup_type = form.get("setup_type", "").strip()
    if setup_type:
        source_name = f"Field Representative - {setup_type}"
        existing_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.name == source_name,
            RecruitmentSource.is_active == True,
        ).first()
        if not existing_source:
            existing_source = RecruitmentSource(
                name=source_name,
                source_type=SourceType.FIELD_REPRESENTATIVE,
                is_active=True,
            )
            db.add(existing_source)
            db.flush()
        source_id = existing_source.id
    if not source_id:
        default_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.is_active == True,
            RecruitmentSource.name.ilike("%Field Agent QR%"),
        ).first()
        source_id = default_source.id if default_source else None

    passing_year_str = form.get("passing_out_year", "")
    passing_out_year = int(passing_year_str) if passing_year_str and passing_year_str.isdigit() else None

    ref = form.get("ref")
    registered_by_id = None
    if ref and ref.isdigit():
        ref_user = db.query(User).filter(User.id == int(ref)).first()
        if ref_user and ref_user.role == UserRole.FIELD_AGENT:
            registered_by_id = ref_user.id

    setup_location = form.get("setup_location", "").strip()
    location_val = form.get("location", "").strip() or setup_location or None

    candidate = Candidate(
        full_name=full_name,
        phone=phone,
        email=form.get("email", "").strip() or None,
        location=location_val,
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
        registered_by_id=registered_by_id,
        employment_stage="available",
        stage_updated_at=datetime.utcnow(),
    )
    db.add(candidate)
    db.commit()

    add_flash(request, "Registration complete. Our team will contact you soon.", "success")
    return RedirectResponse(url="/candidates/register", status_code=302)


@router.get("/{cid}")
async def candidate_view(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if maybe_release_rejected(candidate):
        db.commit()

    assigned_positions = []
    round_one_responses = []
    round_one_custom_responses = []
    round_one_completed = False
    if user.role.value == "recruiter":
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

    round_one = (
        db.query(InterviewRound)
        .filter(InterviewRound.round_number == ROUND_ONE)
        .first()
    )
    if round_one:
        response_rows = (
            db.query(InterviewQuestion, CandidateInterviewResponse)
            .join(
                CandidateInterviewResponse,
                CandidateInterviewResponse.question_id == InterviewQuestion.id,
            )
            .filter(
                CandidateInterviewResponse.candidate_id == candidate.id,
                CandidateInterviewResponse.round_id == round_one.id,
            )
            .order_by(InterviewQuestion.id.asc())
            .all()
        )
        round_one_responses = [
            {
                "question_text": question.question_text,
                "response_text": response.response_text or "",
            }
            for question, response in response_rows
        ]
        round_one_custom_responses = (
            db.query(CandidateCustomQuestionResponse)
            .filter(
                CandidateCustomQuestionResponse.candidate_id == candidate.id,
                CandidateCustomQuestionResponse.round_id == round_one.id,
            )
            .order_by(CandidateCustomQuestionResponse.id.asc())
            .all()
        )
        round_one_completed = bool(round_one_responses or round_one_custom_responses)

    ctx = build_template_context(
        request, db,
        candidate=candidate,
        education_docs=_parse_doc_list(candidate.education_docs, candidate.education_doc_path),
        bank_docs=_parse_doc_list(candidate.bank_docs, candidate.bank_doc_path),
        resume_docs=_parse_doc_list(candidate.resume_docs, candidate.resume_doc_path),
        assigned_positions=assigned_positions,
        round_one_responses=round_one_responses,
        round_one_custom_responses=round_one_custom_responses,
        round_one_completed=round_one_completed,
        stage_order=STAGE_ORDER,
        stage_labels=STAGE_LABELS,
        current_stage=resolve_stage(candidate),
        page_title=f"Candidate - {candidate.full_name}",
        back_url="/candidates",
        back_label="Back to Candidates",
        edit_url=f"/candidates/{cid}/edit",
    )
    return templates.TemplateResponse("candidates/view.html", ctx)


@router.post("/{cid}/aadhaar-ocr")
async def candidate_aadhaar_ocr(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if not candidate.aadhaar_doc_path:
        add_flash(request, "No Aadhaar document found to reprocess.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    abs_path = resolve_aadhaar_path(candidate.aadhaar_doc_path)
    if not os.path.exists(abs_path):
        add_flash(request, "Aadhaar document file is missing on disk.", "danger")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    ocr_data = extract_aadhaar_data(abs_path)
    status, notes = evaluate_aadhaar_match(
        candidate_name=candidate.full_name,
        candidate_dob=candidate.date_of_birth,
        aadhaar_number=candidate.aadhaar_number,
        ocr_data=ocr_data,
    )
    candidate.aadhaar_ocr_status = status
    candidate.aadhaar_ocr_notes = notes
    candidate.aadhaar_ocr_text = ocr_data.get("text") if ocr_data else None
    candidate.aadhaar_ocr_name = ocr_data.get("name") if ocr_data else None
    candidate.aadhaar_ocr_dob = ocr_data.get("dob") if ocr_data else None
    candidate.aadhaar_ocr_number = ocr_data.get("number") if ocr_data else None
    db.commit()

    add_flash(request, "Aadhaar OCR reprocessed.", "success")
    return RedirectResponse(url=f"/candidates/{cid}", status_code=302)


@router.get("/{cid}/round-1")
async def candidate_round_one(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "recruiter":
        return RedirectResponse(url="/unauthorized", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if candidate.assigned_recruiter_id != user.id:
        add_flash(request, "You can only interview candidates assigned to you.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    round_one = (
        db.query(InterviewRound)
        .filter(InterviewRound.round_number == ROUND_ONE, InterviewRound.is_active == True)
        .first()
    )
    if not round_one:
        add_flash(request, "Round 1 is not configured.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    questions = (
        db.query(InterviewQuestion)
        .filter(
            InterviewQuestion.round_id == round_one.id,
            InterviewQuestion.is_active == True,
        )
        .order_by(InterviewQuestion.id.asc())
        .all()
    )
    responses = {}
    custom_responses = []
    round_one_completed = False
    if questions:
        stored = (
            db.query(CandidateInterviewResponse)
            .filter(
                CandidateInterviewResponse.candidate_id == candidate.id,
                CandidateInterviewResponse.round_id == round_one.id,
                CandidateInterviewResponse.question_id.in_([q.id for q in questions]),
            )
            .all()
        )
        responses = {r.question_id: (r.response_text or "") for r in stored}
    custom_responses = (
        db.query(CandidateCustomQuestionResponse)
        .filter(
            CandidateCustomQuestionResponse.candidate_id == candidate.id,
            CandidateCustomQuestionResponse.round_id == round_one.id,
        )
        .order_by(CandidateCustomQuestionResponse.id.asc())
        .all()
    )
    round_one_completed = bool(responses or custom_responses)

    ctx = build_template_context(
        request, db,
        candidate=candidate,
        round_one=round_one,
        questions=questions,
        responses=responses,
        custom_responses=custom_responses,
        round_one_completed=round_one_completed,
        page_title=f"Round 1 — {candidate.full_name}",
        back_url=f"/candidates/{cid}",
        back_label="Back to Candidate",
    )
    return templates.TemplateResponse("candidates/round1.html", ctx)


@router.post("/{cid}/round-1")
async def candidate_round_one_submit(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "recruiter":
        return RedirectResponse(url="/unauthorized", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if candidate.assigned_recruiter_id != user.id:
        add_flash(request, "You can only interview candidates assigned to you.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    round_one = (
        db.query(InterviewRound)
        .filter(InterviewRound.round_number == ROUND_ONE, InterviewRound.is_active == True)
        .first()
    )
    if not round_one:
        add_flash(request, "Round 1 is not configured.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    questions = (
        db.query(InterviewQuestion)
        .filter(
            InterviewQuestion.round_id == round_one.id,
            InterviewQuestion.is_active == True,
        )
        .order_by(InterviewQuestion.id.asc())
        .all()
    )

    form = await request.form()
    for q in questions:
        key = f"q_{q.id}"
        response_text = (form.get(key) or "").strip()
        existing = (
            db.query(CandidateInterviewResponse)
            .filter(
                CandidateInterviewResponse.candidate_id == candidate.id,
                CandidateInterviewResponse.round_id == round_one.id,
                CandidateInterviewResponse.question_id == q.id,
            )
            .first()
        )
        if existing:
            existing.response_text = response_text
        else:
            db.add(
                CandidateInterviewResponse(
                    candidate_id=candidate.id,
                    round_id=round_one.id,
                    question_id=q.id,
                    response_text=response_text,
                )
            )

    custom_ids_raw = form.getlist("custom_id")
    custom_questions = form.getlist("custom_question")
    custom_responses = form.getlist("custom_response")
    existing_custom = (
        db.query(CandidateCustomQuestionResponse)
        .filter(
            CandidateCustomQuestionResponse.candidate_id == candidate.id,
            CandidateCustomQuestionResponse.round_id == round_one.id,
        )
        .all()
    )
    existing_map = {str(item.id): item for item in existing_custom}
    submitted_ids = {
        cid.strip()
        for cid in custom_ids_raw
        if (cid or "").strip().isdigit()
    }

    for idx, question_text in enumerate(custom_questions):
        response_text = custom_responses[idx] if idx < len(custom_responses) else ""
        question_text = (question_text or "").strip()
        response_text = (response_text or "").strip()
        if not question_text and not response_text:
            continue

        custom_id = custom_ids_raw[idx] if idx < len(custom_ids_raw) else ""
        if custom_id and custom_id.strip().isdigit() and custom_id in existing_map:
            existing_map[custom_id].question_text = question_text
            existing_map[custom_id].response_text = response_text
        else:
            db.add(
                CandidateCustomQuestionResponse(
                    candidate_id=candidate.id,
                    round_id=round_one.id,
                    question_text=question_text,
                    response_text=response_text,
                )
            )

    for item in existing_custom:
        if str(item.id) not in submitted_ids:
            db.delete(item)

    db.commit()
    add_flash(request, "Round 1 responses saved.", "success")
    return RedirectResponse(url=f"/candidates/{cid}", status_code=302)


@router.post("/{cid}/round-1/status")
async def candidate_round_one_status(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "recruiter":
        return RedirectResponse(url="/unauthorized", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if candidate.assigned_recruiter_id != user.id:
        add_flash(request, "You can only update assigned candidates.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    round_one = (
        db.query(InterviewRound)
        .filter(InterviewRound.round_number == ROUND_ONE, InterviewRound.is_active == True)
        .first()
    )
    if not round_one:
        add_flash(request, "Round 1 is not configured.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    has_base = (
        db.query(CandidateInterviewResponse.id)
        .filter(
            CandidateInterviewResponse.candidate_id == candidate.id,
            CandidateInterviewResponse.round_id == round_one.id,
        )
        .first()
    )
    has_custom = (
        db.query(CandidateCustomQuestionResponse.id)
        .filter(
            CandidateCustomQuestionResponse.candidate_id == candidate.id,
            CandidateCustomQuestionResponse.round_id == round_one.id,
        )
        .first()
    )
    if not (has_base or has_custom):
        add_flash(request, "Complete Round 1 responses before making a decision.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}/round-1", status_code=302)

    form = await request.form()
    decision = (form.get("decision") or "").strip().lower()
    reason = (form.get("reason") or "").strip()
    if decision not in {ROUND1_SHORTLISTED, ROUND1_NOT_SHORTLISTED}:
        add_flash(request, "Invalid Round 1 decision.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    if decision == ROUND1_NOT_SHORTLISTED and not reason:
        add_flash(request, "Reason is required for not shortlisting.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    candidate.round1_status = decision
    if decision == ROUND1_NOT_SHORTLISTED:
        candidate.round1_not_shortlisted_reason = reason
        candidate.assigned_recruiter_id = None
        candidate.locked_job_id = None
        candidate.locked_at = None
        candidate.employment_stage = "rejected"
        candidate.stage_reason = reason
        candidate.stage_updated_at = datetime.utcnow()
    else:
        candidate.round1_not_shortlisted_reason = None
        candidate.employment_stage = "shortlisted"
        candidate.stage_reason = None
        candidate.stage_updated_at = datetime.utcnow()

    db.commit()
    add_flash(request, "Round 1 decision saved.", "success")
    return RedirectResponse(url="/candidates", status_code=302)


@router.post("/{cid}/lock")
async def candidate_lock(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "recruiter":
        add_flash(request, "Only recruiters can lock candidates.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if candidate.assigned_recruiter_id != user.id:
        add_flash(request, "You can only lock candidates assigned to you.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    form = await request.form()
    position_id = (form.get("position_id") or "").strip()
    next_url = (form.get("next") or "").strip()
    redirect_url = next_url if next_url.startswith("/") else f"/candidates/{cid}"
    if not position_id.isdigit():
        add_flash(request, "Please select a position.", "warning")
        return RedirectResponse(url=redirect_url, status_code=302)

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
        return RedirectResponse(url=redirect_url, status_code=302)

    if candidate.locked_job_id and candidate.locked_job_id != selected_job.id:
        add_flash(request, "Candidate is locked to another position.", "warning")
        return RedirectResponse(url=redirect_url, status_code=302)

    candidate.locked_job_id = selected_job.id
    candidate.locked_at = datetime.utcnow()
    candidate.employment_stage = "locked"
    candidate.stage_updated_at = datetime.utcnow()
    db.commit()
    add_flash(request, "Candidate locked to selected position.", "success")
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/{cid}/unlock")
async def candidate_unlock(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if user.role.value != "recruiter":
        add_flash(request, "Only recruiters can unlock candidates.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    if candidate.assigned_recruiter_id != user.id:
        add_flash(request, "You can only unlock candidates assigned to you.", "warning")
        return RedirectResponse(url=f"/candidates/{cid}", status_code=302)

    form = await request.form()
    next_url = (form.get("next") or "").strip()
    reason = (form.get("reason") or "").strip()
    redirect_url = next_url if next_url.startswith("/") else f"/candidates/{cid}"

    if not reason:
        add_flash(request, "Reason is required for unlocking.", "warning")
        return RedirectResponse(url=redirect_url, status_code=302)

    if not candidate.locked_job_id:
        add_flash(request, "Candidate is not locked to any position.", "info")
        return RedirectResponse(url=redirect_url, status_code=302)

    candidate.locked_job_id = None
    candidate.locked_at = None
    candidate.unlock_reason = reason
    candidate.employment_stage = "available"
    candidate.stage_reason = None
    candidate.stage_updated_at = datetime.utcnow()
    candidate.assigned_recruiter_id = None
    db.commit()
    add_flash(request, "Candidate unlocked.", "success")
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/bulk-upload")
async def candidate_bulk_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir
    ctx = build_template_context(
        request, db,
        page_title="Bulk Upload Candidates",
    )
    return templates.TemplateResponse("candidates/bulk_upload.html", ctx)


@router.get("/bulk-template")
async def candidate_bulk_template(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=candidates_template.csv"},
    )


@router.post("/bulk-upload")
async def candidate_bulk_upload(
    request: Request,
    db: Session = Depends(get_db),
    upload_file: UploadFile = File(...),
):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    if not upload_file.filename:
        add_flash(request, "Please select a file to upload.", "danger")
        return RedirectResponse(url="/candidates/bulk-upload", status_code=302)

    content = await upload_file.read()
    try:
        rows = list(iter_upload_rows(upload_file.filename, content))
    except ValueError as exc:
        add_flash(request, str(exc), "danger")
        return RedirectResponse(url="/candidates/bulk-upload", status_code=302)

    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    source_map = {s.name.strip().lower(): s.id for s in sources if s.name}

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
            source_id=source_id,
            notes=row.get("notes", "").strip() or None,
            registered_by_id=user.id,
            employment_stage="available",
            stage_updated_at=datetime.utcnow(),
        )
        db.add(candidate)
        created += 1

    if created:
        db.commit()

    add_flash(request, f"Bulk upload complete. Created: {created}, Skipped: {skipped}.", "success")
    return RedirectResponse(url="/candidates", status_code=302)


@router.post("/new")
async def candidate_new_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    form = await request.form()
    full_name = form.get("full_name", "").strip()
    phone = form.get("phone", "").strip()

    if not full_name or not phone:
        add_flash(request, "Full name and phone are required.", "danger")
        return RedirectResponse(url="/candidates/new", status_code=302)

    dob_str = form.get("date_of_birth", "")
    dob = date.fromisoformat(dob_str) if dob_str else None

    age = compute_age(dob)

    exp_str = form.get("experience_years", "0")
    exp = int(exp_str) if exp_str and exp_str.isdigit() else 0

    source_id_str = form.get("source_id", "")
    source_id = int(source_id_str) if source_id_str and source_id_str.isdigit() else None
    if not source_id and user.role == UserRole.FIELD_AGENT:
        field_source = db.query(RecruitmentSource).filter(
            RecruitmentSource.name == "Field Representative",
            RecruitmentSource.is_active == True,
        ).first()
        if not field_source:
            field_source = RecruitmentSource(
                name="Field Representative",
                source_type=SourceType.FIELD_REPRESENTATIVE,
                is_active=True,
            )
            db.add(field_source)
            db.commit()
        source_id = field_source.id

    passing_year_str = form.get("passing_out_year", "")
    passing_out_year = int(passing_year_str) if passing_year_str and passing_year_str.isdigit() else None

    aadhaar_number = form.get("aadhaar_number", "").strip() or None
    aadhaar_file = form.get("aadhaar_file")
    aadhaar_doc_path = None
    aadhaar_ocr_data = None
    aadhaar_status = None
    aadhaar_notes = None

    if aadhaar_file and getattr(aadhaar_file, "filename", None):
        _, ext = os.path.splitext(aadhaar_file.filename)
        ext = ext.lower()
        if ext not in AADHAAR_ALLOWED_EXTENSIONS:
            add_flash(request, "Aadhaar upload must be a JPG or PNG image.", "danger")
            return RedirectResponse(url="/candidates/new", status_code=302)

        os.makedirs(AADHAAR_UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid4().hex}{ext}"
        file_path = os.path.join(AADHAAR_UPLOAD_DIR, filename)
        content = await aadhaar_file.read()
        with open(file_path, "wb") as target:
            target.write(content)

        aadhaar_doc_path = f"/static/uploads/aadhaar/{filename}"
        aadhaar_ocr_data = extract_aadhaar_data(file_path)
        aadhaar_status, aadhaar_notes = evaluate_aadhaar_match(
            candidate_name=full_name,
            candidate_dob=dob,
            aadhaar_number=aadhaar_number,
            ocr_data=aadhaar_ocr_data,
        )
    elif aadhaar_number:
        aadhaar_status = "pending"
        aadhaar_notes = "Aadhaar number provided without document."

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
        institution_name=form.get("institution_name", "").strip() or None,
        registered_by_id=user.id,
        aadhaar_number=aadhaar_number,
        aadhaar_doc_path=aadhaar_doc_path,
        aadhaar_ocr_status=aadhaar_status,
        aadhaar_ocr_notes=aadhaar_notes,
        aadhaar_ocr_text=(aadhaar_ocr_data.get("text") if aadhaar_ocr_data else None),
        aadhaar_ocr_name=(aadhaar_ocr_data.get("name") if aadhaar_ocr_data else None),
        aadhaar_ocr_dob=(aadhaar_ocr_data.get("dob") if aadhaar_ocr_data else None),
        aadhaar_ocr_number=(aadhaar_ocr_data.get("number") if aadhaar_ocr_data else None),
        employment_stage="available",
        stage_updated_at=datetime.utcnow(),
    )
    db.add(candidate)
    db.commit()

    add_flash(request, f"Candidate '{full_name}' registered successfully.", "success")
    return RedirectResponse(url="/candidates", status_code=302)


@router.get("/{cid}/edit")
async def candidate_edit_form(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    sources = db.query(RecruitmentSource).filter(RecruitmentSource.is_active == True).all()
    ctx = build_template_context(
        request, db,
        sources=sources,
        page_title=f"Edit Candidate — {candidate.full_name}",
        form_action=f"/candidates/{cid}/edit",
        edit_candidate=candidate,
        statuses=CandidateStatus,
    )
    return templates.TemplateResponse("candidates/form.html", ctx)


@router.post("/{cid}/edit")
async def candidate_edit_post(cid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    candidate = db.query(Candidate).filter(Candidate.id == cid).first()
    if not candidate:
        add_flash(request, "Candidate not found.", "danger")
        return RedirectResponse(url="/candidates", status_code=302)

    form = await request.form()
    previous_aadhaar_number = candidate.aadhaar_number or ""
    candidate.full_name = form.get("full_name", candidate.full_name).strip()
    phone_value = form.get("phone", candidate.phone).strip()
    if user.role.value in RESTRICTED_PHONE_ROLES and "X" in phone_value:
        phone_value = candidate.phone
    candidate.phone = phone_value
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
    institution_name = form.get("institution_name", "").strip()
    if institution_name:
        candidate.institution_name = institution_name

    exp_str = form.get("experience_years", "0")
    candidate.experience_years = int(exp_str) if exp_str and exp_str.isdigit() else 0

    dob_str = form.get("date_of_birth", "")
    candidate.date_of_birth = date.fromisoformat(dob_str) if dob_str else None
    candidate.age = compute_age(candidate.date_of_birth)

    source_id_str = form.get("source_id", "")
    candidate.source_id = int(source_id_str) if source_id_str and source_id_str.isdigit() else None

    status_val = form.get("status", "")
    if status_val:
        candidate.status = CandidateStatus(status_val)

    aadhaar_number = form.get("aadhaar_number", "").strip() or None
    aadhaar_file = form.get("aadhaar_file")
    candidate.aadhaar_number = aadhaar_number

    if aadhaar_file and getattr(aadhaar_file, "filename", None):
        _, ext = os.path.splitext(aadhaar_file.filename)
        ext = ext.lower()
        if ext not in AADHAAR_ALLOWED_EXTENSIONS:
            add_flash(request, "Aadhaar upload must be a JPG or PNG image.", "danger")
            return RedirectResponse(url=f"/candidates/{cid}/edit", status_code=302)

        os.makedirs(AADHAAR_UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid4().hex}{ext}"
        file_path = os.path.join(AADHAAR_UPLOAD_DIR, filename)
        content = await aadhaar_file.read()
        with open(file_path, "wb") as target:
            target.write(content)

        candidate.aadhaar_doc_path = f"/static/uploads/aadhaar/{filename}"
        ocr_data = extract_aadhaar_data(file_path)
        status, notes = evaluate_aadhaar_match(
            candidate_name=candidate.full_name,
            candidate_dob=candidate.date_of_birth,
            aadhaar_number=aadhaar_number,
            ocr_data=ocr_data,
        )
        candidate.aadhaar_ocr_status = status
        candidate.aadhaar_ocr_notes = notes
        candidate.aadhaar_ocr_text = ocr_data.get("text") if ocr_data else None
        candidate.aadhaar_ocr_name = ocr_data.get("name") if ocr_data else None
        candidate.aadhaar_ocr_dob = ocr_data.get("dob") if ocr_data else None
        candidate.aadhaar_ocr_number = ocr_data.get("number") if ocr_data else None
    else:
        if aadhaar_number and aadhaar_number != previous_aadhaar_number:
            candidate.aadhaar_ocr_status = "pending"
            candidate.aadhaar_ocr_notes = "Aadhaar number updated without new document."

    db.commit()
    add_flash(request, "Candidate updated successfully.", "success")
    return RedirectResponse(url="/candidates", status_code=302)
