from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, Text
from database import Base


class AppConfig(Base):
    __tablename__ = "app_configs"

    id = Column(Integer, primary_key=True, index=True)
    session_timeout_minutes = Column(Integer, default=60, nullable=False)
    phone_view_user_ids = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
