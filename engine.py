import csv
import random
from pathlib import Path
from datetime import date, datetime

QUIZ_SIZE = 10


def load_questions(questions_csv: Path):
    """Load questions from CSV and normalize common fields."""
    questions = []
    with questions_csv.open("r", encoding="utf-8-sig", newline="") as f:
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
    """Return available categories for each grade."""
    result = {"all": sorted({q["category"] for q in questions})}
    for q in questions:
        result.setdefault(q["grade"], set()).add(q["category"])
    return {grade: sorted(categories) for grade, categories in result.items()}


def filter_questions(questions, grade="all", category="all"):
    """Filter questions by grade and category for manual practice mode."""
    filtered = questions
    if grade != "all":
        filtered = [q for q in filtered if q["grade"] == grade]
    if category != "all":
        filtered = [q for q in filtered if q["category"] == category]
    return filtered


def shuffle_choices(questions):
    """Return copied questions with shuffled choices."""
    selected = []
    for q in questions:
        q_copy = dict(q)
        shuffled = q_copy["choices"][:]
        random.shuffle(shuffled)
        q_copy["choices"] = shuffled
        selected.append(q_copy)
    return selected


def _days_since(iso_text):
    if not iso_text:
        return None
    try:
        d = datetime.fromisoformat(iso_text).date()
    except ValueError:
        try:
            d = date.fromisoformat(iso_text[:10])
        except ValueError:
            return None
    return (date.today() - d).days


def calculate_priority(question, progress):
    """
    Calculate priority for today's recommended questions.

    Higher score = more likely to be selected.
    This is intentionally simple and explainable. Later, test mode or AI can replace this.
    """
    score = 0
    qid = int(question["id"])
    p = progress.get(qid)

    # 少しだけランダム性を入れて、毎日完全に同じ問題になるのを避ける。
    score += random.uniform(0, 15)

    if not p:
        # まだ解いていない問題も少し混ぜる。
        score += 25
        return score

    today = date.today().isoformat()
    next_review = p.get("next_review")
    last_result = p.get("last_result")
    correct_count = int(p.get("correct_count") or 0)
    wrong_count = int(p.get("wrong_count") or 0)
    hint_count = int(p.get("hint_count") or 0)
    memory_level = int(p.get("memory_level") or 0)

    if next_review and next_review <= today:
        score += 120

    if last_result == "wrong":
        score += 90

    if hint_count > 0:
        score += min(50, hint_count * 10)

    total = correct_count + wrong_count
    if total > 0:
        accuracy = correct_count / total
        if accuracy < 0.5:
            score += 40
        elif accuracy < 0.7:
            score += 20

    # 記憶レベルが低いものを優先。
    score += max(0, 5 - memory_level) * 8

    days_since = _days_since(p.get("last_answered"))
    if days_since is not None:
        if days_since == 0:
            score -= 1000
        elif days_since == 1 and last_result == "correct":
            score -= 30
        elif days_since >= 30:
            score += 25

    return score


def select_questions(
    questions,
    child="",
    grade="all",
    category="all",
    mode="practice",
    count=QUIZ_SIZE,
    fixed_question_ids=None,
    progress_map=None,
):
    """
    Select questions for a quiz.

    Modes:
    - practice: selected grade/category only, random order.
    - review: fixed wrong-question IDs, preserving that order.
    - today: learning-engine mode. Uses question_progress to prioritize due/weak/new questions.
    """
    fixed_question_ids = fixed_question_ids or []
    progress_map = progress_map or {}

    if mode == "review":
        question_map = {q["id"]: q for q in questions}
        selected = [question_map[qid] for qid in fixed_question_ids if qid in question_map]
        return shuffle_choices(selected[:count])

    if mode == "today":
        candidates = filter_questions(questions, grade=grade, category=category)
        if not candidates:
            return []

        scored = []
        for q in candidates:
            priority = calculate_priority(q, progress_map)
            scored.append((priority, q))

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [q for _, q in scored[:count]]

        # 今日すでに解いた問題が多くて足りない場合の保険。
        if len(selected) < count:
            used_ids = {q["id"] for q in selected}
            remaining = [q for q in candidates if q["id"] not in used_ids]
            random.shuffle(remaining)
            selected.extend(remaining[: count - len(selected)])

        return shuffle_choices(selected[:count])

    candidates = filter_questions(questions, grade=grade, category=category)
    random.shuffle(candidates)
    return shuffle_choices(candidates[:count])
