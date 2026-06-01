from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class JobPosting(Base):
    __tablename__ = "job_postings"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(150), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    company_name = Column(String(200), nullable=True)
    profile_id = Column(Integer, ForeignKey("job_profiles.id"), nullable=True)
    role_title = Column(String(120), nullable=True)
    designation = Column(String(120), nullable=True)
    industry = Column(String(120), nullable=True)
    min_experience = Column(Integer, nullable=True)
    max_experience = Column(Integer, nullable=True)
    skills = Column(Text, nullable=True)
    jd_summary = Column(Text, nullable=True)
    technical_responsibilities = Column(Text, nullable=True)
    functional_responsibilities = Column(Text, nullable=True)

    location = Column(String(120), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    plant_address = Column(Text, nullable=True)
    openings = Column(Integer, nullable=True)
    employment_type = Column(String(60), nullable=True)
    salary_range = Column(String(60), nullable=True)

    status = Column(String(30), default="active", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    approval_status = Column(String(30), default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_recruiter_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    profile = relationship("JobProfile")
    company = relationship("Company")
    assigned_recruiter = relationship("User", foreign_keys=[assigned_recruiter_id])
