from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)

    # Company Details
    name = Column(String(200), nullable=False, index=True)
    industry = Column(String(100), nullable=True)
    location = Column(String(200), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True)
    website = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)

    # Technical Contact
    technical_contact_name = Column(String(100), nullable=True)
    technical_contact_email = Column(String(100), nullable=True)
    technical_contact_phone = Column(String(20), nullable=True)

    # HR Contact
    hr_contact_name = Column(String(100), nullable=True)
    hr_contact_email = Column(String(100), nullable=True)
    hr_contact_phone = Column(String(20), nullable=True)

    # Legal / Registration
    gst_number = Column(String(20), nullable=True)
    cin_number = Column(String(21), nullable=True)

    # Status
    is_verified = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    locations = relationship("CompanyLocation", back_populates="company", cascade="all, delete-orphan")
    contacts = relationship("CompanyContact", back_populates="company", cascade="all, delete-orphan")


class CompanyLocation(Base):
    __tablename__ = "company_locations"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="locations")


class CompanyContact(Base):
    __tablename__ = "company_contacts"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    contact_type = Column(String(20), nullable=False)
    name = Column(String(100), nullable=True)
    email = Column(String(100), nullable=True)
    phone = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="contacts")
