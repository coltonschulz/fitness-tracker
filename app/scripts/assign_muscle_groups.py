"""Bulk-assign muscle groups to all exercises in the database.

Reads every unique exercise name from the `exercises` (log) and
`exercise_definitions` (catalogue) tables, then:

  1. Groups names that are case/spacing duplicates of each other.
  2. Merges known spelling variants (SPELLING_ALIASES) into one canonical name.
  3. Renames rows in the `exercises` log table to the canonical form.
  4. Upserts canonical name + muscle group into `exercise_definitions`.
  5. Removes stale `exercise_definitions` rows that were aliases.

Run inside the running container (no rebuild needed):
    docker cp ~/docker/fitness/app/scripts/assign_muscle_groups.py \
        fitness-api:/app/assign_muscle_groups.py
    docker exec fitness-api python /app/assign_muscle_groups.py

Or after a fresh build:
    docker exec fitness-api python /app/scripts/assign_muscle_groups.py
"""

import os
import re
import sys
from collections import defaultdict

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Abbreviations that should stay ALL-CAPS in the canonical name.
_UPPER_WORDS = {"rdl", "db", "bb", "ez", "hiit", "ohp"}


def normalise(name: str) -> str:
    """Lowercase + collapse whitespace/punctuation for fuzzy comparison."""
    n = name.lower().strip()
    n = re.sub(r"[''`]", "", n)       # drop apostrophes
    n = re.sub(r"[-_/]", " ", n)      # unify separators
    n = re.sub(r"\s+", " ", n)        # collapse whitespace
    return n


def title_case(name: str) -> str:
    """Capitalise each word; preserve known abbreviations in ALL-CAPS."""
    words = []
    for word in name.strip().split():
        pre = word[: len(word) - len(word.lstrip("("))]
        suf = word[len(word.rstrip(")")):]
        inner = word.strip("()")
        if inner.lower() in _UPPER_WORDS:
            words.append(pre + inner.upper() + suf)
        else:
            words.append(pre + inner.capitalize() + suf)
    return " ".join(words)


# ---------------------------------------------------------------------------
# SPELLING_ALIASES
# Maps the *normalised* form of an alias to the *normalised* form of the
# canonical name that should survive in the DB.  Add entries here whenever
# two names refer to the same exercise but won't collapse via case-folding.
# ---------------------------------------------------------------------------
SPELLING_ALIASES: dict[str, str] = {
    # plural / singular
    "cable bicep curls":              "cable bicep curl",
    "overhead tricep extensions":     "overhead tricep extension",
    "cable lateral raises":           "cable lateral raise",
    "hammer curls":                   "hammer curl",
    # spelling variants
    "cable flys (high to low)":       "cable flyes (high to low)",
    "cable flyes (high-to-low)":      "cable flyes (high to low)",
    "cable flys (low to high)":       "cable flyes (low to high)",
    "cable flyes (low-to-high)":      "cable flyes (low to high)",
    "dumbbell flyes":                 "dumbbell fly",
    "dumbbell flies":                 "dumbbell fly",
    "peck deck":                      "pec deck",       # common misspelling
    "reverse peck deck":              "reverse pec deck",
    "cable crunches":                 "cable crunch",
    "bird dogs":                      "bird dog",
    "dead bugs":                      "dead bug",
    "face pulls":                     "face pull",
    "cable rows":                     "cable row",
    # semantic duplicates (keep the more descriptive name)
    "cable abductor":                 "cable hip abductor",
    "calf extension machine":         "calf extensions",
    # equipment qualifiers that duplicate a simpler entry
    "seated leg press":               "leg press",
    "seated rowing machine":          "cable row",
    # Smith-machine variants → keep as-is (they're distinct enough)
    # RDL — user classifies as Legs
    "rdl":                            "romanian deadlift (rdl)",
    "romanian deadlift":              "romanian deadlift (rdl)",
    # DB / BB suffix normalisation
    "cross body hammer curls (db)":   "cross body hammer curl",
    "overhead press (db)":            "overhead press",
    "dumbbell bicep curl":            "bicep curl",
}

