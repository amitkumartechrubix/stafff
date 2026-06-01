from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from database import Base


class EmailConfig(Base):
    __tablename__ = "email_configs"

    id = Column(Integer, primary_key=True, index=True)
    config_name = Column(String(100), nullable=False, default="Primary")

    # ── IMAP Settings ─────────────────────────────────────────────────────────
    imap_host = Column(String(200), nullable=True)
    imap_port = Column(Integer, default=993)
    imap_username = Column(String(100), nullable=True)
    imap_password = Column(String(255), nullable=True)   # store encrypted in prod
    imap_use_ssl = Column(Boolean, default=True)
    imap_folder = Column(String(100), default="INBOX")

    # ── SMTP Settings ─────────────────────────────────────────────────────────
    smtp_host = Column(String(200), nullable=True)
    smtp_port = Column(Integer, default=587)
    smtp_username = Column(String(100), nullable=True)
    smtp_password = Column(String(255), nullable=True)   # store encrypted in prod
    smtp_use_tls = Column(Boolean, default=True)
    smtp_from_name = Column(String(100), nullable=True)
    smtp_from_email = Column(String(100), nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    is_active = Column(Boolean, default=True)
    last_imap_test = Column(DateTime, nullable=True)
    last_smtp_test = Column(DateTime, nullable=True)
    imap_test_status = Column(String(20), nullable=True)   # ok / fail
    smtp_test_status = Column(String(20), nullable=True)   # ok / fail

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailRule(Base):
    __tablename__ = "email_rules"

    id = Column(Integer, primary_key=True, index=True)
    config_id = Column(Integer, nullable=False)
    rule_type = Column(String(20), nullable=False)       # inbound / outbound
    rule_name = Column(String(100), nullable=False)
    # JSON string: {"field": "from", "operator": "contains", "value": "@example.com"}
    condition = Column(Text, nullable=True)
    # JSON string: {"action": "forward", "to": "admin@example.com"}
    action = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=10)
    created_at = Column(DateTime, default=datetime.utcnow)
