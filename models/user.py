import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    EMPLOYER = "employer"
    INSTITUTION = "institution"
    MANAGER = "manager"
    RECRUITER = "recruiter"
    FIELD_AGENT = "field_agent"


class LicenseType(str, enum.Enum):
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    EMPLOYER = "employer"
    INSTITUTION = "institution"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    seed_password = Column(String(255), nullable=True)
    role = Column(Enum(UserRole), nullable=False)
    license_type = Column(Enum(LicenseType), default=LicenseType.BASIC, nullable=False)

    # Personal Information
    full_name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=True)

    # Employee-specific fields (manager / recruiter / field_agent) — 10 standard fields
    employee_id = Column(String(50), unique=True, nullable=True, index=True)
    date_of_birth = Column(DateTime, nullable=True)
    gender = Column(String(10), nullable=True)
    address = Column(Text, nullable=True)
    qualification = Column(String(100), nullable=True)
    experience_years = Column(Integer, nullable=True)
    department = Column(String(100), nullable=True)
    emergency_contact = Column(String(100), nullable=True)
    joining_date = Column(DateTime, nullable=True)
    reporting_manager = Column(String(100), nullable=True)

    # Status fields
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    created_by = relationship("User", remote_side=[id], foreign_keys=[created_by_id])
    candidates_registered = relationship(
        "Candidate",
        back_populates="registered_by",
        foreign_keys="Candidate.registered_by_id",
    )

    @property
    def role_display(self) -> str:
        return self.role.value.replace("_", " ").title()

    @property
    def initials(self) -> str:
        parts = (self.full_name or "U").split()
        return "".join(p[0].upper() for p in parts[:2])
