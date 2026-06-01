from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class FieldAgentLocationLog(Base):
    __tablename__ = "field_agent_location_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    accuracy_m = Column(Float, nullable=True)
    address = Column(String(255), nullable=True)
    setup_type = Column(String(60), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")
