from __future__ import annotations

import csv
import io
import json
from datetime import datetime, date, timedelta
from typing import Any, Dict, Iterable, List, Tuple

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from sqlalchemy.sql import sqltypes

from config import settings
from database import get_db
from models.user import User, UserRole
from models.candidate import Candidate
from models.company import Company
from models.institution import Institution
from models.job_profile import JobProfile
from models.job_posting import JobPosting
from models.recruitment_source import RecruitmentSource
from models.report import ReportDefinition, ReportShare
from utils import add_flash, build_template_context, require_auth, mask_phone, can_view_phone

router = APIRouter(prefix="/reports")
templates = Jinja2Templates(directory="templates")

ADMIN_ROLES = ("admin",)
CREATOR_ROLES = ("admin", "manager")
ALLOWED_ROLES = ("admin", "employer", "manager", "recruiter", "field_agent", "institution")
RESTRICTED_PHONE_ROLES = {"manager", "recruiter", "field_agent"}

REPORT_TABLES: Dict[str, Tuple[Any, str]] = {
    "users": (User, "Users"),
    "candidates": (Candidate, "Candidates"),
    "companies": (Company, "Companies"),
    "institutions": (Institution, "Institutions"),
    "job_profiles": (JobProfile, "Job Profiles"),
    "job_postings": (JobPosting, "Job Postings"),
    "recruitment_sources": (RecruitmentSource, "Recruitment Sources"),
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
    ("in", "In list"),
    ("is_null", "Is empty"),
    ("not_null", "Is not empty"),
]

TIME_PRESETS = {
    "last_7": 7,
    "last_30": 30,
    "last_90": 90,
}


def resolve_time_window(time_range: str | None, time_from: str | None, time_to: str | None):
    start_date = None
    end_date = None

    if time_range in TIME_PRESETS:
        days = TIME_PRESETS[time_range]
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
    elif time_range == "ytd":
        end_date = date.today()
        start_date = date(end_date.year, 1, 1)
    elif time_range == "mtd":
        end_date = date.today()
        start_date = date(end_date.year, end_date.month, 1)
    elif time_range == "custom":
        start_date = parse_date(time_from) if time_from else None
        end_date = parse_date(time_to) if time_to else None

    return start_date, end_date


def is_admin(user: User) -> bool:
    return user.role.value == "admin"


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


def to_bool(value: str) -> bool | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value in {"true", "1", "yes", "y"}:
        return True
    if value in {"false", "0", "no", "n"}:
        return False
    return None


def column_type_info(column: Any) -> str:
    col_type = column.type
    if isinstance(col_type, (sqltypes.Integer, sqltypes.Float, sqltypes.Numeric, sqltypes.DECIMAL)):
        return "number"
    if isinstance(col_type, (sqltypes.Date, sqltypes.DateTime)):
        return "date"
    if isinstance(col_type, sqltypes.Boolean):
        return "boolean"
    if isinstance(col_type, sqltypes.Enum):
        return "enum"
    return "string"


def get_table_metadata() -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    for key, (model, label) in REPORT_TABLES.items():
        fields = []
        for column in model.__table__.columns:
            fields.append({
                "name": column.name,
                "label": column.name.replace("_", " ").title(),
                "type": column_type_info(column),
            })
        meta[key] = {"label": label, "fields": fields}
    return meta


