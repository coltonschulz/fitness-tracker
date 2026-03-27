from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime,
    Numeric, ForeignKey, UniqueConstraint, CheckConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255))
    cloudflare_id = Column(String(255), unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Workout(Base):
    __tablename__ = "workouts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workout_date = Column(Date, nullable=False)
    program_name = Column(String(255))
    notes = Column(Text)
    difficulty_rating = Column(Integer)
    duration_minutes = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "workout_date", name="user_date_unique"),
        CheckConstraint(
            "difficulty_rating IS NULL OR (difficulty_rating >= 1 AND difficulty_rating <= 10)",
            name="workouts_difficulty_rating_check",
        ),
    )


class Exercise(Base):
    __tablename__ = "exercises"

    id = Column(Integer, primary_key=True)
    workout_id = Column(Integer, ForeignKey("workouts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_name = Column(String(255), nullable=False, index=True)
    set_number = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    weight_lbs = Column(Numeric(8, 2))
    weight_kg = Column(Numeric(8, 2))
    rpe = Column(Integer)
    tempo = Column(String(50))
    rest_seconds = Column(Integer)
    notes = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint(
            "rpe IS NULL OR (rpe >= 1 AND rpe <= 10)",
            name="exercises_rpe_check",
        ),
    )


class ExerciseDefinition(Base):
    """Persistent exercise catalogue with optional muscle group classification."""

    __tablename__ = "exercise_definitions"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    muscle_group = Column(String(100))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AIReview(Base):
    __tablename__ = "ai_reviews"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workout_id = Column(Integer, ForeignKey("workouts.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(50), default="pending", index=True)
    prompt = Column(Text, nullable=False)
    response = Column(Text)
    requested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    tokens_used = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
