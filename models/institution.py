from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from database import Base


class Institution(Base):
    __tablename__ = "institutions"

    id = Column(Integer, primary_key=True, index=True)

    # Institution Details
    name = Column(String(200), nullable=False, index=True)
    institution_type = Column(String(100), nullable=True)   # college / ITI / polytechnic / vocational
    location = Column(String(200), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)

    # Academic Information
    courses_offered = Column(Text, nullable=True)           # comma-separated list
    years_of_operation = Column(Integer, nullable=True)
    affiliation = Column(String(200), nullable=True)
    student_strength = Column(Integer, nullable=True)

    # Point of Contact
    poc_name = Column(String(100), nullable=True)
    poc_designation = Column(String(100), nullable=True)
    poc_email = Column(String(100), nullable=True)
    poc_phone = Column(String(20), nullable=True)

    # Status
    is_verified = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