def apply_filters(query, model, filters: List[Dict[str, Any]]):
    for fil in filters:
        field = fil.get("field")
        op = fil.get("operator")
        value = fil.get("value")
        value_to = fil.get("value_to")
        if not field or not op:
            continue
        column = getattr(model, field, None)
        if column is None:
            continue
        col_type = column_type_info(column)

        if op in {"is_null", "not_null"}:
            query = query.filter(column.is_(None)) if op == "is_null" else query.filter(column.isnot(None))
            continue

        parsed = value
        parsed_to = value_to
        if col_type == "number":
            parsed = float(value) if value and str(value).replace(".", "", 1).isdigit() else None
            parsed_to = float(value_to) if value_to and str(value_to).replace(".", "", 1).isdigit() else None
        elif col_type == "date":
            parsed = parse_date(value) if value else None
            parsed_to = parse_date(value_to) if value_to else None
        elif col_type == "boolean":
            parsed = to_bool(value)

        if op == "eq":
            query = query.filter(column == parsed)
        elif op == "neq":
            query = query.filter(column != parsed)
        elif op == "contains" and value:
            query = query.filter(column.ilike(f"%{value}%"))
        elif op == "startswith" and value:
            query = query.filter(column.ilike(f"{value}%"))
        elif op == "endswith" and value:
            query = query.filter(column.ilike(f"%{value}"))
        elif op == "gt" and parsed is not None:
            query = query.filter(column > parsed)
        elif op == "gte" and parsed is not None:
            query = query.filter(column >= parsed)
        elif op == "lt" and parsed is not None:
            query = query.filter(column < parsed)
        elif op == "lte" and parsed is not None:
            query = query.filter(column <= parsed)
        elif op == "between" and parsed is not None and parsed_to is not None:
            query = query.filter(column.between(parsed, parsed_to))
        elif op == "in" and value:
            values = [v.strip() for v in str(value).split(",") if v.strip()]
            query = query.filter(column.in_(values))
    return query


def apply_time_range(query, model, time_field: str | None, time_range: str | None,
                     time_from: str | None, time_to: str | None):
    if not time_field:
        return query
    column = getattr(model, time_field, None)
    if column is None:
        return query

    start_date, end_date = resolve_time_window(time_range, time_from, time_to)

    if start_date and end_date:
        query = query.filter(and_(column >= start_date, column <= end_date))
    elif start_date:
        query = query.filter(column >= start_date)
    elif end_date:
        query = query.filter(column <= end_date)

    return query


def serialize_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if value is None:
        return ""
    return str(value)


def run_report(report: ReportDefinition, db: Session):
    model, _ = REPORT_TABLES.get(report.base_table, (None, ""))
    if not model:
        return [], [], [], {}, []

    filters = json.loads(report.filters or "[]")
    selected_columns = json.loads(report.selected_columns or "[]")

    query = db.query(model)
    query = apply_filters(query, model, filters)
    query = apply_time_range(
        query,
        model,
        report.time_field,
        report.time_range,
        report.time_from.isoformat() if report.time_from else None,
        report.time_to.isoformat() if report.time_to else None,
    )

    if selected_columns:
        columns = [getattr(model, col) for col in selected_columns if hasattr(model, col)]
        query = query.with_entities(*columns)
        rows = query.all()
        data_rows = [
            {selected_columns[idx]: serialize_value(value) for idx, value in enumerate(row)}
            for row in rows
        ]
        headers = selected_columns
    else:
        rows = query.all()
        headers = [col.name for col in model.__table__.columns]
        data_rows = [
            {name: serialize_value(getattr(row, name)) for name in headers}
            for row in rows
        ]

    chart = build_chart(report, model, db, filters)
    insights = build_insights(report, model, db, filters)
    return headers, data_rows, chart["labels"], chart, insights


def mask_candidate_phone_rows(
    headers: List[str],
    rows: List[Dict[str, Any]],
    user_role: str | None,
    base_table: str,
    can_view: bool,
):
    if base_table != "candidates":
        return rows
    if can_view:
        return rows
    if user_role not in RESTRICTED_PHONE_ROLES:
        return rows
    if "phone" not in headers:
        return rows
    for row in rows:
        if "phone" in row:
            row["phone"] = mask_phone(row.get("phone"), user_role)
    return rows


