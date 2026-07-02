from pathlib import Path
from datetime import datetime
from uuid import uuid4
import json
import shutil
import zipfile
import tempfile
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.utils import secure_filename

from database import (
    init_db,
    save_study_session,
    update_question_progress,
    get_progress_map,
    get_parent_summary,
    get_recent_sessions,
    get_weak_point_summary,
    get_wrong_question_ids,
)
from engine import (
    load_questions,
    build_grade_category_map,
    select_questions,
    normalize_import_questions,
    append_questions_to_csv,
    append_questions_to_csv_skip_duplicates,
)

app = Flask(__name__)
app.secret_key = "smart-drill-dev-secret"

BASE_DIR = Path(__file__).resolve().parent
QUESTIONS_CSV = BASE_DIR / "data" / "questions.csv"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"

CHILDREN = ["長女", "次女"]
QUIZ_SIZE = 10
IMPORT_CACHE = {}


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}


def _media_type_for_suffix(suffix):
    suffix = suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return ""


def save_uploaded_media(files):
    """Save uploaded image/audio files and return a map from original name to stored path.

    Stored paths are relative to static/uploads, for example:
    - images/20260629_abcd_map.png
    - audio/20260629_abcd_listening.mp3
    """
    media_map = {}
    for file in files:
        if not file or not file.filename:
            continue

        original_name = secure_filename(file.filename)
        suffix = Path(original_name).suffix.lower()
        media_type = _media_type_for_suffix(suffix)
        if not media_type:
            continue

        subdir = "images" if media_type == "image" else "audio"
        target_dir = UPLOAD_DIR / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        stored_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}_{original_name}"
        target_path = target_dir / stored_name
        file.save(target_path)

        media_map[file.filename] = f"{subdir}/{stored_name}"
        media_map[original_name] = f"{subdir}/{stored_name}"
    return media_map


def apply_media_upload_map(questions, media_map):
    """Replace media_file values with saved upload paths when filenames match."""
    for q in questions:
        media_file = (q.get("media_file") or q.get("image") or "").strip()
        if media_file in media_map:
            q["media_file"] = media_map[media_file]
        elif Path(media_file).name in media_map:
            q["media_file"] = media_map[Path(media_file).name]

        if not q.get("media_type") and q.get("media_file"):
            suffix = Path(q["media_file"]).suffix.lower()
            q["media_type"] = _media_type_for_suffix(suffix)
    return questions





def _safe_extract_zip(zip_file, target_dir):
    """Extract a zip safely, rejecting paths that escape the target directory."""
    target_dir = Path(target_dir).resolve()
    for member in zip_file.infolist():
        member_path = (target_dir / member.filename).resolve()
        if not str(member_path).startswith(str(target_dir)):
            raise ValueError("ZIP内に不正なパスが含まれています。")
    zip_file.extractall(target_dir)


def _find_package_file(extract_dir, filename):
    """Find package files even if the ZIP contains one top-level folder."""
    extract_dir = Path(extract_dir)
    direct = extract_dir / filename
    if direct.exists():
        return direct
    matches = list(extract_dir.glob(f"*/{filename}"))
    return matches[0] if matches else None


def _count_media_by_type(questions):
    counts = {"images": 0, "audio": 0}
    seen = set()
    for q in questions:
        media_type = str(q.get("media_type") or "").strip().lower()
        media_file = str(q.get("media_file") or q.get("image") or "").strip()
        if not media_file:
            continue
        media_key = (media_type, media_file)
        if media_key in seen:
            continue
        seen.add(media_key)
        if media_type == "image":
            counts["images"] += 1
        elif media_type == "audio":
            counts["audio"] += 1
    return counts


def _build_package_summary(package_meta, question_count, media_counts):
    return {
        "title": package_meta.get("title") or package_meta.get("タイトル") or "",
        "subject": package_meta.get("subject") or package_meta.get("教科") or "",
        "grade": package_meta.get("grade") or package_meta.get("学年") or "",
        "source": package_meta.get("source") or package_meta.get("出典") or "",
        "questions_count": question_count,
        "images_count": media_counts.get("images", 0),
        "audio_count": media_counts.get("audio", 0),
    }


