"""
Fitness Tracker API
Multi-user workout logging with AI coaching.
Auth via Cloudflare Access (Google OAuth).
"""

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, Depends, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from database.models import User, Workout, Exercise, AIReview, ExerciseDefinition
from schemas import (
    WorkoutCreate, WorkoutUpdate, ExerciseCreate, ExerciseDefinitionCreate,
)
from deps import engine, get_db, get_current_user
from api.workouts import router as workouts_router
from api.auth import router as auth_router
from api.ai_coaching import router as ai_coaching_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify DB connection on startup. Schema managed via SQL migrations."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection verified")
        # Create new tables that may not exist in older deployments.
        ExerciseDefinition.__table__.create(bind=engine, checkfirst=True)
        AIReview.__table__.create(bind=engine, checkfirst=True)
        # Allow AI coaching requests without a linked workout.
        # Allow multiple workouts per user per day.
        with engine.connect() as migration_conn:
            migration_conn.execute(text(
                "ALTER TABLE ai_reviews "
                "ALTER COLUMN workout_id DROP NOT NULL"
            ))
            migration_conn.execute(text(
                "ALTER TABLE workouts "
                "DROP CONSTRAINT IF EXISTS user_date_unique"
            ))
            migration_conn.commit()
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise
    yield


app = FastAPI(title="Fitness Tracker API", version="2.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def extract_cloudflare_user(request: Request, call_next):
    """Extract authenticated user email from Cloudflare Access headers."""
    user_email = (
        request.headers.get("Cf-Access-Authenticated-User-Email")
        or request.headers.get("X-User-Email")
        or ""
    )
    user_name = request.headers.get("Cf-Access-Authenticated-User-Name", "")
    request.state.user_email = user_email.lower().strip()
    request.state.user_name = user_name
    return await call_next(request)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(workouts_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(ai_coaching_router, prefix="/api")


# ── Workout list & detail ─────────────────────────────────────────────────────

@app.get("/api/workouts", tags=["Workouts"])
async def list_workouts(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Paginated list of workouts for the current user, newest first."""
    user = get_current_user(request, db)
    total = (
        db.query(func.count(Workout.id))
        .filter(Workout.user_id == user.id)
        .scalar()
    )
    workouts = (
        db.query(Workout)
        .filter(Workout.user_id == user.id)
        .order_by(Workout.workout_date.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    result = []
    for w in workouts:
        exercise_count = (
            db.query(func.count(Exercise.id))
            .filter(Exercise.workout_id == w.id)
            .scalar()
        )
        has_ai_review = (
            db.query(AIReview)
            .filter(AIReview.workout_id == w.id, AIReview.status == "completed")
            .first()
            is not None
        )
        result.append({
            "id": w.id,
            "workout_date": str(w.workout_date),
            "program_name": w.program_name,
            "difficulty_rating": w.difficulty_rating,
            "duration_minutes": w.duration_minutes,
            "exercise_count": exercise_count,
            "has_ai_review": has_ai_review,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        })
    return {"total": total, "limit": limit, "offset": offset, "workouts": result}


@app.get("/api/workouts/{workout_id}", tags=["Workouts"])
async def get_workout(
    workout_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get a single workout with all its exercises."""
    user = get_current_user(request, db)
    workout = (
        db.query(Workout)
        .filter(Workout.id == workout_id, Workout.user_id == user.id)
        .first()
    )
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    exercises = (
        db.query(Exercise)
        .filter(Exercise.workout_id == workout_id)
        .order_by(Exercise.exercise_name, Exercise.set_number)
        .all()
    )
    return {
        "id": workout.id,
        "user_id": workout.user_id,
        "workout_date": str(workout.workout_date),
        "program_name": workout.program_name,
        "notes": workout.notes,
        "difficulty_rating": workout.difficulty_rating,
        "duration_minutes": workout.duration_minutes,
        "created_at": workout.created_at.isoformat() if workout.created_at else None,
        "updated_at": workout.updated_at.isoformat() if workout.updated_at else None,
        "exercises": [
            {
                "id": e.id,
                "exercise_name": e.exercise_name,
                "set_number": e.set_number,
                "reps": e.reps,
                "weight_lbs": float(e.weight_lbs) if e.weight_lbs else None,
                "weight_kg": float(e.weight_kg) if e.weight_kg else None,
                "rpe": e.rpe,
                "tempo": e.tempo,
                "rest_seconds": e.rest_seconds,
                "notes": e.notes,
            }
            for e in exercises
        ],
    }


@app.put("/api/workouts/{workout_id}", tags=["Workouts"])
async def update_workout(
    workout_id: int,
    workout_data: WorkoutUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update workout metadata."""
    user = get_current_user(request, db)
    workout = (
        db.query(Workout)
        .filter(Workout.id == workout_id, Workout.user_id == user.id)
        .first()
    )
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    for field, value in workout_data.model_dump(exclude_unset=True).items():
        setattr(workout, field, value)
    workout.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(workout)
    return {"id": workout.id, "updated_at": workout.updated_at.isoformat()}


@app.delete(
    "/api/workouts/{workout_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Workouts"],
)
async def delete_workout(
    workout_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a workout and cascade to its exercises."""
    user = get_current_user(request, db)
    workout = (
        db.query(Workout)
        .filter(Workout.id == workout_id, Workout.user_id == user.id)
        .first()
    )
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    db.delete(workout)
    db.commit()


# ── Exercise endpoints ────────────────────────────────────────────────────────

@app.get("/api/exercises", tags=["Exercises"])
async def list_exercise_names(
    request: Request,
    db: Session = Depends(get_db),
):
    """All exercise definitions, plus any exercise names the user has logged that
    lack a definition (for backward compatibility with data logged before the
    exercise_definitions table existed)."""
    user = get_current_user(request, db)

    definitions = (
        db.query(ExerciseDefinition)
        .order_by(ExerciseDefinition.muscle_group.nulls_last(), ExerciseDefinition.name)
        .all()
    )
    defined_names = {d.name for d in definitions}

    # Legacy exercises logged by this user that have no definition entry.
    logged = (
        db.query(Exercise.exercise_name)
        .filter(Exercise.user_id == user.id)
        .distinct()
        .order_by(Exercise.exercise_name)
        .all()
    )

    result = [
        {"name": d.name, "muscle_group": d.muscle_group}
        for d in definitions
    ]
    for (name,) in logged:
        if name not in defined_names:
            result.append({"name": name, "muscle_group": None})

    return result


@app.post("/api/exercises", tags=["Exercises"], status_code=status.HTTP_201_CREATED)
async def create_exercise_definition(
    data: ExerciseDefinitionCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Add (or upsert) an exercise definition with an optional muscle group."""
    get_current_user(request, db)  # require authentication

    name = data.name.strip()
    muscle_group = data.muscle_group.strip() if data.muscle_group else None

    existing = (
        db.query(ExerciseDefinition)
        .filter(ExerciseDefinition.name == name)
        .first()
    )
    if existing:
        if muscle_group:
            existing.muscle_group = muscle_group
            db.commit()
        return {"name": existing.name, "muscle_group": existing.muscle_group}

    definition = ExerciseDefinition(name=name, muscle_group=muscle_group)
    db.add(definition)
    db.commit()
    db.refresh(definition)
    return {"name": definition.name, "muscle_group": definition.muscle_group}


@app.get("/api/exercises/{exercise_name}/history", tags=["Exercises"])
async def get_exercise_history(
    exercise_name: str,
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    """All logged sets for a specific exercise, most recent first."""
    user = get_current_user(request, db)
    rows = (
        db.query(Exercise, Workout.workout_date)
        .join(Workout, Exercise.workout_id == Workout.id)
        .filter(
            Exercise.user_id == user.id,
            Exercise.exercise_name == exercise_name,
        )
        .order_by(Workout.workout_date.desc(), Exercise.set_number)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "workout_id": e.workout_id,
            "workout_date": str(wd),
            "set_number": e.set_number,
            "reps": e.reps,
            "weight_lbs": float(e.weight_lbs) if e.weight_lbs else None,
            "weight_kg": float(e.weight_kg) if e.weight_kg else None,
            "rpe": e.rpe,
            "notes": e.notes,
        }
        for e, wd in rows
    ]


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats/prs", tags=["Stats"])
async def get_personal_records(
    request: Request,
    db: Session = Depends(get_db),
):
    """Per-exercise PR (all-time max weight) and most recent set for the current user."""
    user = get_current_user(request, db)
    rows = db.execute(
        text("""
            WITH latest_per_exercise AS (
                SELECT DISTINCT ON (e.exercise_name)
                    e.exercise_name,
                    e.weight_lbs  AS recent_weight,
                    e.reps        AS recent_reps,
                    w.workout_date AS last_logged
                FROM exercises e
                JOIN workouts w ON e.workout_id = w.id
                WHERE e.user_id = :uid AND e.weight_lbs IS NOT NULL
                ORDER BY e.exercise_name, w.workout_date DESC, e.set_number DESC
            ),
            pr_per_exercise AS (
                SELECT exercise_name, MAX(weight_lbs) AS pr_weight
                FROM exercises
                WHERE user_id = :uid AND weight_lbs IS NOT NULL
                GROUP BY exercise_name
            )
            SELECT
                l.exercise_name,
                l.recent_weight,
                l.recent_reps,
                l.last_logged,
                p.pr_weight
            FROM latest_per_exercise l
            JOIN pr_per_exercise p ON l.exercise_name = p.exercise_name
            ORDER BY l.exercise_name
        """),
        {"uid": user.id},
    ).fetchall()
    return [
        {
            "exercise_name": r[0],
            "recent_weight": float(r[1]) if r[1] else None,
            "recent_reps": r[2],
            "last_logged": str(r[3]) if r[3] else None,
            "pr_weight": float(r[4]) if r[4] else None,
        }
        for r in rows
    ]


@app.get("/api/stats/volume-by-muscle", tags=["Stats"])
async def get_volume_by_muscle(
    request: Request,
    db: Session = Depends(get_db),
):
    """Sets per muscle group for the current user over the last 90 days."""
    user = get_current_user(request, db)
    rows = db.execute(
        text("""
            SELECT
                COALESCE(ed.muscle_group, 'Other') AS muscle_group,
                COUNT(e.id) AS set_count
            FROM exercises e
            JOIN workouts w ON e.workout_id = w.id
            LEFT JOIN exercise_definitions ed ON e.exercise_name = ed.name
            WHERE e.user_id = :uid
              AND w.workout_date >= CURRENT_DATE - INTERVAL '90 days'
            GROUP BY COALESCE(ed.muscle_group, 'Other')
            ORDER BY set_count DESC
        """),
        {"uid": user.id},
    ).fetchall()
    return [{"muscle_group": r[0], "set_count": r[1]} for r in rows]


@app.get("/api/stats/summary", tags=["Stats"])
async def get_stats_summary(
    request: Request,
    db: Session = Depends(get_db),
):
    """High-level training summary for the current user."""
    user = get_current_user(request, db)
    total_workouts = (
        db.query(func.count(Workout.id))
        .filter(Workout.user_id == user.id)
        .scalar()
    )
    total_sets = (
        db.query(func.count(Exercise.id))
        .filter(Exercise.user_id == user.id)
        .scalar()
    )
    unique_exercises = (
        db.query(func.count(func.distinct(Exercise.exercise_name)))
        .filter(Exercise.user_id == user.id)
        .scalar()
    )
    last_workout = (
        db.query(Workout.workout_date)
        .filter(Workout.user_id == user.id)
        .order_by(Workout.workout_date.desc())
        .first()
    )
    return {
        "total_workouts": total_workouts,
        "total_sets": total_sets,
        "unique_exercises": unique_exercises,
        "last_workout_date": str(last_workout[0]) if last_workout else None,
    }


# ── Goals ─────────────────────────────────────────────────────────────────────

@app.get("/api/goals", tags=["Goals"])
async def list_goals(
    request: Request,
    db: Session = Depends(get_db),
):
    """List goals for the current user."""
    user = get_current_user(request, db)
    rows = db.execute(
        text(
            "SELECT id, goal_text, target_date, is_completed, completed_date, created_at "
            "FROM goals WHERE user_id = :uid ORDER BY created_at DESC"
        ),
        {"uid": user.id},
    ).fetchall()
    return [
        {
            "id": r[0],
            "goal_text": r[1],
            "target_date": str(r[2]) if r[2] else None,
            "is_completed": r[3],
            "completed_date": str(r[4]) if r[4] else None,
            "created_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": "Database unavailable"},
        )


# ── Static files / SPA ────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_root():
    return FileResponse("static/index.html")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return FileResponse("static/index.html")