def build_chart(report: ReportDefinition, model: Any, db: Session, filters: List[Dict[str, Any]]):
    chart_type = report.chart_type or ""
    chart = {"labels": [], "values": [], "type": chart_type}

    if not chart_type or report.view_mode == "table":
        return chart

    if chart_type in {"bar", "pie"} and report.chart_x:
        column = getattr(model, report.chart_x, None)
        if column is None:
            return chart
        query = db.query(column, func.count())
        query = apply_filters(query, model, filters)
        query = apply_time_range(
            query,
            model,
            report.time_field,
            report.time_range,
            report.time_from.isoformat() if report.time_from else None,
            report.time_to.isoformat() if report.time_to else None,
        )
        results = query.group_by(column).all()
        chart["labels"] = [serialize_value(row[0]) for row in results]
        chart["values"] = [row[1] for row in results]
        return chart

    if chart_type == "line" and report.time_field:
        column = getattr(model, report.time_field, None)
        if column is None:
            return chart

        end_date = date.today()
        start_date = end_date - timedelta(days=30)
        if report.time_range in TIME_PRESETS:
            start_date = end_date - timedelta(days=TIME_PRESETS[report.time_range])
        elif report.time_range == "ytd":
            start_date = date(end_date.year, 1, 1)
        elif report.time_range == "mtd":
            start_date = date(end_date.year, end_date.month, 1)
        elif report.time_range == "custom":
            start_date = parse_date(report.time_from.isoformat()) if report.time_from else None
            end_date = parse_date(report.time_to.isoformat()) if report.time_to else end_date

        query = db.query(column, func.count())
        query = apply_filters(query, model, filters)
        if start_date:
            query = query.filter(column >= start_date)
        if end_date:
            query = query.filter(column <= end_date)
        results = query.group_by(column).order_by(column).all()
        chart["labels"] = [serialize_value(row[0]) for row in results]
        chart["values"] = [row[1] for row in results]
        return chart

    return chart


def build_insights(report: ReportDefinition, model: Any, db: Session, filters: List[Dict[str, Any]]):
    insights = []
    query = db.query(model)
    query = apply_filters(query, model, filters)
    query = apply_time_range(
        query,
        model,
        report.time_field,
        report.time_range,
        report.time_from.isoformat() if report.time_from else None,
        report.time_to.isoformat() if report.time_to else None,
    )
    total = query.count()
    insights.append({"label": "Total records", "value": total})

    if report.time_field:
        column = getattr(model, report.time_field, None)
        start_date, end_date = resolve_time_window(
            report.time_range,
            report.time_from.isoformat() if report.time_from else None,
            report.time_to.isoformat() if report.time_to else None,
        )
        if column is not None and (start_date or end_date):
            current_query = query
            if start_date:
                current_query = current_query.filter(column >= start_date)
            if end_date:
                current_query = current_query.filter(column <= end_date)
            current_count = current_query.count()

            prev_count = None
            if start_date and end_date:
                delta_days = (end_date - start_date).days + 1
                prev_end = start_date - timedelta(days=1)
                prev_start = prev_end - timedelta(days=delta_days - 1)
                prev_query = query.filter(column >= prev_start, column <= prev_end)
                prev_count = prev_query.count()

            insights.append({
                "label": "Current period",
                "value": current_count,
            })
            if prev_count is not None:
                diff = current_count - prev_count
                pct = (diff / prev_count * 100) if prev_count else 100
                insights.append({
                    "label": "Change vs previous",
                    "value": f"{diff:+} ({pct:.1f}%)",
                })

    if report.time_field:
        column = getattr(model, report.time_field, None)
        if column is not None:
            first = query.order_by(column.asc()).first()
            last = query.order_by(column.desc()).first()
            insights.append({"label": "First date", "value": serialize_value(getattr(first, report.time_field)) if first else ""})
            insights.append({"label": "Last date", "value": serialize_value(getattr(last, report.time_field)) if last else ""})

    if report.chart_x:
        column = getattr(model, report.chart_x, None)
        if column is not None:
            top_query = db.query(column, func.count())
            top_query = apply_filters(top_query, model, filters)
            top_query = apply_time_range(
                top_query,
                model,
                report.time_field,
                report.time_range,
                report.time_from.isoformat() if report.time_from else None,
                report.time_to.isoformat() if report.time_to else None,
            )
            top = top_query.group_by(column).order_by(func.count().desc()).limit(3).all()
            if top:
                summary = ", ".join([f"{serialize_value(row[0])} ({row[1]})" for row in top])
                insights.append({"label": "Top segments", "value": summary})

    return insights


def ensure_report_access(user: User, report: ReportDefinition, db: Session) -> bool:
    if is_admin(user):
        return True
    if report.created_by_id == user.id:
        return True
    role_share = db.query(ReportShare).filter(
        ReportShare.report_id == report.id,
        ReportShare.role == user.role.value,
    ).first()
    user_share = db.query(ReportShare).filter(
        ReportShare.report_id == report.id,
        ReportShare.user_id == user.id,
    ).first()
    return bool(role_share or user_share)


