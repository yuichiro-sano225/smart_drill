import sqlite3
from pathlib import Path
from datetime import datetime, date

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "smart_drill.db"


def get_connection():
    DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            child TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            duration_seconds INTEGER DEFAULT 0,
            total_questions INTEGER DEFAULT 0,
            correct_count INTEGER DEFAULT 0,
            accuracy REAL DEFAULT 0,
            total_score INTEGER DEFAULT 0,
            max_score INTEGER DEFAULT 0,
            score_rate REAL DEFAULT 0,
            hint_total INTEGER DEFAULT 0,
            hint_rate REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            question_id INTEGER,
            grade TEXT,
            category TEXT,
            question TEXT,
            selected TEXT,
            answer TEXT,
            is_correct INTEGER,
            hint_count INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            explanation TEXT,
            FOREIGN KEY(session_id) REFERENCES study_sessions(id)
        )
        """
    )
    conn.commit()
    conn.close()


def save_study_session(child, started_at, ended_at, duration_seconds, answers):
    init_db()
    total = len(answers)
    correct = sum(1 for a in answers if a.get("is_correct"))
    total_score = sum(int(a.get("score", 0)) for a in answers)
    max_score = total * 10
    hint_total = sum(int(a.get("hint_count", 0)) for a in answers)
    hint_used_questions = sum(1 for a in answers if int(a.get("hint_count", 0)) > 0)
    accuracy = round(correct / total * 100, 1) if total else 0
    score_rate = round(total_score / max_score * 100, 1) if max_score else 0
    hint_rate = round(hint_used_questions / total * 100, 1) if total else 0

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO study_sessions (
            child, started_at, ended_at, duration_seconds,
            total_questions, correct_count, accuracy,
            total_score, max_score, score_rate,
            hint_total, hint_rate, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            child, started_at, ended_at, duration_seconds,
            total, correct, accuracy,
            total_score, max_score, score_rate,
            hint_total, hint_rate, datetime.now().isoformat(timespec="seconds")
        )
    )
    session_id = cur.lastrowid

    for a in answers:
        cur.execute(
            """
            INSERT INTO answer_records (
                session_id, question_id, grade, category, question,
                selected, answer, is_correct, hint_count, score, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                a.get("question_id"), a.get("grade"), a.get("category"), a.get("question"),
                a.get("selected"), a.get("answer"), 1 if a.get("is_correct") else 0,
                int(a.get("hint_count", 0)), int(a.get("score", 0)), a.get("explanation")
            )
        )

    conn.commit()
    conn.close()
    return session_id


def get_recent_sessions(limit=20):
    init_db()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM study_sessions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_parent_summary():
    init_db()
    conn = get_connection()
    today = date.today().isoformat()

    children = conn.execute(
        "SELECT DISTINCT child FROM study_sessions ORDER BY child"
    ).fetchall()

    summaries = []
    for row in children:
        child = row["child"]
        today_rows = conn.execute(
            """
            SELECT * FROM study_sessions
            WHERE child = ? AND substr(created_at, 1, 10) = ?
            ORDER BY id DESC
            """,
            (child, today)
        ).fetchall()

        all_rows = conn.execute(
            """
            SELECT * FROM study_sessions
            WHERE child = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (child,)
        ).fetchall()

        today_count = len(today_rows)
        today_seconds = sum(int(r["duration_seconds"] or 0) for r in today_rows)
        today_questions = sum(int(r["total_questions"] or 0) for r in today_rows)
        today_correct = sum(int(r["correct_count"] or 0) for r in today_rows)
        today_score = sum(int(r["total_score"] or 0) for r in today_rows)
        today_max_score = sum(int(r["max_score"] or 0) for r in today_rows)
        today_hint_total = sum(int(r["hint_total"] or 0) for r in today_rows)

        summaries.append({
            "child": child,
            "today_count": today_count,
            "today_minutes": round(today_seconds / 60, 1),
            "today_accuracy": round(today_correct / today_questions * 100, 1) if today_questions else 0,
            "today_score_rate": round(today_score / today_max_score * 100, 1) if today_max_score else 0,
            "today_hint_total": today_hint_total,
            "recent_sessions": [dict(r) for r in all_rows],
        })

    conn.close()
    return summaries
