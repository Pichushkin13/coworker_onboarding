import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "training.db"

SESSION_TTL_SECONDS = 6 * 60 * 60
ADMIN_SESSIONS = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(v)


def safe_id(value: str, prefix: str) -> str:
    base = "_".join("".join(c.lower() if c.isalnum() else " " for c in str(value or "")).split())[:40]
    if not base:
        base = uuid.uuid4().hex[:8]
    return f"{prefix}_{base}_{uuid.uuid4().hex[:6]}"


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.executescript(
        """
CREATE TABLE IF NOT EXISTS courses (
  courseId TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  category TEXT DEFAULT '',
  level TEXT DEFAULT 'Beginner',
  displayOrder INTEGER DEFAULT 1,
  passingScore INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS modules (
  moduleId TEXT PRIMARY KEY,
  courseId TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  displayOrder INTEGER DEFAULT 1,
  status TEXT DEFAULT 'active',
  moduleType TEXT DEFAULT 'learning',
  maxAttempts INTEGER,
  passingScore INTEGER,
  reviewMode TEXT,
  lockAfterSubmit TEXT,
  isTimed TEXT,
  timeLimitMinutes INTEGER
);
CREATE TABLE IF NOT EXISTS activities (
  activityId TEXT PRIMARY KEY,
  courseId TEXT NOT NULL,
  moduleId TEXT NOT NULL,
  activityType TEXT NOT NULL,
  title TEXT DEFAULT '',
  content TEXT DEFAULT '',
  configJson TEXT DEFAULT '{}',
  displayOrder INTEGER DEFAULT 1,
  status TEXT DEFAULT 'active',
  validationJson TEXT DEFAULT '{}',
  points INTEGER,
  manualReviewRequired TEXT
);
CREATE TABLE IF NOT EXISTS sqlSchemas (
  schemaId TEXT PRIMARY KEY,
  title TEXT,
  description TEXT,
  initSql TEXT,
  status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS learningEvents (
  eventId TEXT PRIMARY KEY,
  userEmail TEXT,
  courseId TEXT,
  activityId TEXT,
  activityType TEXT,
  eventType TEXT,
  answerJson TEXT,
  isCorrect TEXT,
  feedback TEXT,
  createdAt TEXT
);
CREATE TABLE IF NOT EXISTS assessmentAttempts (
  attemptId TEXT PRIMARY KEY,
  userEmail TEXT,
  courseId TEXT,
  assessmentBlockId TEXT,
  attemptNo INTEGER,
  totalScore INTEGER,
  maxScore INTEGER,
  scorePercent INTEGER,
  resultStatus TEXT,
  submittedAt TEXT,
  lockedAfterSubmit TEXT,
  submissionReason TEXT,
  sessionId TEXT
);
CREATE TABLE IF NOT EXISTS assessmentTaskAttempts (
  taskAttemptId TEXT PRIMARY KEY,
  attemptId TEXT,
  userEmail TEXT,
  assessmentBlockId TEXT,
  assessmentTaskId TEXT,
  taskType TEXT,
  answerJson TEXT,
  interpreterStatus TEXT,
  interpreterOutput TEXT,
  validationStatus TEXT,
  validationOutput TEXT,
  score INTEGER,
  manualReviewRequired TEXT
);
CREATE TABLE IF NOT EXISTS assessmentAttemptOverrides (
  overrideId TEXT PRIMARY KEY,
  userEmail TEXT,
  courseId TEXT,
  assessmentBlockId TEXT,
  maxAttemptsOverride INTEGER,
  reason TEXT,
  createdBy TEXT,
  createdAt TEXT,
  status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS assessmentAttemptSessions (
  sessionId TEXT PRIMARY KEY,
  userEmail TEXT,
  courseId TEXT,
  moduleId TEXT,
  attemptNo INTEGER,
  startedAt TEXT,
  expiresAt TEXT,
  status TEXT,
  submittedAt TEXT,
  submissionReason TEXT
);
CREATE TABLE IF NOT EXISTS adminUsers (
  username TEXT PRIMARY KEY,
  password TEXT,
  role TEXT,
  status TEXT,
  createdAt TEXT
);
        """
    )
    cur.execute("SELECT username FROM adminUsers WHERE username='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO adminUsers(username,password,role,status,createdAt) VALUES(?,?,?,?,?)",
            ("admin", "admin", "admin", "active", now_iso()),
        )
    conn.commit()
    conn.close()