# ---------------------------------------------------------------------------
# EXACT muscle-group map  (keyed on *normalised* canonical name)
# ---------------------------------------------------------------------------
EXACT: dict[str, str] = {
    # ── Chest ────────────────────────────────────────────────────────────────
    "bench press":                    "Chest",
    "barbell bench press":            "Chest",
    "flat bench press":               "Chest",
    "incline bench press":            "Chest",
    "decline bench press":            "Chest",
    "dumbbell bench press":           "Chest",
    "dumbbell press":                 "Chest",
    "incline dumbbell press":         "Chest",
    "decline dumbbell press":         "Chest",
    "incline dumbbell fly":           "Chest",
    "dumbbell fly":                   "Chest",
    "cable fly":                      "Chest",
    "cable flyes (low to high)":      "Chest",
    "cable flyes (high to low)":      "Chest",
    "cable crossover":                "Chest",
    "pec deck":                       "Chest",
    "pec dec":                        "Chest",
    "chest fly":                      "Chest",
    "chest press":                    "Chest",
    "chest press machine":            "Chest",
    "machine chest press":            "Chest",
    "smith machine bench press":      "Chest",
    "push up":                        "Chest",
    "push-up":                        "Chest",
    "pushup":                         "Chest",
    "wide push up":                   "Chest",
    "dips":                           "Chest",
    "chest dips":                     "Chest",
    "svend press":                    "Chest",

    # ── Back ─────────────────────────────────────────────────────────────────
    "pull up":                        "Back",
    "pull-up":                        "Back",
    "pullup":                         "Back",
    "chin up":                        "Back",
    "chin-up":                        "Back",
    "chinup":                         "Back",
    "lat pulldown":                   "Back",
    "wide grip lat pulldown":         "Back",
    "close grip lat pulldown":        "Back",
    "cable lat pulldown":             "Back",
    "single arm lat pulldown":        "Back",
    "cable row":                      "Back",
    "seated cable row":               "Back",
    "low cable row":                  "Back",
    "chest supported row":            "Back",
    "bent over row":                  "Back",
    "barbell row":                    "Back",
    "bent over barbell row":          "Back",
    "pendlay row":                    "Back",
    "t-bar row":                      "Back",
    "t bar row":                      "Back",
    "machine row":                    "Back",
    "smith machine rows":             "Back",
    "single arm dumbbell row":        "Back",
    "one arm dumbbell row":           "Back",
    "dumbbell row":                   "Back",
    "meadows row":                    "Back",
    "deadlift":                       "Back",
    "conventional deadlift":          "Back",
    "sumo deadlift":                  "Back",
    "stiff leg deadlift":             "Back",
    "straight leg deadlift":          "Back",
    "good morning":                   "Back",
    "hyperextension":                 "Back",
    "back extension":                 "Back",
    "45 degree back extension":       "Back",
    "face pull":                      "Back",
    "reverse pec deck":               "Back",
    "straight arm pulldown":          "Back",
    "cable pullover":                 "Back",
    "dumbbell pullover":              "Back",
    "seated row":                     "Back",
    "inverted row":                   "Back",
    "rack pull":                      "Back",
    "shrug":                          "Back",
    "barbell shrug":                  "Back",
    "dumbbell shrug":                 "Back",
    "trap bar deadlift":              "Back",

    # ── Shoulders ────────────────────────────────────────────────────────────
    "overhead press":                 "Shoulders",
    "ohp":                            "Shoulders",
    "barbell overhead press":         "Shoulders",
    "military press":                 "Shoulders",
    "seated overhead press":          "Shoulders",
    "seated dumbbell press":          "Shoulders",
    "dumbbell shoulder press":        "Shoulders",
    "arnold press":                   "Shoulders",
    "cable shoulder press":           "Shoulders",
    "machine shoulder press":         "Shoulders",
    "shoulder press machine":         "Shoulders",
    "overhead smith press":           "Shoulders",
    "push press":                     "Shoulders",
    "lateral raise":                  "Shoulders",
    "dumbbell lateral raise":         "Shoulders",
    "cable lateral raise":            "Shoulders",
    "machine lateral raise":          "Shoulders",
    "front raise":                    "Shoulders",
    "dumbbell front raise":           "Shoulders",
    "cable front raise":              "Shoulders",
    "rear delt fly":                  "Shoulders",
    "reverse fly":                    "Shoulders",
    "dumbbell reverse fly":           "Shoulders",
    "cable reverse fly":              "Shoulders",
    "rear delt raise":                "Shoulders",
    "bent over rear delt raise":      "Shoulders",
    "upright row":                    "Shoulders",
    "barbell upright row":            "Shoulders",

    # ── Arms ─────────────────────────────────────────────────────────────────
    "bicep curl":                     "Arms",
    "biceps curl":                    "Arms",
    "barbell curl":                   "Arms",
    "ez bar curl":                    "Arms",
    "ez curl":                        "Arms",
    "dumbbell curl":                  "Arms",
    "alternating dumbbell curl":      "Arms",
    "hammer curl":                    "Arms",
    "cross body hammer curl":         "Arms",
    "cable curl":                     "Arms",
    "cable bicep curl":               "Arms",
    "preacher curl":                  "Arms",
    "preacher ez curl":               "Arms",
    "concentration curl":             "Arms",
    "incline dumbbell curl":          "Arms",
    "zottman curl":                   "Arms",
    "reverse curl":                   "Arms",
    "spider curl":                    "Arms",
    "machine curl":                   "Arms",
    "tricep pushdown":                "Arms",
    "triceps pushdown":               "Arms",
    "cable tricep pushdown":          "Arms",
    "rope pushdown":                  "Arms",
    "rope tricep pushdown":           "Arms",
    "tricep rope pushdown":           "Arms",
    "v-bar pushdown":                 "Arms",
    "overhead tricep extension":      "Arms",
    "overhead dumbbell tricep extension": "Arms",
    "cable overhead tricep extension": "Arms",
    "skull crusher":                  "Arms",
    "ez bar skull crusher":           "Arms",
    "lying tricep extension":         "Arms",
    "close grip bench press":         "Arms",
    "tricep dips":                    "Arms",
    "diamond push up":                "Arms",
    "tricep kickback":                "Arms",
    "cable kickback":                 "Arms",
    "machine tricep extension":       "Arms",
    "wrist curl":                     "Arms",
    "reverse wrist curl":             "Arms",

    # ── Legs ─────────────────────────────────────────────────────────────────
    "squat":                          "Legs",
    "back squat":                     "Legs",
    "front squat":                    "Legs",
    "high bar squat":                 "Legs",
    "low bar squat":                  "Legs",
    "goblet squat":                   "Legs",
    "hack squat":                     "Legs",
    "belt squat":                     "Legs",
    "pause squat":                    "Legs",
    "box squat":                      "Legs",
    "overhead squat":                 "Legs",
    "smith machine squat":            "Legs",
    "leg press":                      "Legs",
    "leg extension":                  "Legs",
    "machine leg extension":          "Legs",
    "leg curl":                       "Legs",
    "lying leg curl":                 "Legs",
    "seated leg curl":                "Legs",
    "standing leg curl":              "Legs",
    "nordic curl":                    "Legs",
    "lunge":                          "Legs",
    "walking lunge":                  "Legs",
    "reverse lunge":                  "Legs",
    "lateral lunge":                  "Legs",
    "bulgarian split squat":          "Legs",
    "split squat":                    "Legs",
    "step up":                        "Legs",
    "box step up":                    "Legs",
    "hip thrust":                     "Legs",
    "barbell hip thrust":             "Legs",
    "glute bridge":                   "Legs",
    "single leg glute bridge":        "Legs",
    "glute trainer machine":          "Legs",
    "calf raise":                     "Legs",
    "standing calf raise":            "Legs",
    "seated calf raise":              "Legs",
    "donkey calf raise":              "Legs",
    "calf extensions":                "Legs",
    "adductor machine":               "Legs",
    "abductor machine":               "Legs",
    "cable hip abductor":             "Legs",
    "cable hip adductor":             "Legs",
    "inner thigh machine":            "Legs",
    "outer thigh machine":            "Legs",
    "sumo squat":                     "Legs",
    "wall sit":                       "Legs",
    "glute kickback":                 "Legs",
    "cable glute kickback":           "Legs",
    "romanian deadlift (rdl)":        "Legs",
    "cable knee up":                  "Legs",

    # ── Core ─────────────────────────────────────────────────────────────────
    "plank":                          "Core",
    "front plank":                    "Core",
    "side plank":                     "Core",
    "crunch":                         "Core",
    "sit up":                         "Core",
    "sit-up":                         "Core",
    "situp":                          "Core",
    "cable crunch":                   "Core",
    "machine crunch":                 "Core",
    "ab wheel":                       "Core",
    "ab rollout":                     "Core",
    "hollow hold":                    "Core",
    "hollow body":                    "Core",
    "leg raise":                      "Core",
    "hanging leg raise":              "Core",
    "lying leg raise":                "Core",
    "toes to bar":                    "Core",
    "knee raise":                     "Core",
    "hanging knee raise":             "Core",
    "russian twist":                  "Core",
    "bicycle crunch":                 "Core",
    "v-up":                           "Core",
    "v up":                           "Core",
    "dragon flag":                    "Core",
    "pallof press":                   "Core",
    "cable pallof press":             "Core",
    "dead bug":                       "Core",
    "bird dog":                       "Core",
    "cat cow":                        "Core",
    "wood chop":                      "Core",
    "cable wood chop":                "Core",
    "landmine rotation":              "Core",
    "decline crunch":                 "Core",
    "incline sit up":                 "Core",
    "medicine ball slam":             "Core",

    # ── Cardio ───────────────────────────────────────────────────────────────
    "running":                        "Cardio",
    "treadmill":                      "Cardio",
    "treadmill run":                  "Cardio",
    "elliptical":                     "Cardio",
    "recumbent bike":                 "Cardio",
    "stationary bike":                "Cardio",
    "cycling":                        "Cardio",
    "spin bike":                      "Cardio",
    "assault bike":                   "Cardio",
    "air bike":                       "Cardio",
    "rowing":                         "Cardio",
    "rowing machine":                 "Cardio",
    "erg":                            "Cardio",
    "jump rope":                      "Cardio",
    "stair climber":                  "Cardio",
    "stairmaster":                    "Cardio",
    "step mill":                      "Cardio",
    "sled push":                      "Cardio",
    "sled pull":                      "Cardio",
    "farmers walk":                   "Cardio",
    "farmer carry":                   "Cardio",
    "battle ropes":                   "Cardio",
    "sprints":                        "Cardio",
    "hiit":                           "Cardio",
    "box jump":                       "Cardio",
    "jump squat":                     "Cardio",
}

