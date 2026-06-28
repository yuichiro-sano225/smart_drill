import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "smart_drill.db"

# 連続正解・記憶レベルに応じた次回復習間隔（日）
REVIEW_INTERVAL_DAYS = {
    0: 1,
    1: 1,
    2: 3,
    3: 7,
    4: 14,
    5: 30,
}


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

    # v2.0: 子供ごと・問題ごとの学習状態。
    # CSVは問題そのもの、SQLiteは学習状態、という分離を守る。
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS question_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            child TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            correct_count INTEGER DEFAULT 0,
            wrong_count INTEGER DEFAULT 0,
            hint_count INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            memory_level INTEGER DEFAULT 0,
            last_result TEXT,
            last_answered TEXT,
            next_review TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(child, question_id)
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


def _next_review_date(is_correct, hint_count, memory_level):
    today = date.today()
    if not is_correct:
        return (today + timedelta(days=1)).isoformat()
    if hint_count > 0:
        return (today + timedelta(days=2)).isoformat()
    days = REVIEW_INTERVAL_DAYS.get(memory_level, 30)
    return (today + timedelta(days=days)).isoformat()


def update_question_progress(child, answers):
    """Update per-question learning state after a quiz result is saved."""
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    cur = conn.cursor()

    for a in answers:
        question_id = a.get("question_id")
        if question_id is None:
            continue

        is_correct = bool(a.get("is_correct"))
        used_hint_count = int(a.get("hint_count", 0))

        row = cur.execute(
            """
            SELECT * FROM question_progress
            WHERE child = ? AND question_id = ?
            """,
            (child, question_id)
        ).fetchone()

        if row:
            correct_count = int(row["correct_count"] or 0)
            wrong_count = int(row["wrong_count"] or 0)
            hint_count = int(row["hint_count"] or 0)
            streak = int(row["streak"] or 0)
            memory_level = int(row["memory_level"] or 0)
        else:
            correct_count = wrong_count = hint_count = streak = memory_level = 0

        hint_count += used_hint_count

        if is_correct:
            correct_count += 1
            streak += 1
            # ヒントなし正解は記憶レベルを上げる。ヒントあり正解は定着扱いを弱める。
            if used_hint_count == 0:
                memory_level = min(5, memory_level + 1)
            else:
                memory_level = max(1, memory_level)
            last_result = "correct"
        else:
            wrong_count += 1
            streak = 0
            memory_level = max(0, memory_level - 2)
            last_result = "wrong"

        next_review = _next_review_date(is_correct, used_hint_count, memory_level)

        if row:
            cur.execute(
                """
                UPDATE question_progress
                SET correct_count = ?, wrong_count = ?, hint_count = ?,
                    streak = ?, memory_level = ?, last_result = ?,
                    last_answered = ?, next_review = ?, updated_at = ?
                WHERE child = ? AND question_id = ?
                """,
                (
                    correct_count, wrong_count, hint_count,
                    streak, memory_level, last_result,
                    now, next_review, now,
                    child, question_id,
                )
            )
        else:
            cur.execute(
                """
                INSERT INTO question_progress (
                    child, question_id, correct_count, wrong_count, hint_count,
                    streak, memory_level, last_result, last_answered,
                    next_review, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    child, question_id, correct_count, wrong_count, hint_count,
                    streak, memory_level, last_result, now,
                    next_review, now,
                )
            )

    conn.commit()
    conn.close()


def get_progress_map(child):
    """Return {question_id: progress_dict} for the specified child."""
    init_db()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM question_progress
        WHERE child = ?
        """,
        (child,)
    ).fetchall()
    conn.close()
    return {int(r["question_id"]): dict(r) for r in rows}


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

        progress_rows = conn.execute(
            """
            SELECT
                COUNT(*) AS learned_questions,
                SUM(CASE WHEN next_review IS NOT NULL AND next_review <= ? THEN 1 ELSE 0 END) AS due_questions,
                AVG(memory_level) AS avg_memory_level
            FROM question_progress
            WHERE child = ?
            """,
            (today, child)
        ).fetchone()

        summaries.append({
            "child": child,
            "today_count": today_count,
            "today_minutes": round(today_seconds / 60, 1),
            "today_accuracy": round(today_correct / today_questions * 100, 1) if today_questions else 0,
            "today_score_rate": round(today_score / today_max_score * 100, 1) if today_max_score else 0,
            "today_hint_total": today_hint_total,
            "recent_sessions": [dict(r) for r in all_rows],
            "learned_questions": int(progress_rows["learned_questions"] or 0),
            "due_questions": int(progress_rows["due_questions"] or 0),
            "avg_memory_level": round(float(progress_rows["avg_memory_level"] or 0), 1),
        })

    conn.close()
    return summaries


def get_weak_point_summary(min_answers=1):
    """Return per-child weak point stats grouped by grade and category."""
    init_db()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            s.child AS child,
            a.grade AS grade,
            a.category AS category,
            COUNT(*) AS total_answers,
            SUM(CASE WHEN a.is_correct = 1 THEN 1 ELSE 0 END) AS correct_answers,
            SUM(COALESCE(a.hint_count, 0)) AS hint_total,
            SUM(COALESCE(a.score, 0)) AS total_score
        FROM answer_records a
        JOIN study_sessions s ON s.id = a.session_id
        GROUP BY s.child, a.grade, a.category
        HAVING COUNT(*) >= ?
        ORDER BY s.child ASC,
                 (CAST(SUM(CASE WHEN a.is_correct = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*)) ASC,
                 COUNT(*) DESC
        """,
        (min_answers,)
    ).fetchall()
    conn.close()

    grouped = {}
    for r in rows:
        total = int(r["total_answers"] or 0)
        correct = int(r["correct_answers"] or 0)
        hint_total = int(r["hint_total"] or 0)
        total_score = int(r["total_score"] or 0)
        max_score = total * 10
        item = {
            "grade": r["grade"] or "未設定",
            "category": r["category"] or "未設定",
            "total_answers": total,
            "correct_answers": correct,
            "accuracy": round(correct / total * 100, 1) if total else 0,
            "hint_total": hint_total,
            "hint_rate": round(hint_total / total, 1) if total else 0,
            "score_rate": round(total_score / max_score * 100, 1) if max_score else 0,
        }
        grouped.setdefault(r["child"], []).append(item)

    return grouped


def get_wrong_question_ids(child, limit=10):
    """Return recent distinct question IDs that the child answered incorrectly."""
    init_db()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT a.question_id
        FROM answer_records a
        JOIN study_sessions s ON s.id = a.session_id
        WHERE s.child = ?
          AND a.is_correct = 0
          AND a.question_id IS NOT NULL
        ORDER BY a.id DESC
        """,
        (child,)
    ).fetchall()
    conn.close()

    seen = set()
    result = []
    for row in rows:
        qid = row["question_id"]
        if qid in seen:
            continue
        seen.add(qid)
        result.append(qid)
        if len(result) >= limit:
            break
    return result