def build_shares(report_id: int, roles: Iterable[str], users: Iterable[str], db: Session):
    for role in roles:
        if role:
            db.add(ReportShare(report_id=report_id, role=role))
    for user_id in users:
        if user_id and str(user_id).isdigit():
            db.add(ReportShare(report_id=report_id, user_id=int(user_id)))


def build_report_payload(report: ReportDefinition | None) -> Dict[str, Any] | None:
    if not report:
        return None
    return {
        "selected_columns": json.loads(report.selected_columns or "[]"),
        "filters": json.loads(report.filters or "[]"),
        "chart_x": report.chart_x,
        "time_field": report.time_field,
    }


@router.get("")
async def reports_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    my_reports = (
        db.query(ReportDefinition)
        .filter(ReportDefinition.created_by_id == user.id, ReportDefinition.is_template == False)
        .all()
    )

    shared_reports = []
    if not is_admin(user):
        shared_reports = (
            db.query(ReportDefinition)
            .join(ReportShare, ReportShare.report_id == ReportDefinition.id)
            .filter(
                ReportDefinition.is_active == True,
                ReportDefinition.is_template == False,
                (ReportShare.role == user.role.value) | (ReportShare.user_id == user.id),
            )
            .distinct()
            .all()
        )

    report_templates = db.query(ReportDefinition).filter(ReportDefinition.is_template == True).all()

    ctx = build_template_context(
        request, db,
        my_reports=my_reports,
        shared_reports=shared_reports,
        templates=report_templates,
        page_title="Reports",
        is_admin=is_admin(user),
        can_create_reports=user.role.value in CREATOR_ROLES,
    )
    try:
        return templates.TemplateResponse("reports/list.html", ctx)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise


@router.get("/new")
async def report_new_form(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *CREATOR_ROLES)
    if redir:
        return redir

    users = []
    if is_admin(user):
        users = db.query(User).filter(User.is_active == True).order_by(User.full_name).all()
    share_roles = []
    share_users = []
    ctx = build_template_context(
        request, db,
        page_title="Create Report",
        report=None,
        roles=UserRole,
        users=users,
        table_meta=get_table_metadata(),
        operator_options=OPERATOR_OPTIONS,
        report_payload=None,
        share_roles=share_roles,
        share_users=share_users,
        can_share=is_admin(user),
    )
    return templates.TemplateResponse("reports/builder.html", ctx)


@router.post("/new")
async def report_new_post(request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *CREATOR_ROLES)
    if redir:
        return redir

    form = await request.form()
    name = form.get("name", "").strip()
    base_table = form.get("base_table", "").strip()
    if not name or base_table not in REPORT_TABLES:
        add_flash(request, "Report name and base table are required.", "danger")
        return RedirectResponse(url="/reports/new", status_code=302)

    selected_columns = form.getlist("columns")
    filters = build_filters_from_form(form)

    report = ReportDefinition(
        name=name,
        description=form.get("description", "").strip() or None,
        base_table=base_table,
        selected_columns=json.dumps(selected_columns),
        filters=json.dumps(filters),
        chart_type=form.get("chart_type", "").strip() or None,
        chart_x=form.get("chart_x", "").strip() or None,
        chart_y=form.get("chart_y", "").strip() or None,
        chart_agg=form.get("chart_agg", "").strip() or None,
        time_field=form.get("time_field", "").strip() or None,
        time_range=form.get("time_range", "").strip() or None,
        time_from=parse_datetime_input(form.get("time_from", "")),
        time_to=parse_datetime_input(form.get("time_to", "")),
        view_mode=form.get("view_mode", "both"),
        is_template=(form.get("is_template") == "1") if is_admin(user) else False,
        created_by_id=user.id,
    )
    db.add(report)
    db.commit()

    if is_admin(user):
        roles = form.getlist("share_roles")
        users = form.getlist("share_users")
        build_shares(report.id, roles, users, db)
        db.commit()

    add_flash(request, "Report created successfully.", "success")
    return RedirectResponse(url=f"/reports/{report.id}", status_code=302)