def _copy_sdp_media(extract_dir, package_questions):
    """Copy media referenced by questions.json to static/uploads and return updated questions."""
    extract_dir = Path(extract_dir)
    extract_root = extract_dir.resolve()
    updated = []
    copied_counts = {"images": 0, "audio": 0}
    copy_errors = []
    copied_media = {}
    for q in package_questions:
        item = dict(q)
        media = item.get("media") or {}
        if not isinstance(media, dict):
            media = {}

        media_file = str(media.get("file") or item.get("media_file") or "").strip()
        media_type = str(media.get("type") or item.get("media_type") or "").strip().lower()

        if media_file:
            source_path = (extract_dir / media_file).resolve()
            media_key = str(source_path)
            if not str(source_path).startswith(str(extract_root)) or not source_path.exists():
                copy_errors.append(f"メディアファイルが見つかりません: {media_file}")
            elif media_key in copied_media:
                media_type, media_file = copied_media[media_key]
            else:
                if not media_type:
                    media_type = _media_type_for_suffix(source_path.suffix)
                if media_type in {"image", "audio"}:
                    subdir = "images" if media_type == "image" else "audio"
                    target_dir = UPLOAD_DIR / subdir
                    target_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = secure_filename(source_path.name)
                    stored_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}_{safe_name}"
                    target_path = target_dir / stored_name
                    shutil.copy2(source_path, target_path)
                    media_file = f"{subdir}/{stored_name}"
                    item["_sdp_copied_media_type"] = media_type
                    copied_media[media_key] = (media_type, media_file)
                    copied_counts[subdir] += 1
                else:
                    copy_errors.append(f"未対応のメディア種別です: {media_type or source_path.suffix}")

        if media_file:
            item["media"] = {"type": media_type, "file": media_file}
        updated.append(item)
    return updated, copied_counts, copy_errors


def load_sdp_package(file_storage):
    """Load a Smart Drill Package (.sdp/.zip) and return (data, errors)."""
    if not file_storage or not file_storage.filename:
        return None, ["SDPファイルを選択してください。"]

    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".sdp", ".zip"}:
        return None, ["拡張子は .sdp または .zip にしてください。"]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            package_path = tmp_path / filename
            file_storage.save(package_path)
            with zipfile.ZipFile(package_path, "r") as zf:
                _safe_extract_zip(zf, tmp_path / "extract")

            extract_dir = tmp_path / "extract"
            questions_path = _find_package_file(extract_dir, "questions.json")
            package_meta_path = _find_package_file(extract_dir, "package.json")
            if not questions_path:
                return None, ["questions.json読み込み: SDP内に questions.json がありません。"]

            try:
                package_meta = {}
                if package_meta_path:
                    with package_meta_path.open("r", encoding="utf-8-sig") as f:
                        package_meta = json.load(f)
            except json.JSONDecodeError as e:
                return None, [f"package.json読み込み: JSONの形式が正しくありません: {e}"]
            except Exception as e:
                return None, [f"package.json読み込み: 読み込みに失敗しました: {e}"]

            try:
                with questions_path.open("r", encoding="utf-8-sig") as f:
                    questions_data = json.load(f)
            except json.JSONDecodeError as e:
                return None, [f"questions.json読み込み: JSONの形式が正しくありません: {e}"]
            except Exception as e:
                return None, [f"questions.json読み込み: 読み込みに失敗しました: {e}"]

            if not isinstance(package_meta, dict):
                return None, ["package.json読み込み: package.json はオブジェクト形式にしてください。"]

            if isinstance(questions_data, dict):
                question_items = questions_data.get("questions", [])
            else:
                question_items = questions_data
            if not isinstance(question_items, list):
                return None, ["questions.json読み込み: questions.json は配列、または {\"questions\": [...]} にしてください。"]

            try:
                # If the zip has one top-level folder, media paths should resolve from that folder.
                media_root = questions_path.parent
                question_items, media_counts, copy_errors = _copy_sdp_media(media_root, question_items)
            except Exception as e:
                return None, [f"mediaコピー: SDP内メディアのコピーに失敗しました: {e}"]
            if copy_errors:
                return None, [f"mediaコピー: {error}" for error in copy_errors]

            package_summary = _build_package_summary(package_meta, len(question_items), media_counts)
            return {
                "package": package_meta,
                "package_summary": package_summary,
                "questions": question_items,
                "media_counts": media_counts,
            }, []
    except zipfile.BadZipFile:
        return None, ["SDPをZIPとして読み込めませんでした。ファイルが壊れている可能性があります。"]
    except json.JSONDecodeError as e:
        return None, [f"JSONの形式が正しくありません: {e}"]
    except Exception as e:
        return None, [f"SDPの読み込み中にエラーが発生しました: {e}"]

def start_quiz(child, questions, mode="practice"):
    """Store selected questions in session and move to quiz screen."""
    selected = questions[:QUIZ_SIZE]

    session["child"] = child
    session["quiz"] = selected
    session["current_index"] = 0
    session["answers"] = []
    session["started_at"] = datetime.now().isoformat(timespec="seconds")
    session["quiz_mode"] = mode
    session.pop("saved_session_id", None)

    return redirect(url_for("quiz"))


@app.route("/")
def index():
    questions = load_questions(QUESTIONS_CSV)
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


