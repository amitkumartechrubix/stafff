import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Enum
from database import Base


class SourceType(str, enum.Enum):
    COLLEGE = "college"
    SOCIAL_MEDIA = "social_media"
    SITE_IN_CHARGE = "site_in_charge"
    JOB_PORTAL = "job_portal"
    WEBSITE_INQUIRY = "website_inquiry"
    FIELD_REPRESENTATIVE = "field_representative"
    DIRECT_CALL = "direct_call"


SOURCE_TYPE_LABELS = {
    SourceType.COLLEGE: "College / Campus",
    SourceType.SOCIAL_MEDIA: "Social Media",
    SourceType.SITE_IN_CHARGE: "Site In-Charge",
    SourceType.JOB_PORTAL: "Job Portal",
    SourceType.WEBSITE_INQUIRY: "Website Inquiry",
    SourceType.FIELD_REPRESENTATIVE: "Field Representative",
    SourceType.DIRECT_CALL: "Direct Call / Walk-in",
}


class RecruitmentSource(Base):
    __tablename__ = "recruitment_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    source_type = Column(Enum(SourceType), nullable=False)
    description = Column(Text, nullable=True)
    contact_info = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def type_label(self) -> str:
        return SOURCE_TYPE_LABELS.get(self.source_type, self.source_type.value)