@router.get("/{rid}")
async def report_view(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    report = db.query(ReportDefinition).filter(ReportDefinition.id == rid).first()
    if not report:
        add_flash(request, "Report not found.", "danger")
        return RedirectResponse(url="/reports", status_code=302)

    if not ensure_report_access(user, report, db):
        return RedirectResponse(url="/unauthorized", status_code=302)

    headers, rows, _, chart, insights = run_report(report, db)
    rows = mask_candidate_phone_rows(
        headers,
        rows,
        user.role.value,
        report.base_table,
        can_view_phone(user.id, db),
    )

    ctx = build_template_context(
        request, db,
        report=report,
        headers=headers,
        rows=rows,
        chart=chart,
        insights=insights,
        page_title=report.name,
    )
    return templates.TemplateResponse("reports/view.html", ctx)


@router.get("/{rid}/edit")
async def report_edit_form(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *CREATOR_ROLES)
    if redir:
        return redir

    report = db.query(ReportDefinition).filter(ReportDefinition.id == rid).first()
    if not report:
        add_flash(request, "Report not found.", "danger")
        return RedirectResponse(url="/reports", status_code=302)

    if not is_admin(user) and report.created_by_id != user.id:
        return RedirectResponse(url="/unauthorized", status_code=302)

    users = []
    share_roles = []
    share_users = []
    if is_admin(user):
        users = db.query(User).filter(User.is_active == True).order_by(User.full_name).all()
        share_roles = [s.role for s in report.shares if s.role]
        share_users = [s.user_id for s in report.shares if s.user_id]
    ctx = build_template_context(
        request, db,
        page_title=f"Edit Report — {report.name}",
        report=report,
        roles=UserRole,
        users=users,
        table_meta=get_table_metadata(),
        operator_options=OPERATOR_OPTIONS,
        report_payload=build_report_payload(report),
        share_roles=share_roles,
        share_users=share_users,
        can_share=is_admin(user),
    )
    return templates.TemplateResponse("reports/builder.html", ctx)


@router.post("/{rid}/edit")
async def report_edit_post(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *CREATOR_ROLES)
    if redir:
        return redir

    report = db.query(ReportDefinition).filter(ReportDefinition.id == rid).first()
    if not report:
        add_flash(request, "Report not found.", "danger")
        return RedirectResponse(url="/reports", status_code=302)

    if not is_admin(user) and report.created_by_id != user.id:
        return RedirectResponse(url="/unauthorized", status_code=302)

    form = await request.form()
    name = form.get("name", "").strip()
    base_table = form.get("base_table", "").strip()
    if not name or base_table not in REPORT_TABLES:
        add_flash(request, "Report name and base table are required.", "danger")
        return RedirectResponse(url=f"/reports/{rid}/edit", status_code=302)

    report.name = name
    report.description = form.get("description", "").strip() or None
    report.base_table = base_table
    report.selected_columns = json.dumps(form.getlist("columns"))
    report.filters = json.dumps(build_filters_from_form(form))
    report.chart_type = form.get("chart_type", "").strip() or None
    report.chart_x = form.get("chart_x", "").strip() or None
    report.chart_y = form.get("chart_y", "").strip() or None
    report.chart_agg = form.get("chart_agg", "").strip() or None
    report.time_field = form.get("time_field", "").strip() or None
    report.time_range = form.get("time_range", "").strip() or None
    report.time_from = parse_datetime_input(form.get("time_from", ""))
    report.time_to = parse_datetime_input(form.get("time_to", ""))
    report.view_mode = form.get("view_mode", "both")
    report.is_template = (form.get("is_template") == "1") if is_admin(user) else report.is_template

    if is_admin(user):
        db.query(ReportShare).filter(ReportShare.report_id == report.id).delete()
        roles = form.getlist("share_roles")
        users = form.getlist("share_users")
        build_shares(report.id, roles, users, db)

    db.commit()
    add_flash(request, "Report updated successfully.", "success")
    return RedirectResponse(url=f"/reports/{rid}", status_code=302)


@router.post("/{rid}/clone")
async def report_clone(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    report = db.query(ReportDefinition).filter(ReportDefinition.id == rid).first()
    if not report:
        add_flash(request, "Report not found.", "danger")
        return RedirectResponse(url="/reports", status_code=302)

    if not ensure_report_access(user, report, db):
        return RedirectResponse(url="/unauthorized", status_code=302)

    clone = ReportDefinition(
        name=f"{report.name} Copy",
        description=report.description,
        base_table=report.base_table,
        selected_columns=report.selected_columns,
        filters=report.filters,
        chart_type=report.chart_type,
        chart_x=report.chart_x,
        chart_y=report.chart_y,
        chart_agg=report.chart_agg,
        time_field=report.time_field,
        time_range=report.time_range,
        time_from=report.time_from,
        time_to=report.time_to,
        view_mode=report.view_mode,
        is_template=False,
        created_by_id=user.id,
    )
    db.add(clone)
    db.commit()

    add_flash(request, "Report cloned successfully.", "success")
    if is_admin(user):
        return RedirectResponse(url=f"/reports/{clone.id}/edit", status_code=302)
    return RedirectResponse(url=f"/reports/{clone.id}", status_code=302)


@router.post("/{rid}/delete")
async def report_delete(rid: int, request: Request, db: Session = Depends(get_db)):
    user, redir = require_auth(request, db, *CREATOR_ROLES)
    if redir:
        return redir

    report = db.query(ReportDefinition).filter(ReportDefinition.id == rid).first()
    if report:
        if not is_admin(user) and report.created_by_id != user.id:
            return RedirectResponse(url="/unauthorized", status_code=302)
        db.delete(report)
        db.commit()

    add_flash(request, "Report deleted.", "success")
    return RedirectResponse(url="/reports", status_code=302)


@router.get("/{rid}/export")
async def report_export(rid: int, request: Request, db: Session = Depends(get_db), format: str = "csv"):
    user, redir = require_auth(request, db, *ALLOWED_ROLES)
    if redir:
        return redir

    report = db.query(ReportDefinition).filter(ReportDefinition.id == rid).first()
    if not report:
        add_flash(request, "Report not found.", "danger")
        return RedirectResponse(url="/reports", status_code=302)

    if not ensure_report_access(user, report, db):
        return RedirectResponse(url="/unauthorized", status_code=302)

    headers, rows, _, _, _ = run_report(report, db)
    rows = mask_candidate_phone_rows(headers, rows, user.role.value, report.base_table, can_view_phone(user.id, db))
    if format == "csv":
        return export_csv(report, headers, rows)
    if format == "xlsx":
        return export_xlsx(report, headers, rows)
    if format == "pdf":
        return export_pdf(report, headers, rows)

    add_flash(request, "Unsupported export format.", "danger")
    return RedirectResponse(url=f"/reports/{rid}", status_code=302)


def export_csv(report: ReportDefinition, headers: List[str], rows: List[Dict[str, Any]]):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(h, "") for h in headers])
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={report.name}.csv"},
    )


