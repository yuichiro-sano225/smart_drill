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


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _question_importance(question):
    """Return question importance from optional CSV columns.

    Supported columns:
    - importance
    - 重要度

    If the column is missing, use 3 as a neutral default.
    """
    return _safe_int(question.get("importance") or question.get("重要度"), 3)


def _is_due(progress):
    if not progress:
        return False
    next_review = progress.get("next_review")
    return bool(next_review and next_review <= date.today().isoformat())


def _is_weak(progress):
    if not progress:
        return False

    last_result = progress.get("last_result")
    if last_result in {"wrong", "hint"}:
        return True

    correct_count = _safe_int(progress.get("correct_count"))
    wrong_count = _safe_int(progress.get("wrong_count"))
    total = correct_count + wrong_count
    if total >= 2 and correct_count / total < 0.7:
        return True

    memory_level = _safe_int(progress.get("memory_level"))
    return memory_level <= 1


def calculate_priority(question, progress):
    """
    Calculate priority for today's recommended questions.

    Higher score = more likely to be selected.
    v2.3 adds three ideas:
    - due review questions are strongly prioritized
    - weak questions are prioritized
    - optional question importance can raise priority
    """
    score = 0
    qid = int(question["id"])
    p = progress.get(qid)

    # 少しだけランダム性を入れて、毎日完全に同じ問題になるのを避ける。
    score += random.uniform(0, 15)

    # CSVに importance または 重要度 があれば使う。無ければ3。
    importance = _question_importance(question)
    score += max(0, min(5, importance)) * 6

    if not p:
        # まだ解いていない問題も必ず混ぜたいので、一定の優先度を持たせる。
        score += 35
        return score

    today = date.today().isoformat()
    next_review = p.get("next_review")
    last_result = p.get("last_result")
    correct_count = _safe_int(p.get("correct_count"))
    wrong_count = _safe_int(p.get("wrong_count"))
    hint_count = _safe_int(p.get("hint_count"))
    last_hint_count = _safe_int(p.get("last_hint_count"))
    memory_level = _safe_int(p.get("memory_level"))

    if next_review and next_review <= today:
        score += 130

    if last_result == "wrong":
        score += 95

    if last_result == "hint" or last_hint_count > 0:
        score += 65

    if hint_count > 0:
        score += min(30, hint_count * 5)

    total = correct_count + wrong_count
    if total > 0:
        accuracy = correct_count / total
        if accuracy < 0.5:
            score += 45
        elif accuracy < 0.7:
            score += 25

    # 記憶レベルが低いものを優先。
    score += max(0, 5 - memory_level) * 8

    days_since = _days_since(p.get("last_answered"))
    if days_since is not None:
        if days_since == 0:
            # 今日すでに解いた問題は、原則として出さない。
            score -= 1000
        elif days_since == 1 and last_result == "correct":
            score -= 40
        elif days_since >= 30:
            score += 30

    return score


def _take_from_bucket(scored_items, used_ids, limit):
    """Take high-priority questions from a scored bucket without duplicates."""
    if limit <= 0:
        return []

    available = [(score, q) for score, q in scored_items if q["id"] not in used_ids]
    available.sort(key=lambda item: item[0], reverse=True)
    picked = [q for _, q in available[:limit]]
    used_ids.update(q["id"] for q in picked)
    return picked


def _select_today_questions(candidates, progress_map, count):
    """Select today's recommended questions with a balanced mix.

    Target mix for 10 questions:
    - 4 due review questions
    - 3 weak questions
    - 2 new questions
    - 1 important question

    If a bucket is short, fill the remaining slots by overall priority.
    """
    scored = [(calculate_priority(q, progress_map), q) for q in candidates]

    due_bucket = []
    weak_bucket = []
    new_bucket = []
    important_bucket = []

    for score, q in scored:
        qid = int(q["id"])
        p = progress_map.get(qid)
        if _is_due(p):
            due_bucket.append((score, q))
        if _is_weak(p):
            weak_bucket.append((score, q))
        if not p:
            new_bucket.append((score, q))
        if _question_importance(q) >= 4:
            important_bucket.append((score, q))

    # countが10以外でもだいたい同じ比率になるようにする。
    due_quota = max(1, round(count * 0.4))
    weak_quota = max(1, round(count * 0.3))
    new_quota = max(1, round(count * 0.2))
    important_quota = max(0, count - due_quota - weak_quota - new_quota)

    selected = []
    used_ids = set()

    for bucket, quota in [
        (due_bucket, due_quota),
        (weak_bucket, weak_quota),
        (new_bucket, new_quota),
        (important_bucket, important_quota),
    ]:
        picked = _take_from_bucket(bucket, used_ids, quota)
        selected.extend(picked)

    if len(selected) < count:
        selected.extend(_take_from_bucket(scored, used_ids, count - len(selected)))

    return selected[:count]


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
