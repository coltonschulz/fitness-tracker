import logging

from fastapi import APIRouter, Request, Depends, status
from sqlalchemy.orm import Session

from database.models import Workout, Exercise
from schemas import WorkoutCreate
from deps import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/workouts", status_code=status.HTTP_201_CREATED)
async def create_workout(
    workout_data: WorkoutCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a workout with exercises for the authenticated user."""
    user = get_current_user(request, db)

    workout = Workout(
        user_id=user.id,
        workout_date=workout_data.workout_date,
        program_name=workout_data.program_name,
        notes=workout_data.notes or "",
        difficulty_rating=workout_data.difficulty_rating,
        duration_minutes=workout_data.duration_minutes,
    )
    db.add(workout)
    db.flush()  # Populate workout.id without committing

    for ex in workout_data.exercises:
        db.add(Exercise(
            workout_id=workout.id,
            user_id=user.id,
            exercise_name=ex.exercise_name,
            set_number=ex.set_number,
            reps=ex.reps,
            weight_lbs=ex.weight_lbs,
            weight_kg=ex.weight_kg,
            rpe=ex.rpe,
            tempo=ex.tempo,
            rest_seconds=ex.rest_seconds,
            notes=ex.notes or "",
        ))

    db.commit()
    db.refresh(workout)

    return {
        "id": workout.id,
        "user_id": workout.user_id,
        "workout_date": str(workout.workout_date),
        "program_name": workout.program_name,
        "created_at": workout.created_at.isoformat() if workout.created_at else None,
    }