def rows(conn, query, params=()):
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def active_rows(items):
    return [x for x in items if str(x.get("status", "")).lower() == "active"]


def get_effective_max_attempts(module, overrides):
    m = int(module.get("maxAttempts") or 1)
    for o in overrides:
        m = max(m, int(o.get("maxAttemptsOverride") or m))
    return m


def session_response(session):
    return {
        "status": session.get("status", "active"),
        "sessionId": session["sessionId"],
        "startedAt": session["startedAt"],
        "expiresAt": session["expiresAt"],
        "attemptNo": int(session.get("attemptNo") or 1),
    }


def require_admin(payload):
    token = (payload or {}).get("token")
    if not token or token not in ADMIN_SESSIONS:
        raise ValueError("Admin session expired. Sign in again.")
    if ADMIN_SESSIONS[token]["expires"] < datetime.now(timezone.utc):
        ADMIN_SESSIONS.pop(token, None)
        raise ValueError("Admin session expired. Sign in again.")


def required(payload, field, label):
    value = str((payload or {}).get(field, "")).strip()
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def json_text(value):
    if isinstance(value, str):
        json.loads(value or "{}")
        return value or "{}"
    return json.dumps(value or {}, ensure_ascii=False)


def next_order(conn, table, where="", params=()):
    query = f"SELECT COALESCE(MAX(displayOrder),0) FROM {table}"
    if where:
        query += f" WHERE {where}"
    return int(conn.execute(query, params).fetchone()[0] or 0) + 1


def normalize_activity_orders(conn, module_id):
    items = rows(
        conn,
        "SELECT activityId FROM activities WHERE moduleId=? AND status='active' ORDER BY displayOrder",
        (module_id,),
    )
    for index, item in enumerate(items, start=1):
        conn.execute("UPDATE activities SET displayOrder=? WHERE activityId=?", (index, item["activityId"]))


def activity_insert_order(conn, module_id, payload):
    items = rows(
        conn,
        "SELECT activityId FROM activities WHERE moduleId=? AND status='active' ORDER BY displayOrder",
        (module_id,),
    )
    ordered = [item["activityId"] for item in items]
    position = len(ordered)
    before_id = payload.get("insertBeforeActivityId")
    after_id = payload.get("insertAfterActivityId")
    if before_id and before_id in ordered:
        position = ordered.index(before_id)
    elif after_id and after_id in ordered:
        position = ordered.index(after_id) + 1
    for index, activity_id in enumerate(ordered[position:], start=position + 2):
        conn.execute("UPDATE activities SET displayOrder=? WHERE activityId=?", (index, activity_id))
    return position + 1


def validate_activity_type(module, activity_type):
    learning = {"html_content", "text", "content", "image", "practice_quiz", "drag_mapping", "drag_order", "sql_practice", "python_practice"}
    assessment = {"quiz", "sql_task", "python_task", "open_answer"}
    allowed = assessment if module["moduleType"] == "assessment" else learning
    if activity_type not in allowed:
        raise ValueError(f"Activity type {activity_type} is not allowed for module type {module['moduleType']}.")


def module_payload(payload, current_order):
    module_type = "assessment" if payload.get("moduleType") == "assessment" else "learning"
    timed = to_bool(payload.get("isTimed"))
    return {
        "courseId": required(payload, "courseId", "Course"),
        "title": required(payload, "title", "Module title"),
        "description": payload.get("description", ""),
        "displayOrder": int(payload.get("displayOrder") or current_order or 1),
        "status": payload.get("status", "active"),
        "moduleType": module_type,
        "maxAttempts": int(payload.get("maxAttempts") or 1) if module_type == "assessment" else None,
        "passingScore": int(payload.get("passingScore") or 0) if module_type == "assessment" else None,
        "reviewMode": payload.get("reviewMode", "mixed") if module_type == "assessment" else None,
        "lockAfterSubmit": str(payload.get("lockAfterSubmit", True)) if module_type == "assessment" else None,
        "isTimed": str(timed) if module_type == "assessment" else None,
        "timeLimitMinutes": int(payload.get("timeLimitMinutes") or 1) if module_type == "assessment" and timed else None,
    }