@app.route("/today/start", methods=["POST"])
def start_today():
    child = request.form.get("child", "長女")

    all_questions = load_questions(QUESTIONS_CSV)
    progress_map = get_progress_map(child)
    questions = select_questions(
        all_questions,
        child=child,
        mode="today",
        count=QUIZ_SIZE,
        progress_map=progress_map,
    )

    if len(questions) == 0:
        session["error_message"] = "今日のおすすめに出せる問題がまだありません。問題データを確認してください。"
        session["selected_child"] = child
        return redirect(url_for("index"))

    return start_quiz(child, questions, mode="today")


@app.route("/start", methods=["POST"])
def start():
    child = request.form.get("child", "長女")
    grade = request.form.get("grade", "all")
    category = request.form.get("category", "all")

    all_questions = load_questions(QUESTIONS_CSV)
    questions = select_questions(
        all_questions,
        child=child,
        grade=grade,
        category=category,
        mode="practice",
        count=QUIZ_SIZE,
    )

    if len(questions) == 0:
        session["error_message"] = "この条件の問題はまだありません。別の学年・単元を選んでください。"
        session["selected_child"] = child
        session["selected_grade"] = grade
        session["selected_category"] = category
        return redirect(url_for("index"))

    return start_quiz(child, questions, mode="practice")