def export_xlsx(report: ReportDefinition, headers: List[str], rows: List[Dict[str, Any]]):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={report.name}.xlsx"},
    )


def export_pdf(report: ReportDefinition, headers: List[str], rows: List[Dict[str, Any]]):
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=landscape(letter))
    width, height = landscape(letter)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, height - 40, report.name)

    c.setFont("Helvetica", 8)
    y = height - 70
    x_start = 40
    col_width = max(80, int((width - 80) / max(1, len(headers))))

    for idx, header in enumerate(headers):
        c.drawString(x_start + idx * col_width, y, str(header))
    y -= 14

    for row in rows[:200]:
        for idx, header in enumerate(headers):
            c.drawString(x_start + idx * col_width, y, str(row.get(header, ""))[:30])
        y -= 12
        if y < 40:
            c.showPage()
            y = height - 40

    c.save()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={report.name}.pdf"},
    )


def parse_datetime_input(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.combine(parse_date(value), datetime.min.time())
        except Exception:
            return None


def build_filters_from_form(form) -> List[Dict[str, Any]]:
    fields = form.getlist("filter_field")
    operators = form.getlist("filter_operator")
    values = form.getlist("filter_value")
    values_to = form.getlist("filter_value_to")

    filters = []
    for idx, field in enumerate(fields):
        field = (field or "").strip()
        if not field:
            continue
        filters.append({
            "field": field,
            "operator": operators[idx] if idx < len(operators) else "eq",
            "value": values[idx] if idx < len(values) else "",
            "value_to": values_to[idx] if idx < len(values_to) else "",
        })
    return filters