def activity_payload(payload, module, current_order):
    activity_type = required(payload, "activityType", "Activity type")
    validate_activity_type(module, activity_type)
    return {
        "courseId": module["courseId"],
        "moduleId": module["moduleId"],
        "activityType": activity_type,
        "title": payload.get("title", ""),
        "content": payload.get("content", ""),
        "configJson": json_text(payload.get("configJson", "{}")),
        "displayOrder": int(payload.get("displayOrder") or current_order or 1),
        "status": payload.get("status", "active"),
        "validationJson": json_text(payload.get("validationJson", "{}")),
        "points": int(payload.get("points") or 0) if module["moduleType"] == "assessment" else None,
        "manualReviewRequired": str(to_bool(payload.get("manualReviewRequired"))) if module["moduleType"] == "assessment" else None,
    }


def finalize_expired_sessions(conn, user_email, modules, activities):
    now = datetime.now(timezone.utc)
    sessions = rows(
        conn,
        "SELECT * FROM assessmentAttemptSessions WHERE userEmail=? AND status='active'",
        (user_email,),
    )
    for s in sessions:
        if datetime.fromisoformat(s["expiresAt"]) > now:
            continue
        module = next((m for m in modules if m["moduleId"] == s["moduleId"] and m["moduleType"] == "assessment"), None)
        if not module:
            continue
        already = conn.execute("SELECT 1 FROM assessmentAttempts WHERE sessionId=?", (s["sessionId"],)).fetchone()
        if already:
            conn.execute("UPDATE assessmentAttemptSessions SET status='submitted' WHERE sessionId=?", (s["sessionId"],))
            continue
        tasks = sorted([a for a in activities if a["moduleId"] == module["moduleId"]], key=lambda x: int(x.get("displayOrder") or 0))
        overrides = active_rows(rows(conn, "SELECT * FROM assessmentAttemptOverrides WHERE userEmail=? AND assessmentBlockId=?", (user_email, module["moduleId"])))
        max_attempts = get_effective_max_attempts(module, overrides)
        complete_assessment_attempt(conn, module, tasks, user_email, int(s.get("attemptNo") or 1), [], "time_expired", s, max_attempts)
    conn.commit()


