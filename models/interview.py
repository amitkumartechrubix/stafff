from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class InterviewRound(Base):
    __tablename__ = "interview_rounds"

    id = Column(Integer, primary_key=True, index=True)
    round_number = Column(Integer, nullable=False, index=True)
    title = Column(String(100), nullable=True)
    allow_random = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    questions = relationship(
        "InterviewQuestion",
        back_populates="round",
        cascade="all, delete-orphan",
    )


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"

    id = Column(Integer, primary_key=True, index=True)
    round_id = Column(Integer, ForeignKey("interview_rounds.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    round = relationship("InterviewRound", back_populates="questions")


class CandidateInterviewResponse(Base):
    __tablename__ = "candidate_interview_responses"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
    round_id = Column(Integer, ForeignKey("interview_rounds.id"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("interview_questions.id"), nullable=False, index=True)
    response_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    round = relationship("InterviewRound")
    question = relationship("InterviewQuestion")


class CandidateCustomQuestionResponse(Base):
    __tablename__ = "candidate_custom_question_responses"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
    round_id = Column(Integer, ForeignKey("interview_rounds.id"), nullable=False, index=True)
    question_text = Column(Text, nullable=False)
    response_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    round = relationship("InterviewRound")
