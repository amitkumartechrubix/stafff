from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from database import Base


class JobProfile(Base):
    __tablename__ = "job_profiles"

    id = Column(Integer, primary_key=True, index=True)

    role_title = Column(String(120), nullable=False, index=True)
    designation = Column(String(120), nullable=True, index=True)
    industry = Column(String(120), nullable=True, index=True)
    min_experience = Column(Integer, nullable=True)
    max_experience = Column(Integer, nullable=True)
    skills = Column(Text, nullable=True)
    jd_summary = Column(Text, nullable=True)
    technical_responsibilities = Column(Text, nullable=True)
    functional_responsibilities = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