@app.route("/review/start", methods=["POST"])
def start_review():
    child = request.form.get("child", "長女")
    wrong_ids = get_wrong_question_ids(child, limit=QUIZ_SIZE)

    questions = load_questions(QUESTIONS_CSV)
    selected = select_questions(
        questions,
        child=child,
        mode="review",
        count=QUIZ_SIZE,
        fixed_question_ids=wrong_ids,
    )

    if len(selected) == 0:
        session["parent_message"] = f"{child}さんの復習対象はまだありません。まずはドリルで間違えた問題を作ってください。"
        return redirect(url_for("parent"))

    return start_quiz(child, selected, mode="review")


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
        update_question_progress(
            child=session.get("child", ""),
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



def build_import_preview(questions):
    """Add validation details used only by the import preview screen."""
    preview = []
    for idx, q in enumerate(questions):
        item = dict(q)
        warnings = []
        warnings.extend(item.get("_warnings", []))
        if not item.get("hint1"):
            warnings.append("ヒント1がありません。")
        if not item.get("hint2"):
            warnings.append("ヒント2がありません。")
        if not item.get("explanation"):
            warnings.append("解説がありません。")
        if not item.get("importance"):
            warnings.append("重要度がありません。")
        if not item.get("difficulty"):
            warnings.append("難易度がありません。")
        if item.get("media_type") and not item.get("media_file"):
            warnings.append("メディア種別がありますが、ファイル指定がありません。")
        if item.get("media_file") and not item.get("media_type"):
            warnings.append("メディアファイルがありますが、種別が判定できません。")
        item["preview_index"] = idx
        item["warnings"] = warnings
        item["status"] = "warning" if warnings else "ok"
        preview.append(item)
    return preview


def clean_import_questions(questions):
    """Remove preview-only keys before saving imported questions."""
    cleaned = []
    for q in questions:
        row = dict(q)
        row.pop("preview_index", None)
        row.pop("warnings", None)
        row.pop("status", None)
        row.pop("_warnings", None)
        row.pop("_sdp_copied_media_type", None)
        cleaned.append(row)
    return cleaned


def _get_import_state():
    token = session.get("import_token")
    if not token:
        return {}
    return IMPORT_CACHE.get(token, {})


def _set_import_state(**values):
    token = uuid4().hex
    IMPORT_CACHE[token] = values
    session["import_token"] = token


def _clear_import_state():
    token = session.pop("import_token", None)
    if token:
        IMPORT_CACHE.pop(token, None)
    session.pop("import_result", None)


@app.route("/admin/import", methods=["GET"])
def import_questions_page():
    message = session.pop("import_message", "")
    errors = session.pop("import_errors", [])
    import_result = session.pop("import_result", None)
    import_state = _get_import_state()
    preview = import_state.get("preview", [])
    raw_json = import_state.get("raw_json", "")
    package_summary = import_state.get("package_summary")
    source_type = import_state.get("source_type", "")
    return render_template(
        "import_questions.html",
        message=message,
        errors=errors,
        import_result=import_result,
        preview=preview,
        raw_json=raw_json,
        package_summary=package_summary,
        source_type=source_type,
    )



@app.route("/admin/import/sdp", methods=["POST"])
def import_sdp_preview():
    data, load_errors = load_sdp_package(request.files.get("sdp_file"))
    if load_errors:
        _clear_import_state()
        session["import_errors"] = load_errors
        return redirect(url_for("import_questions_page"))

    preview, errors = normalize_import_questions(data)
    preview = build_import_preview(preview)

    package_meta = data.get("package", {}) if isinstance(data, dict) else {}
    package_title = package_meta.get("title") or package_meta.get("タイトル") or "SDP"

    _set_import_state(
        raw_json=json.dumps(data, ensure_ascii=False, indent=2),
        preview=preview,
        package_summary=data.get("package_summary", {}),
        media_counts=data.get("media_counts", {"images": 0, "audio": 0}),
        source_type="sdp",
    )
    session.pop("import_result", None)
    session["import_errors"] = [f"questions.json読み込み: {error}" for error in errors]

    if preview and not errors:
        warning_count = sum(1 for q in preview if q.get("warnings"))
        if warning_count:
            session["import_message"] = f"SDP読み込み成功: {package_title}: {len(preview)}問を確認できます（注意あり {warning_count}問）。登録する問題を選んでください。"
        else:
            session["import_message"] = f"SDP読み込み成功: {package_title}: {len(preview)}問を確認できます。登録する問題を選んでください。"
    return redirect(url_for("import_questions_page"))


@app.route("/admin/import/preview", methods=["POST"])
def import_questions_preview():
    raw_json = request.form.get("questions_json", "").strip()
    if not raw_json:
        _clear_import_state()
        session["import_errors"] = ["JSONを貼り付けてください。"]
        return redirect(url_for("import_questions_page"))

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        _clear_import_state()
        session["import_errors"] = [f"JSONの形式が正しくありません: {e}"]
        _set_import_state(raw_json=raw_json, preview=[], source_type="json")
        return redirect(url_for("import_questions_page"))

    preview, errors = normalize_import_questions(data)
    media_map = save_uploaded_media(request.files.getlist("media_files"))
    preview = apply_media_upload_map(preview, media_map)
    preview = build_import_preview(preview)

    _set_import_state(
        raw_json=raw_json,
        preview=preview,
        package_summary=None,
        media_counts=_count_media_by_type(preview),
        source_type="json",
    )
    session["import_errors"] = errors
    session.pop("import_result", None)

    if preview and not errors:
        warning_count = sum(1 for q in preview if q.get("warnings"))
        media_text = f" / メディア{len(media_map)}件保存" if media_map else ""
        if warning_count:
            session["import_message"] = f"{len(preview)}問を確認できます（注意あり {warning_count}問{media_text}）。登録する問題を選んでください。"
        else:
            session["import_message"] = f"{len(preview)}問を確認できます{media_text}。登録する問題を選んでください。"
    return redirect(url_for("import_questions_page"))


@app.route("/admin/import/save", methods=["POST"])
def import_questions_save():
    import_state = _get_import_state()
    preview_from_session = import_state.get("preview", [])
    if not preview_from_session:
        session["import_errors"] = ["CSV登録: 登録するプレビューがありません。もう一度プレビューしてください。"]
        return redirect(url_for("import_questions_page"))

    selected_indexes = request.form.getlist("include")
    if not selected_indexes:
        session["import_errors"] = ["CSV登録: 登録する問題を1問以上選んでください。"]
        return redirect(url_for("import_questions_page"))

    selected = []
    for value in selected_indexes:
        try:
            idx = int(value)
        except ValueError:
            continue
        if 0 <= idx < len(preview_from_session):
            selected.append(preview_from_session[idx])

    if not selected:
        session["import_errors"] = ["CSV登録: 選択された問題を読み取れませんでした。もう一度プレビューしてください。"]
        return redirect(url_for("import_questions_page"))

    questions = clean_import_questions(selected)
    try:
        if import_state.get("source_type") == "sdp":
            count, duplicate_skipped = append_questions_to_csv_skip_duplicates(QUESTIONS_CSV, questions)
        else:
            count = append_questions_to_csv(QUESTIONS_CSV, questions)
            duplicate_skipped = 0
    except Exception as e:
        session["import_errors"] = [f"CSV登録: questions.csv への登録に失敗しました: {e}"]
        return redirect(url_for("import_questions_page"))

    skipped = len(preview_from_session) - len(selected)
    media_counts = _count_media_by_type(selected)
    session["import_result"] = {
        "registered": count,
        "skipped": skipped,
        "duplicate_skipped": duplicate_skipped,
        "images": media_counts.get("images", 0),
        "audio": media_counts.get("audio", 0),
    }
    token = session.pop("import_token", None)
    if token:
        IMPORT_CACHE.pop(token, None)
    session["import_message"] = f"{count}問を登録しました。重複のため{duplicate_skipped}問をスキップしました。"
    return redirect(url_for("import_questions_page"))


@app.route("/parent")
def parent():
    summaries = get_parent_summary()
    recent_sessions = get_recent_sessions(limit=20)
    weak_points = get_weak_point_summary()
    parent_message = session.pop("parent_message", "")
    return render_template(
        "parent.html",
        summaries=summaries,
        recent_sessions=recent_sessions,
        weak_points=weak_points,
        parent_message=parent_message,
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