def complete_assessment_attempt(conn, module, tasks, user_email, attempt_no, task_results, reason, session, max_attempts):
    results = {r.get("activityId"): r for r in (task_results or [])}
    score = 0
    max_score = 0
    pending_review = False
    failed = False

    for task in tasks:
        result = results.get(task["activityId"], {"validationStatus": "not_checked", "score": 0, "answer": {}})
        score += int(result.get("score") or 0)
        max_score += int(task.get("points") or 0)
        if to_bool(task.get("manualReviewRequired")) or result.get("validationStatus") == "pending_review":
            pending_review = True
        if result.get("validationStatus") in ("failed", "error", "not_checked"):
            failed = True

    percent = round(score * 100 / max_score) if max_score else 0
    if pending_review:
        status = "pending_review"
    else:
        status = "passed" if (not failed and percent >= int(module.get("passingScore") or 0)) else "failed"

    attempt_id = "att_" + uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO assessmentAttempts(attemptId,userEmail,courseId,assessmentBlockId,attemptNo,totalScore,maxScore,scorePercent,resultStatus,submittedAt,lockedAfterSubmit,submissionReason,sessionId)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (attempt_id, user_email, module["courseId"], module["moduleId"], attempt_no, score, max_score, percent, status, now_iso(), str(module.get("lockAfterSubmit")), reason, session["sessionId"] if session else ""),
    )

    for task in tasks:
        result = results.get(task["activityId"], {"validationStatus": "not_checked", "score": 0, "answer": {}})
        conn.execute(
            """
            INSERT INTO assessmentTaskAttempts(taskAttemptId,attemptId,userEmail,assessmentBlockId,assessmentTaskId,taskType,answerJson,interpreterStatus,interpreterOutput,validationStatus,validationOutput,score,manualReviewRequired)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "tatt_" + uuid.uuid4().hex,
                attempt_id,
                user_email,
                module["moduleId"],
                task["activityId"],
                task.get("activityType", ""),
                json.dumps(result.get("answer") or {}),
                result.get("interpreterStatus", ""),
                result.get("interpreterOutput", ""),
                result.get("validationStatus", ""),
                result.get("validationOutput", ""),
                int(result.get("score") or 0),
                str(task.get("manualReviewRequired")),
            ),
        )

    if session:
        conn.execute(
            "UPDATE assessmentAttemptSessions SET status='submitted', submittedAt=?, submissionReason=? WHERE sessionId=?",
            (now_iso(), reason, session["sessionId"]),
        )

    conn.commit()
    return {
        "status": "submitted",
        "attemptNo": attempt_no,
        "effectiveMaxAttempts": max_attempts,
        "totalScore": score,
        "maxScore": max_score,
        "scorePercent": percent,
        "resultStatus": status,
        "submissionReason": reason,
    }


def build_assessment_progress(modules, attempts, overrides, sessions):
    result = []
    for module in modules:
        if module.get("moduleType") != "assessment":
            continue
        m_attempts = [a for a in attempts if a.get("assessmentBlockId") == module["moduleId"]]
        m_overrides = [o for o in overrides if o.get("assessmentBlockId") == module["moduleId"]]
        max_a = get_effective_max_attempts(module, m_overrides)
        last = m_attempts[-1] if m_attempts else None
        active_session = next((s for s in sessions if s.get("moduleId") == module["moduleId"] and s.get("status") == "active"), None)
        result.append({
            "moduleId": module["moduleId"],
            "attemptsUsed": len(m_attempts),
            "effectiveMaxAttempts": max_a,
            "remainingAttempts": max(max_a - len(m_attempts), 0),
            "canSubmit": len(m_attempts) < max_a,
            "canStart": len(m_attempts) < max_a and not active_session,
            "isTimed": to_bool(module.get("isTimed")),
            "timeLimitMinutes": int(module.get("timeLimitMinutes") or 0),
            "activeAttempt": session_response(active_session) if active_session else None,
            "lastResultStatus": last.get("resultStatus") if last else "",
            "lastScorePercent": int(last.get("scorePercent") or 0) if last else 0,
        })
    return result


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self.api_get(parsed.path)
        return self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return json_response(self, 404, {"error": "Not found"})
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return json_response(self, 400, {"error": "Invalid JSON"})
        return self.api_post(parsed.path, payload)

    def serve_static(self, path):
        if path in ("/", ""):
            file_path = STATIC_DIR / "index.html"
        else:
            file_path = (STATIC_DIR / path.lstrip("/")).resolve()
            if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
                self.send_error(403)
                return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        mime = "text/plain; charset=utf-8"
        if file_path.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            mime = "application/javascript; charset=utf-8"
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def api_get(self, path):
        if path != "/api/app-data":
            return json_response(self, 404, {"error": "Not found"})
        user_email = self.headers.get("X-User-Email", "demo.user@example.com")
        conn = db_conn()
        try:
            courses = sorted(active_rows(rows(conn, "SELECT * FROM courses")), key=lambda x: int(x.get("displayOrder") or 0))
            modules = sorted(active_rows(rows(conn, "SELECT * FROM modules")), key=lambda x: int(x.get("displayOrder") or 0))
            activities = sorted(active_rows(rows(conn, "SELECT * FROM activities")), key=lambda x: int(x.get("displayOrder") or 0))
            sql_schemas = active_rows(rows(conn, "SELECT * FROM sqlSchemas"))
            finalize_expired_sessions(conn, user_email, modules, activities)
            attempts = rows(conn, "SELECT * FROM assessmentAttempts WHERE userEmail=? ORDER BY submittedAt", (user_email,))
            overrides = active_rows(rows(conn, "SELECT * FROM assessmentAttemptOverrides WHERE userEmail=?", (user_email,)))
            sessions = rows(conn, "SELECT * FROM assessmentAttemptSessions WHERE userEmail=?", (user_email,))
            return json_response(self, 200, {
                "version": "migrated-api-v1",
                "userEmail": user_email,
                "courses": courses,
                "modules": modules,
                "activities": activities,
                "sqlSchemas": sql_schemas,
                "assessmentProgress": build_assessment_progress(modules, attempts, overrides, sessions),
            })
        finally:
            conn.close()

    def api_post(self, path, payload):
        conn = db_conn()
        try:
            if path == "/api/admin/login":
                username = str(payload.get("username", "")).strip()
                password = str(payload.get("password", ""))
                user = conn.execute(
                    "SELECT * FROM adminUsers WHERE username=? AND password=? AND role='admin' AND status='active'",
                    (username, password),
                ).fetchone()
                if not user:
                    raise ValueError("Invalid admin login or password.")
                token = secrets.token_urlsafe(24)
                ADMIN_SESSIONS[token] = {"username": username, "expires": datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)}
                return json_response(self, 200, {"token": token, "username": username})

            if path == "/api/course/create":
                require_admin(payload)
                cid = safe_id(payload.get("courseId") or payload.get("title"), "course")
                exists = conn.execute("SELECT 1 FROM courses WHERE courseId=?", (cid,)).fetchone()
                if exists:
                    raise ValueError(f"Course ID already exists: {cid}")
                conn.execute(
                    "INSERT INTO courses(courseId,title,description,category,level,displayOrder,passingScore,status) VALUES(?,?,?,?,?,?,?,?)",
                    (cid, required(payload, "title", "Course title"), payload.get("description", ""), payload.get("category", ""), payload.get("level", "Beginner"), int(payload.get("displayOrder") or next_order(conn, "courses")), int(payload.get("passingScore") or 0), payload.get("status", "active")),
                )
                conn.commit()
                return json_response(self, 200, {"status": "ok", "courseId": cid})

            if path == "/api/course/update":
                require_admin(payload)
                course_id = required(payload, "courseId", "Course ID")
                current = conn.execute("SELECT * FROM courses WHERE courseId=?", (course_id,)).fetchone()
                if not current:
                    raise ValueError(f"Course not found: {course_id}")
                conn.execute(
                    "UPDATE courses SET title=?,description=?,category=?,level=?,displayOrder=?,passingScore=?,status=? WHERE courseId=?",
                    (
                        required(payload, "title", "Course title"),
                        payload.get("description", ""),
                        payload.get("category", ""),
                        payload.get("level", "Beginner"),
                        int(payload.get("displayOrder") or current["displayOrder"] or 1),
                        int(payload.get("passingScore") or 0),
                        payload.get("status", "active"),
                        course_id,
                    ),
                )
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/course/delete":
                require_admin(payload)
                course_id = required(payload, "courseId", "Course ID")
                if not conn.execute("SELECT 1 FROM courses WHERE courseId=?", (course_id,)).fetchone():
                    raise ValueError(f"Course not found: {course_id}")
                conn.execute("UPDATE courses SET status='inactive' WHERE courseId=?", (course_id,))
                conn.execute("UPDATE modules SET status='inactive' WHERE courseId=?", (course_id,))
                conn.execute("UPDATE activities SET status='inactive' WHERE courseId=?", (course_id,))
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/course/reorder":
                require_admin(payload)
                ordered = payload.get("orderedIds") if isinstance(payload.get("orderedIds"), list) else []
                active = rows(conn, "SELECT courseId FROM courses WHERE status='active'")
                allowed = {r["courseId"] for r in active}
                if set(ordered) != allowed:
                    raise ValueError("Course order does not match active courses.")
                for index, course_id in enumerate(ordered, start=1):
                    conn.execute("UPDATE courses SET displayOrder=? WHERE courseId=?", (index, course_id))
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/module/create":
                require_admin(payload)
                mid = safe_id(payload.get("moduleId") or payload.get("title"), "mod")
                values = module_payload(payload, next_order(conn, "modules", "courseId=?", (payload.get("courseId"),)))
                conn.execute(
                    "INSERT INTO modules(moduleId,courseId,title,description,displayOrder,status,moduleType,maxAttempts,passingScore,reviewMode,lockAfterSubmit,isTimed,timeLimitMinutes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        mid,
                        values["courseId"],
                        values["title"],
                        values["description"],
                        values["displayOrder"],
                        values["status"],
                        values["moduleType"],
                        values["maxAttempts"],
                        values["passingScore"],
                        values["reviewMode"],
                        values["lockAfterSubmit"],
                        values["isTimed"],
                        values["timeLimitMinutes"],
                    ),
                )
                conn.commit()
                return json_response(self, 200, {"status": "ok", "moduleId": mid})

            if path == "/api/module/update":
                require_admin(payload)
                module_id = required(payload, "moduleId", "Module ID")
                current = conn.execute("SELECT * FROM modules WHERE moduleId=?", (module_id,)).fetchone()
                if not current:
                    raise ValueError(f"Module not found: {module_id}")
                values = module_payload(payload, current["displayOrder"])
                conn.execute(
                    "UPDATE modules SET courseId=?,title=?,description=?,displayOrder=?,status=?,moduleType=?,maxAttempts=?,passingScore=?,reviewMode=?,lockAfterSubmit=?,isTimed=?,timeLimitMinutes=? WHERE moduleId=?",
                    (
                        values["courseId"],
                        values["title"],
                        values["description"],
                        values["displayOrder"],
                        values["status"],
                        values["moduleType"],
                        values["maxAttempts"],
                        values["passingScore"],
                        values["reviewMode"],
                        values["lockAfterSubmit"],
                        values["isTimed"],
                        values["timeLimitMinutes"],
                        module_id,
                    ),
                )
                conn.execute("UPDATE activities SET courseId=? WHERE moduleId=?", (values["courseId"], module_id))
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/module/delete":
                require_admin(payload)
                module_id = required(payload, "moduleId", "Module ID")
                if not conn.execute("SELECT 1 FROM modules WHERE moduleId=?", (module_id,)).fetchone():
                    raise ValueError(f"Module not found: {module_id}")
                conn.execute("UPDATE modules SET status='inactive' WHERE moduleId=?", (module_id,))
                conn.execute("UPDATE activities SET status='inactive' WHERE moduleId=?", (module_id,))
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/module/reorder":
                require_admin(payload)
                course_id = required(payload, "courseId", "Course ID")
                ordered = payload.get("orderedIds") if isinstance(payload.get("orderedIds"), list) else []
                active = rows(conn, "SELECT moduleId FROM modules WHERE courseId=? AND status='active'", (course_id,))
                allowed = {r["moduleId"] for r in active}
                if set(ordered) != allowed:
                    raise ValueError("Module order does not match active modules in the selected course.")
                for index, module_id in enumerate(ordered, start=1):
                    conn.execute("UPDATE modules SET displayOrder=? WHERE moduleId=?", (index, module_id))
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/activity/create":
                require_admin(payload)
                module = conn.execute("SELECT * FROM modules WHERE moduleId=?", (payload.get("moduleId"),)).fetchone()
                if not module:
                    raise ValueError("Module not found.")
                module = dict(module)
                aid = safe_id(payload.get("activityId") or payload.get("title") or payload.get("activityType"), "act")
                insert_order = activity_insert_order(conn, module["moduleId"], payload)
                values = activity_payload(payload, module, insert_order)
                conn.execute(
                    "INSERT INTO activities(activityId,courseId,moduleId,activityType,title,content,configJson,displayOrder,status,validationJson,points,manualReviewRequired) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        aid,
                        values["courseId"],
                        values["moduleId"],
                        values["activityType"],
                        values["title"],
                        values["content"],
                        values["configJson"],
                        values["displayOrder"],
                        values["status"],
                        values["validationJson"],
                        values["points"],
                        values["manualReviewRequired"],
                    ),
                )
                conn.commit()
                return json_response(self, 200, {"status": "ok", "activityId": aid})

            if path == "/api/activity/update":
                require_admin(payload)
                activity_id = required(payload, "activityId", "Activity ID")
                current = conn.execute("SELECT * FROM activities WHERE activityId=?", (activity_id,)).fetchone()
                if not current:
                    raise ValueError(f"Activity not found: {activity_id}")
                module = conn.execute("SELECT * FROM modules WHERE moduleId=?", (payload.get("moduleId"),)).fetchone()
                if not module:
                    raise ValueError("Module not found.")
                values = activity_payload(payload, dict(module), current["displayOrder"])
                conn.execute(
                    "UPDATE activities SET courseId=?,moduleId=?,activityType=?,title=?,content=?,configJson=?,displayOrder=?,status=?,validationJson=?,points=?,manualReviewRequired=? WHERE activityId=?",
                    (
                        values["courseId"],
                        values["moduleId"],
                        values["activityType"],
                        values["title"],
                        values["content"],
                        values["configJson"],
                        values["displayOrder"],
                        values["status"],
                        values["validationJson"],
                        values["points"],
                        values["manualReviewRequired"],
                        activity_id,
                    ),
                )
                normalize_activity_orders(conn, values["moduleId"])
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/activity/delete":
                require_admin(payload)
                activity_id = required(payload, "activityId", "Activity ID")
                current = conn.execute("SELECT * FROM activities WHERE activityId=?", (activity_id,)).fetchone()
                if not current:
                    raise ValueError(f"Activity not found: {activity_id}")
                conn.execute("UPDATE activities SET status='inactive' WHERE activityId=?", (activity_id,))
                normalize_activity_orders(conn, current["moduleId"])
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/activity/reorder":
                require_admin(payload)
                module_id = required(payload, "moduleId", "Module ID")
                ordered = payload.get("orderedIds") if isinstance(payload.get("orderedIds"), list) else []
                active = rows(conn, "SELECT activityId FROM activities WHERE moduleId=? AND status='active'", (module_id,))
                allowed = {r["activityId"] for r in active}
                if set(ordered) != allowed:
                    raise ValueError("Activity order does not match active activities in the selected module.")
                for index, activity_id in enumerate(ordered, start=1):
                    conn.execute("UPDATE activities SET displayOrder=? WHERE activityId=?", (index, activity_id))
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            if path == "/api/assessment/start-attempt":
                user_email = self.headers.get("X-User-Email", "demo.user@example.com")
                module = conn.execute("SELECT * FROM modules WHERE moduleId=? AND moduleType='assessment' AND status='active'", (payload.get("moduleId"),)).fetchone()
                if not module:
                    raise ValueError("Assessment module not found.")
                module = dict(module)
                if not to_bool(module.get("isTimed")):
                    return json_response(self, 200, {"status": "not_required"})
                attempts = rows(conn, "SELECT * FROM assessmentAttempts WHERE userEmail=? AND assessmentBlockId=?", (user_email, module["moduleId"]))
                overrides = active_rows(rows(conn, "SELECT * FROM assessmentAttemptOverrides WHERE userEmail=? AND assessmentBlockId=?", (user_email, module["moduleId"])))
                max_attempts = get_effective_max_attempts(module, overrides)
                if len(attempts) >= max_attempts:
                    raise ValueError("No attempts left for this assessment module.")
                existing = conn.execute("SELECT * FROM assessmentAttemptSessions WHERE userEmail=? AND moduleId=? AND status='active'", (user_email, module["moduleId"])).fetchone()
                if existing:
                    return json_response(self, 200, session_response(dict(existing)))
                now = datetime.now(timezone.utc)
                expires = now + timedelta(minutes=max(1, int(module.get("timeLimitMinutes") or 1)))
                session = {
                    "sessionId": "sess_" + uuid.uuid4().hex,
                    "userEmail": user_email,
                    "courseId": module["courseId"],
                    "moduleId": module["moduleId"],
                    "attemptNo": len(attempts) + 1,
                    "startedAt": now.isoformat(),
                    "expiresAt": expires.isoformat(),
                    "status": "active",
                    "submittedAt": "",
                    "submissionReason": "",
                }
                conn.execute(
                    "INSERT INTO assessmentAttemptSessions(sessionId,userEmail,courseId,moduleId,attemptNo,startedAt,expiresAt,status,submittedAt,submissionReason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (session["sessionId"], session["userEmail"], session["courseId"], session["moduleId"], session["attemptNo"], session["startedAt"], session["expiresAt"], session["status"], "", ""),
                )
                conn.commit()
                return json_response(self, 200, session_response(session))

            if path == "/api/assessment/submit-module":
                user_email = self.headers.get("X-User-Email", "demo.user@example.com")
                module = conn.execute("SELECT * FROM modules WHERE moduleId=? AND moduleType='assessment' AND status='active'", (payload.get("moduleId"),)).fetchone()
                if not module:
                    raise ValueError("Assessment module not found.")
                module = dict(module)
                tasks = active_rows(rows(conn, "SELECT * FROM activities WHERE moduleId=? ORDER BY displayOrder", (module["moduleId"],)))
                attempts = rows(conn, "SELECT * FROM assessmentAttempts WHERE userEmail=? AND assessmentBlockId=?", (user_email, module["moduleId"]))
                overrides = active_rows(rows(conn, "SELECT * FROM assessmentAttemptOverrides WHERE userEmail=? AND assessmentBlockId=?", (user_email, module["moduleId"])))
                max_attempts = get_effective_max_attempts(module, overrides)
                if len(attempts) >= max_attempts:
                    raise ValueError("No attempts left for this assessment module.")

                session = None
                if to_bool(module.get("isTimed")):
                    row = conn.execute("SELECT * FROM assessmentAttemptSessions WHERE userEmail=? AND moduleId=? AND status='active'", (user_email, module["moduleId"])).fetchone()
                    if not row:
                        raise ValueError("Start attempt before submitting this timed assessment.")
                    session = dict(row)

                expired = session and datetime.fromisoformat(session["expiresAt"]) <= datetime.now(timezone.utc)
                reason = "time_expired" if expired else payload.get("submissionReason", "manual")
                submitted_results = [] if (expired and payload.get("submissionReason") != "time_expired") else payload.get("taskResults", [])
                result = complete_assessment_attempt(conn, module, tasks, user_email, len(attempts) + 1, submitted_results, reason, session, max_attempts)
                return json_response(self, 200, result)

            if path == "/api/learning-event":
                user_email = self.headers.get("X-User-Email", "demo.user@example.com")
                conn.execute(
                    "INSERT INTO learningEvents(eventId,userEmail,courseId,activityId,activityType,eventType,answerJson,isCorrect,feedback,createdAt) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "le_" + uuid.uuid4().hex,
                        user_email,
                        payload.get("courseId", ""),
                        payload.get("activityId", ""),
                        payload.get("activityType", ""),
                        payload.get("eventType", "check_answer"),
                        json.dumps(payload.get("answer", {}), ensure_ascii=False),
                        str(payload.get("isCorrect") is True),
                        payload.get("feedback", ""),
                        now_iso(),
                    ),
                )
                conn.commit()
                return json_response(self, 200, {"status": "ok"})

            return json_response(self, 404, {"error": "Unknown endpoint"})
        except ValueError as e:
            return json_response(self, 400, {"error": str(e)})
        except Exception as e:
            return json_response(self, 500, {"error": f"Server error: {e}"})
        finally:
            conn.close()


if __name__ == "__main__":
    init_db()
    host = "127.0.0.1"
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Training app started: http://{host}:{port}")
    server.serve_forever()