# Keyword fallback — first match wins; ordered specific → broad.
KEYWORDS: list[tuple[str, str]] = [
    ("tricep",         "Arms"),
    ("bicep",          "Arms"),
    ("biceps",         "Arms"),
    ("hammer curl",    "Arms"),
    ("curl",           "Arms"),
    ("pushdown",       "Arms"),
    ("skull",          "Arms"),
    ("wrist",          "Arms"),
    ("forearm",        "Arms"),
    ("chest",          "Chest"),
    ("pec",            "Chest"),
    ("bench",          "Chest"),
    ("fly",            "Chest"),
    ("crossover",      "Chest"),
    ("lat ",           "Back"),
    ("pulldown",       "Back"),
    ("pull up",        "Back"),
    ("pullup",         "Back"),
    ("chin up",        "Back"),
    ("chinup",         "Back"),
    ("row",            "Back"),
    ("deadlift",       "Back"),
    ("back extension", "Back"),
    ("hyperextension", "Back"),
    ("shrug",          "Back"),
    ("face pull",      "Back"),
    ("shoulder",       "Shoulders"),
    ("lateral raise",  "Shoulders"),
    ("front raise",    "Shoulders"),
    ("rear delt",      "Shoulders"),
    ("upright row",    "Shoulders"),
    ("overhead press", "Shoulders"),
    ("military press", "Shoulders"),
    ("arnold",         "Shoulders"),
    ("squat",          "Legs"),
    ("lunge",          "Legs"),
    ("leg press",      "Legs"),
    ("leg curl",       "Legs"),
    ("leg extension",  "Legs"),
    ("hip thrust",     "Legs"),
    ("glute",          "Legs"),
    ("calf",           "Legs"),
    ("hamstring",      "Legs"),
    ("quad",           "Legs"),
    ("adductor",       "Legs"),
    ("abductor",       "Legs"),
    ("step up",        "Legs"),
    ("split squat",    "Legs"),
    ("kickback",       "Legs"),
    ("plank",          "Core"),
    ("crunch",         "Core"),
    ("sit up",         "Core"),
    ("ab ",            "Core"),
    ("core",           "Core"),
    ("oblique",        "Core"),
    ("russian twist",  "Core"),
    ("leg raise",      "Core"),
    ("pallof",         "Core"),
    ("treadmill",      "Cardio"),
    ("elliptical",     "Cardio"),
    ("bike",           "Cardio"),
    ("rowing",         "Cardio"),
    ("cardio",         "Cardio"),
    ("sprint",         "Cardio"),
    ("running",        "Cardio"),
    ("jump rope",      "Cardio"),
    ("sled",           "Cardio"),
    ("farmer",         "Cardio"),
]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve(normalised_name: str) -> str | None:
    """Return muscle group for a *normalised* name, or None."""
    if normalised_name in EXACT:
        return EXACT[normalised_name]
    for kw, group in KEYWORDS:
        if kw in normalised_name:
            return group
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("ERROR: DATABASE_URL environment variable is not set.")

    engine = create_engine(database_url)

    with engine.connect() as conn:
        # ── 1. Collect all raw names ─────────────────────────────────────────
        logged = {
            row[0]
            for row in conn.execute(
                text("SELECT DISTINCT exercise_name FROM exercises")
            ).fetchall()
        }
        defined = {
            row[0]
            for row in conn.execute(
                text("SELECT DISTINCT name FROM exercise_definitions")
            ).fetchall()
        }
        all_raw: set[str] = logged | defined
        print(f"Found {len(all_raw)} raw exercise names across both tables.\n")

        # ── 2. Group by normalised form (catches case / spacing duplicates) ──
        # norm_key → list of raw names that share it
        groups: dict[str, list[str]] = defaultdict(list)
        for raw in all_raw:
            groups[normalise(raw)].append(raw)

        # ── 3. Apply SPELLING_ALIASES to merge cross-group duplicates ────────
        # alias_norm → canonical_norm
        # Walk each group; if its norm key is in SPELLING_ALIASES, redirect it
        # to the canonical group (creating that group if it doesn't exist yet).
        canonical_groups: dict[str, list[str]] = defaultdict(list)
        for norm_key, raw_names in groups.items():
            target = SPELLING_ALIASES.get(norm_key, norm_key)
            canonical_groups[target].extend(raw_names)

        # ── 4. For each canonical group, pick the display name and resolve ───
        upserted: list[dict] = []
        renamed: list[dict] = []   # for updating the exercises log table
        stale_defs: list[str] = [] # exercise_definitions rows to delete
        unresolved: list[str] = []

        for canon_norm, raw_names in sorted(canonical_groups.items()):
            muscle_group = resolve(canon_norm)
            # Derive a nice display name from the canonical norm key
            canon_display = title_case(canon_norm)

            if muscle_group is None:
                unresolved.extend(raw_names)
                continue

            upserted.append({"name": canon_display, "muscle_group": muscle_group})

            # Any raw name that isn't already the canonical display needs renaming
            for raw in raw_names:
                if raw != canon_display:
                    renamed.append({"old": raw, "new": canon_display})
                    if raw in defined:
                        stale_defs.append(raw)

        # ── 5. Rename in the exercises log table ─────────────────────────────
        if renamed:
            print(f"Renaming {len(renamed)} alias(es) in the exercises log table:")
            for r in renamed:
                conn.execute(
                    text(
                        "UPDATE exercises SET exercise_name = :new "
                        "WHERE exercise_name = :old"
                    ),
                    r,
                )
                print(f"   '{r['old']}' → '{r['new']}'")
            conn.commit()

        # ── 6. Remove stale exercise_definitions rows ────────────────────────
        if stale_defs:
            print(f"\nRemoving {len(stale_defs)} stale definition(s):")
            for name in stale_defs:
                conn.execute(
                    text("DELETE FROM exercise_definitions WHERE name = :name"),
                    {"name": name},
                )
                print(f"   deleted '{name}'")
            conn.commit()

        # ── 7. Upsert canonical definitions ──────────────────────────────────
        if upserted:
            conn.execute(
                text("""
                    INSERT INTO exercise_definitions (name, muscle_group)
                    VALUES (:name, :muscle_group)
                    ON CONFLICT (name)
                    DO UPDATE SET muscle_group = EXCLUDED.muscle_group
                """),
                upserted,
            )
            conn.commit()
            print(f"\n✓ Upserted {len(upserted)} canonical exercise definitions:")
            for row in upserted:
                print(f"   {row['muscle_group']:12s}  {row['name']}")

        if unresolved:
            print(
                f"\n⚠  Could not resolve {len(unresolved)} exercise(s) "
                f"— left unchanged:"
            )
            for name in sorted(set(unresolved)):
                print(f"   {name}")
        else:
            print("\nAll exercises resolved — no manual cleanup needed.")


if __name__ == "__main__":
    main()
