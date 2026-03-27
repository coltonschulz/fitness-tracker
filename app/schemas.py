from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, date
from typing import Optional, List


class ExerciseCreate(BaseModel):
    exercise_name: str
    set_number: int
    reps: int
    weight_lbs: Optional[float] = None
    weight_kg: Optional[float] = None
    rpe: Optional[int] = None
    tempo: Optional[str] = None
    rest_seconds: Optional[int] = None
    notes: Optional[str] = None


class ExerciseDefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    muscle_group: Optional[str] = Field(None, max_length=100)


class WorkoutCreate(BaseModel):
    workout_date: date
    program_name: Optional[str] = None
    notes: Optional[str] = None
    difficulty_rating: Optional[int] = None
    duration_minutes: Optional[int] = None
    exercises: List[ExerciseCreate]


class WorkoutUpdate(BaseModel):
    program_name: Optional[str] = None
    notes: Optional[str] = None
    difficulty_rating: Optional[int] = None
    duration_minutes: Optional[int] = None


class WorkoutListResponse(BaseModel):
    id: int
    workout_date: date
    program_name: Optional[str]
    difficulty_rating: Optional[int]
    duration_minutes: Optional[int]
    exercise_count: int
    has_ai_review: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkoutDetailResponse(BaseModel):
    id: int
    user_id: int
    workout_date: date
    program_name: Optional[str]
    notes: Optional[str]
    difficulty_rating: Optional[int]
    duration_minutes: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkoutsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    workouts: List[WorkoutListResponse]


class AICoachingRequest(BaseModel):
    workout_id: int
    focus_areas: Optional[List[str]] = None
    context: Optional[str] = None


class AIReviewResponse(BaseModel):
    id: int
    workout_id: int
    user_id: int
    status: str
    requested_at: datetime
    completed_at: Optional[datetime] = None
    response: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AICoachingAcceptedResponse(BaseModel):
    review_id: int
    status: str
    requested_at: datetime
    message: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CurrentUserResponse(UserResponse):
    pass
