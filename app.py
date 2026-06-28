import csv
import random
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session

from database import init_db, save_study_session, get_parent_summary, get_recent_sessions

app = Flask(__name__)
app.secret_key = "smart-drill-dev-secret"

BASE_DIR = Path(__file__).resolve().parent
QUESTIONS_CSV = BASE_DIR / "data" / "questions.csv"

CHILDREN = ["長女", "次女"]
QUIZ_SIZE = 10


def load_questions():
    questions = []
    with QUESTIONS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            row["id"] = i
            choices = [row["choice1"], row["choice2"], row["choice3"], row["choice4"]]
            row["choices"] = choices
            row.setdefault("hint1", "")
            row.setdefault("hint2", "")
            row.setdefault("explanation", "")
            questions.append(row)
    return questions


def build_grade_category_map(questions):
    result = {"all": sorted({q["category"] for q in questions})}
    for q in questions:
        result.setdefault(q["grade"], set()).add(q["category"])
    return {grade: sorted(categories) for grade, categories in result.items()}


@app.route("/")
def index():
    questions = load_questions()
    grades = sorted({q["grade"] for q in questions})
    grade_category_map = build_grade_category_map(questions)

    error_message = session.pop("error_message", "")
    selected_child = session.pop("selected_child", "長女")
    selected_grade = session.pop("selected_grade", "all")
    selected_category = session.pop("selected_category", "all")

    return render_template(
        "index.html",
        children=CHILDREN,
        grades=grades,
        grade_category_map=grade_category_map,
        error_message=error_message,
        selected_child=selected_child,
        selected_grade=selected_grade,
        selected_category=selected_category,
    )


@app.route("/start", methods=["POST"])
def start():
    child = request.form.get("child", "長女")
    grade = request.form.get("grade", "all")
    category = request.form.get("category", "all")

    questions = load_questions()
    if grade != "all":
        questions = [q for q in questions if q["grade"] == grade]
    if category != "all":
        questions = [q for q in questions if q["category"] == category]

    if len(questions) == 0:
        session["error_message"] = "この条件の問題はまだありません。別の学年・単元を選んでください。"
        session["selected_child"] = child
        session["selected_grade"] = grade
        session["selected_category"] = category
        return redirect(url_for("index"))

    random.shuffle(questions)
    selected = questions[:QUIZ_SIZE]

    for q in selected:
        shuffled = q["choices"][:]
        random.shuffle(shuffled)
        q["choices"] = shuffled

    session["child"] = child
    session["quiz"] = selected
    session["current_index"] = 0
    session["answers"] = []
    session["started_at"] = datetime.now().isoformat(timespec="seconds")
    session.pop("saved_session_id", None)

    return redirect(url_for("quiz"))


def current_quiz():
    return session.get("quiz", [])


def current_index():
    return session.get("current_index", 0)


@app.route("/quiz")
def quiz():
    quiz_data = current_quiz()
    idx = current_index()

    if not quiz_data:
        return redirect(url_for("index"))
    if idx >= len(quiz_data):
        return redirect(url_for("result"))

    q = quiz_data[idx]
    return render_template(
        "quiz.html",
        question=q,
        current_no=idx + 1,
        total=len(quiz_data),
        child=session.get("child", ""),
    )


@app.route("/answer", methods=["POST"])
def answer():
    quiz_data = current_quiz()
    idx = current_index()

    if not quiz_data or idx >= len(quiz_data):
        return redirect(url_for("index"))

    q = quiz_data[idx]
    selected = request.form.get("selected", "")
    hint_count = int(request.form.get("hint_count", "0"))
    is_correct = selected == q["answer"]
    score = max(10 - hint_count, 0) if is_correct else 0

    answer_record = {
        "question_id": q["id"],
        "grade": q["grade"],
        "category": q["category"],
        "question": q["question"],
        "selected": selected,
        "answer": q["answer"],
        "is_correct": is_correct,
        "hint_count": hint_count,
        "score": score,
        "explanation": q.get("explanation", ""),
    }

    answers = session.get("answers", [])
    answers.append(answer_record)
    session["answers"] = answers

    return render_template(
        "feedback.html",
        record=answer_record,
        current_no=idx + 1,
        total=len(quiz_data),
    )


@app.route("/next")
def next_question():
    session["current_index"] = current_index() + 1
    if current_index() >= len(current_quiz()):
        session["ended_at"] = datetime.now().isoformat(timespec="seconds")
        return redirect(url_for("result"))
    return redirect(url_for("quiz"))


@app.route("/result")
def result():
    answers = session.get("answers", [])
    total = len(answers)
    correct = sum(1 for a in answers if a["is_correct"])
    total_score = sum(a["score"] for a in answers)
    max_score = total * 10
    hint_total = sum(a["hint_count"] for a in answers)
    hint_used_questions = sum(1 for a in answers if a["hint_count"] > 0)
    accuracy = round(correct / total * 100, 1) if total else 0
    score_rate = round(total_score / max_score * 100, 1) if max_score else 0
    hint_rate = round(hint_used_questions / total * 100, 1) if total else 0

    started_at = session.get("started_at")
    ended_at = session.get("ended_at", datetime.now().isoformat(timespec="seconds"))
    duration_seconds = 0
    if started_at:
        try:
            duration_seconds = int((datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds())
        except Exception:
            duration_seconds = 0

    saved_session_id = session.get("saved_session_id")
    if answers and not saved_session_id:
        saved_session_id = save_study_session(
            child=session.get("child", ""),
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration_seconds,
            answers=answers,
        )
        session["saved_session_id"] = saved_session_id

    return render_template(
        "result.html",
        child=session.get("child", ""),
        answers=answers,
        total=total,
        correct=correct,
        accuracy=accuracy,
        total_score=total_score,
        max_score=max_score,
        score_rate=score_rate,
        hint_total=hint_total,
        hint_rate=hint_rate,
        duration_seconds=duration_seconds,
        saved_session_id=saved_session_id,
    )


@app.route("/parent")
def parent():
    summaries = get_parent_summary()
    recent_sessions = get_recent_sessions(limit=20)
    return render_template("parent.html", summaries=summaries, recent_sessions=recent_sessions)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
