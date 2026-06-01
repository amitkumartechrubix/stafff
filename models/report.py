from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class ReportDefinition(Base):
    __tablename__ = "report_definitions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    description = Column(String(300), nullable=True)
    base_table = Column(String(80), nullable=False)
    selected_columns = Column(Text, nullable=True)
    filters = Column(Text, nullable=True)
    chart_type = Column(String(30), nullable=True)
    chart_x = Column(String(80), nullable=True)
    chart_y = Column(String(80), nullable=True)
    chart_agg = Column(String(20), nullable=True)
    time_field = Column(String(80), nullable=True)
    time_range = Column(String(30), nullable=True)
    time_from = Column(DateTime, nullable=True)
    time_to = Column(DateTime, nullable=True)
    view_mode = Column(String(20), default="both", nullable=False)
    is_template = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = relationship("User")
    shares = relationship("ReportShare", back_populates="report", cascade="all, delete-orphan")


class ReportShare(Base):
    __tablename__ = "report_shares"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("report_definitions.id"), nullable=False)
    role = Column(String(40), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    report = relationship("ReportDefinition", back_populates="shares")
    user = relationship("User")
