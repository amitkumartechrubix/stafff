import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey, Text, Date
from sqlalchemy.orm import relationship
from database import Base


class CandidateStatus(str, enum.Enum):
    ACTIVE = "active"
    PLACED = "placed"
    INACTIVE = "inactive"
    BLACKLISTED = "blacklisted"


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)

    # Basic Info
    full_name = Column(String(100), nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    email = Column(String(100), nullable=True, index=True)

    # Location
    location = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True, index=True)
    state = Column(String(100), nullable=True, index=True)

    # Personal Details
    qualification = Column(String(100), nullable=True)
    age = Column(Integer, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(10), nullable=True)

    # Aadhaar Details
    aadhaar_number = Column(String(20), nullable=True, index=True)
    aadhaar_doc_path = Column(String(255), nullable=True)
    aadhaar_ocr_status = Column(String(20), nullable=True)
    aadhaar_ocr_notes = Column(Text, nullable=True)
    aadhaar_ocr_text = Column(Text, nullable=True)
    aadhaar_ocr_name = Column(String(120), nullable=True)
    aadhaar_ocr_dob = Column(String(20), nullable=True)
    aadhaar_ocr_number = Column(String(20), nullable=True)
    education_doc_path = Column(String(255), nullable=True)
    bank_doc_path = Column(String(255), nullable=True)
    resume_doc_path = Column(String(255), nullable=True)
    education_docs = Column(Text, nullable=True)
    bank_docs = Column(Text, nullable=True)
    resume_docs = Column(Text, nullable=True)

    # Professional Details
    skills = Column(Text, nullable=True)
    experience_years = Column(Integer, default=0)
    preferred_job_type = Column(String(100), nullable=True)
    expected_salary = Column(String(50), nullable=True)
    passing_out_year = Column(Integer, nullable=True)

    # Status & Source
    status = Column(Enum(CandidateStatus), default=CandidateStatus.ACTIVE, nullable=False)
    source_id = Column(Integer, ForeignKey("recruitment_sources.id"), nullable=True)

    # Institution context
    institution_name = Column(String(200), nullable=True, index=True)

    # Registration metadata
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    registered_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_recruiter_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    locked_job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    round1_status = Column(String(30), nullable=True)
    round1_not_shortlisted_reason = Column(Text, nullable=True)
    unlock_reason = Column(Text, nullable=True)
    employment_stage = Column(String(40), nullable=True)
    stage_reason = Column(Text, nullable=True)
    stage_updated_at = Column(DateTime, nullable=True)
    registered_with_layam = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Outcomes
    employed = Column(Boolean, default=False, nullable=False)

    # Additional notes
    notes = Column(Text, nullable=True)

    # Relationships
    registered_by = relationship(
        "User",
        back_populates="candidates_registered",
        foreign_keys=[registered_by_id],
    )
    assigned_recruiter = relationship("User", foreign_keys=[assigned_recruiter_id])
    locked_job = relationship("JobPosting", foreign_keys=[locked_job_id])
    source = relationship("RecruitmentSource")
