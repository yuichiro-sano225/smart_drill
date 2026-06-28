import csv
import os
import random
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from flask import Flask, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database" / "study.db"
QUESTIONS_CSV = BASE_DIR / "data" / "questions.csv"

app = Flask(__name__)
app.secret_key = "smart-drill-local-secret"

CHILDREN = ["栞莉", "紬葵"]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                correct_count INTEGER NOT NULL,
                accuracy REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                grade TEXT NOT NULL,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                selected_answer TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                explanation TEXT NOT NULL,
                answered_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
        """)


def load_questions():
    questions = []
    with open(QUESTIONS_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            row["id"] = i
            questions.append(row)
    return questions


def get_wrong_question_ids(child_name):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT question_id,
                   SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) AS wrong_count,
                   SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct_count
            FROM answers
            WHERE child_name IS NULL
            GROUP BY question_id
            """
        ).fetchall()
    return [r["question_id"] for r in rows if r["wrong_count"] > r["correct_count"]]


def stats_for_child(child_name):
    with get_db() as conn:
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE child_name = ? ORDER BY started_at DESC LIMIT 20",
            (child_name,),
        ).fetchall()
        totals = conn.execute(
            """
            SELECT COUNT(*) AS session_count,
                   COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                   COALESCE(AVG(accuracy), 0) AS avg_accuracy
            FROM sessions
            WHERE child_name = ?
            """,
            (child_name,),
        ).fetchone()
        by_category = conn.execute(
            """
            SELECT category,
                   COUNT(*) AS total,
                   SUM(is_correct) AS correct,
                   ROUND(100.0 * SUM(is_correct) / COUNT(*), 1) AS accuracy
            FROM answers a
            JOIN sessions s ON s.id = a.session_id
            WHERE s.child_name = ?
            GROUP BY category
            ORDER BY category
            """,
            (child_name,),
        ).fetchall()
        wrongs = conn.execute(
            """
            SELECT question, correct_answer, explanation, COUNT(*) AS wrong_count
            FROM answers a
            JOIN sessions s ON s.id = a.session_id
            WHERE s.child_name = ? AND is_correct = 0
            GROUP BY question_id, question, correct_answer, explanation
            ORDER BY wrong_count DESC
            LIMIT 10
            """,
            (child_name,),
        ).fetchall()
        day_rows = conn.execute(
            """
            SELECT substr(started_at, 1, 10) AS day,
                   SUM(duration_seconds) AS seconds
            FROM sessions
            WHERE child_name = ?
            GROUP BY day
            ORDER BY day DESC
            LIMIT 30
            """,
            (child_name,),
        ).fetchall()

    studied_days = {r["day"] for r in day_rows}
    streak = 0
    d = date.today()
    while d.isoformat() in studied_days:
        streak += 1
        d -= timedelta(days=1)

    total_minutes = round(totals["total_seconds"] / 60, 1)
    level = int(totals["total_seconds"] // 600) + 1
    badges = []
    if totals["session_count"] >= 1:
        badges.append("はじめの一歩")
    if totals["total_seconds"] >= 1800:
        badges.append("30分達成")
    if streak >= 3:
        badges.append("3日連続")
    if totals["avg_accuracy"] >= 80 and totals["session_count"] >= 3:
        badges.append("正答率80%")

    return {
        "sessions": sessions,
        "totals": totals,
        "total_minutes": total_minutes,
        "by_category": by_category,
        "wrongs": wrongs,
        "day_rows": day_rows,
        "streak": streak,
        "level": level,
        "badges": badges,
    }


@app.before_request
def setup():
    init_db()


@app.route("/")
def index():
    questions = load_questions()
    grades = sorted(set(q["grade"] for q in questions))
    categories = sorted(set(q["category"] for q in questions))
    return render_template("index.html", children=CHILDREN, grades=grades, categories=categories)


@app.route("/start", methods=["POST"])
def start():
    child_name = request.form["child_name"]
    grade = request.form.get("grade", "all")
    category = request.form.get("category", "all")
    mode = request.form.get("mode", "normal")

    questions = load_questions()
    if grade != "all":
        questions = [q for q in questions if q["grade"] == grade]
    if category != "all":
        questions = [q for q in questions if q["category"] == category]

    if mode == "review":
        wrong_ids = get_review_ids(child_name)
        questions = [q for q in questions if q["id"] in wrong_ids]

    random.shuffle(questions)
    selected = questions[:10]
    if not selected:
        return render_template("no_questions.html")

    session["quiz"] = {
        "child_name": child_name,
        "mode": mode,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "questions": selected,
        "answers": [],
        "index": 0,
    }
    return redirect(url_for("quiz"))


def get_review_ids(child_name):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT a.question_id,
                   SUM(CASE WHEN a.is_correct = 0 THEN 1 ELSE 0 END) AS wrong_count,
                   SUM(CASE WHEN a.is_correct = 1 THEN 1 ELSE 0 END) AS correct_count
            FROM answers a
            JOIN sessions s ON s.id = a.session_id
            WHERE s.child_name = ?
            GROUP BY a.question_id
            """,
            (child_name,),
        ).fetchall()
    return [r["question_id"] for r in rows if r["wrong_count"] > r["correct_count"]]


@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    quiz_data = session.get("quiz")
    if not quiz_data:
        return redirect(url_for("index"))

    if request.method == "POST":
        q = quiz_data["questions"][quiz_data["index"]]
        selected = request.form["answer"]
        quiz_data["answers"].append({
            "question_id": q["id"],
            "grade": q["grade"],
            "category": q["category"],
            "question": q["question"],
            "selected_answer": selected,
            "correct_answer": q["answer"],
            "is_correct": selected == q["answer"],
            "explanation": q["explanation"],
        })
        quiz_data["index"] += 1
        session["quiz"] = quiz_data
        if quiz_data["index"] >= len(quiz_data["questions"]):
            return redirect(url_for("finish"))

    q = quiz_data["questions"][quiz_data["index"]]
    choices = [q["choice1"], q["choice2"], q["choice3"], q["choice4"]]
    return render_template(
        "quiz.html",
        q=q,
        choices=choices,
        current=quiz_data["index"] + 1,
        total=len(quiz_data["questions"]),
    )


@app.route("/finish")
def finish():
    quiz_data = session.get("quiz")
    if not quiz_data:
        return redirect(url_for("index"))

    ended_at = datetime.now()
    started_at = datetime.fromisoformat(quiz_data["started_at"])
    duration_seconds = max(1, int((ended_at - started_at).total_seconds()))
    answers = quiz_data["answers"]
    total = len(answers)
    correct = sum(1 for a in answers if a["is_correct"])
    accuracy = round(100 * correct / total, 1) if total else 0

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO sessions
            (child_name, mode, started_at, ended_at, duration_seconds, total_questions, correct_count, accuracy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quiz_data["child_name"], quiz_data["mode"], started_at.isoformat(timespec="seconds"),
                ended_at.isoformat(timespec="seconds"), duration_seconds, total, correct, accuracy,
            ),
        )
        session_id = cur.lastrowid
        for a in answers:
            conn.execute(
                """
                INSERT INTO answers
                (session_id, question_id, grade, category, question, selected_answer, correct_answer, is_correct, explanation, answered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, a["question_id"], a["grade"], a["category"], a["question"], a["selected_answer"], a["correct_answer"], int(a["is_correct"]), a["explanation"], ended_at.isoformat(timespec="seconds")),
            )

    session.pop("quiz", None)
    return render_template("result.html", total=total, correct=correct, accuracy=accuracy, duration_seconds=duration_seconds, answers=answers)


@app.route("/parent")
def parent():
    child_name = request.args.get("child_name", CHILDREN[0])
    stats = stats_for_child(child_name)
    return render_template("parent.html", children=CHILDREN, child_name=child_name, stats=stats)


@app.template_filter("minutes")
def minutes_filter(seconds):
    return round(seconds / 60, 1)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)
