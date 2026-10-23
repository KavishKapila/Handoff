from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response, session
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import os
import json
import urllib.request
import urllib.error
import secrets
from contextlib import contextmanager
from urllib.parse import urlparse
from handoff_lint import check as check_handoff_context

BASE_DIR = Path(__file__).resolve().parent


def load_local_env():
    """Read a small .env file without an extra dependency; existing env wins."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


load_local_env()
DB_PATH = Path(os.environ.get("HANDOFF_DB", BASE_DIR / "handoff.db"))
app = Flask(__name__)
def get_secret_key():
    configured = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if configured:
        return configured
    secret_file = BASE_DIR / ".flask_secret"
    try:
        if secret_file.exists():
            value = secret_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_urlsafe(48)
        secret_file.write_text(value, encoding="utf-8")
        return value
    except OSError:
        # Ephemeral fallback for read-only environments; sessions won't persist across restarts.
        return secrets.token_urlsafe(48)

app.secret_key = get_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
STATUSES = ("Not started", "In progress", "Blocked", "Done")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@app.before_request
def protect_local_demo():
    host = (urlparse("//" + request.host).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        return "Handoff only accepts local requests.", 403
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin", "").strip()
        if origin:
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"} or origin_host not in LOCAL_HOSTS or parsed.netloc.casefold() != request.host.casefold():
                return "Cross-origin request blocked.", 403


@contextmanager
def db_connect():
    """Yield a configured SQLite connection and always commit/close it safely."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db(force_seed=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_connect() as db:
        original_version = db.execute("PRAGMA user_version").fetchone()[0]
        db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            project TEXT NOT NULL DEFAULT 'General',
            owner TEXT NOT NULL DEFAULT 'Me',
            due_date TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Not started',
            priority TEXT NOT NULL DEFAULT 'Normal',
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            name TEXT NOT NULL COLLATE NOCASE,
            role TEXT NOT NULL DEFAULT 'Member',
            created_at TEXT NOT NULL,
            UNIQUE(team_id, name)
        );
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            task_title TEXT NOT NULL,
            project TEXT NOT NULL DEFAULT 'General',
            from_person TEXT NOT NULL DEFAULT 'Me',
            to_person TEXT NOT NULL DEFAULT '',
            completed TEXT NOT NULL,
            next_steps TEXT NOT NULL,
            blockers TEXT NOT NULL DEFAULT '',
            links TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Ready to pick up',
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_handoffs_created ON handoffs(created_at DESC);
        """)
        # Team support is additive: existing tasks and handoffs remain intact.
        task_columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
        if "team_id" not in task_columns:
            db.execute("ALTER TABLE tasks ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL")
        handoff_columns = {row[1] for row in db.execute("PRAGMA table_info(handoffs)").fetchall()}
        if "team_id" not in handoff_columns:
            db.execute("ALTER TABLE handoffs ADD COLUMN team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_team ON tasks(team_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_handoffs_team ON handoffs(team_id)")
        # Idempotent SQLite schema migration. Existing v1 data is preserved.
        for column, declaration in (
            ("accepted_by", "TEXT NOT NULL DEFAULT ''"),
            ("accepted_at", "TEXT NOT NULL DEFAULT ''"),
            ("deadline", "TEXT NOT NULL DEFAULT ''"),
            ("gaps_flagged", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in handoff_columns:
                db.execute(f"ALTER TABLE handoffs ADD COLUMN {column} {declaration}")
        db.execute("""CREATE TABLE IF NOT EXISTS handoff_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            handoff_id INTEGER NOT NULL REFERENCES handoffs(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_handoff_events_handoff ON handoff_events(handoff_id, id)")
        # Create a default team and attach legacy records without changing their content.
        default_team = db.execute("SELECT id FROM teams ORDER BY id LIMIT 1").fetchone()
        if not default_team:
            cur = db.execute("INSERT INTO teams(name,description,created_at) VALUES(?,?,?)",
                             ("Student Studio", "Your starter workspace for class projects and group work.", now_text()))
            default_team_id = cur.lastrowid
            db.execute("INSERT INTO team_members(team_id,name,role,created_at) VALUES(?,?,?,?)",
                       (default_team_id, "You", "Team lead", now_text()))
        else:
            default_team_id = default_team["id"]
        db.execute("UPDATE tasks SET team_id=? WHERE team_id IS NULL", (default_team_id,))
        db.execute("UPDATE handoffs SET team_id=COALESCE(team_id, (SELECT team_id FROM tasks WHERE tasks.id=handoffs.task_id), ?) WHERE team_id IS NULL", (default_team_id,))
        should_seed = force_seed or (original_version == 0 and db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0 and db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0)
        db.execute("PRAGMA user_version = 3")
        if should_seed:
            today = datetime.now().date()
            sample_tasks = [
                ("Finish the science fair slides", "Science fair", "Aarav", (today + timedelta(days=2)).isoformat(), "In progress", "Urgent", "Add the final results chart and make the conclusion easier to scan."),
                ("Collect sources for the history report", "History project", "Maya", (today + timedelta(days=3)).isoformat(), "Not started", "High", "Find three reliable sources and add short notes for each."),
                ("Review slides and add citations", "Science fair", "You", (today + timedelta(days=1)).isoformat(), "Blocked", "High", "Need the final measurements from Aarav before citations can be finalized."),
                ("Prepare the group meeting agenda", "History project", "Riya", (today + timedelta(days=1)).isoformat(), "Done", "Normal", "Agenda is ready for the team to review."),
                ("Check accessibility and presentation timing", "Science fair", "You", (today + timedelta(days=4)).isoformat(), "Not started", "Normal", "Check contrast, font sizes, and the five-minute run-through."),
            ]
            for title, project, owner, due, status, priority, description in sample_tasks:
                stamp = now_text()
                db.execute("""INSERT INTO tasks(title,project,owner,due_date,status,priority,description,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (title, project, owner, due, status, priority, description, stamp, stamp))
            ids = {r["title"]: r["id"] for r in db.execute("SELECT id,title FROM tasks")}
            db.execute("""INSERT INTO handoffs(task_id,task_title,project,from_person,to_person,completed,next_steps,blockers,links,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                ids["Finish the science fair slides"], "Finish the science fair slides", "Science fair", "Aarav", "You",
                "The slide outline and experiment photos are in the shared folder. The results chart still needs its final numbers.",
                "1. Add the latest measurements to the results chart.\n2. Write a two-sentence conclusion.\n3. Do one timing run before sharing.",
                "Waiting on the final measurements from the experiment team.", "slides/ · experiment-notes/", "Ready to pick up", now_text()))
            db.execute("""INSERT INTO handoffs(task_id,task_title,project,from_person,to_person,completed,next_steps,blockers,links,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                ids["Prepare the group meeting agenda"], "Prepare the group meeting agenda", "History project", "Riya", "Maya",
                "Drafted the agenda and grouped questions by topic.", "Review the agenda, add your source updates, then send the final version to the group.",
                "None right now.", "meeting-agenda.md", "Picked up", (datetime.now()-timedelta(hours=4)).strftime("%Y-%m-%d %H:%M")))
            db.execute("UPDATE tasks SET owner='Maya' WHERE id=?", (ids["Prepare the group meeting agenda"],))
            db.execute("UPDATE handoffs SET accepted_by='Maya', accepted_at=created_at WHERE task_id=? AND status='Picked up'", (ids["Prepare the group meeting agenda"],))
        # Seeded rows are created using the legacy insert shape; assign them to the starter team.
        db.execute("UPDATE tasks SET team_id=? WHERE team_id IS NULL", (default_team_id,))
        db.execute("UPDATE handoffs SET team_id=COALESCE((SELECT team_id FROM tasks WHERE tasks.id=handoffs.task_id), ?) WHERE team_id IS NULL", (default_team_id,))
        # Backfill a lightweight event trail for older records and the sample data.
        existing_handoffs = db.execute("SELECT * FROM handoffs").fetchall()
        for handoff in existing_handoffs:
            has_event = db.execute("SELECT 1 FROM handoff_events WHERE handoff_id=? LIMIT 1", (handoff["id"],)).fetchone()
            if not has_event:
                db.execute("INSERT INTO handoff_events(handoff_id,kind,actor,body,created_at) VALUES(?,?,?,?,?)",
                           (handoff["id"], "created", handoff["from_person"], "Handoff record available in the event trail", handoff["created_at"]))
                if handoff["accepted_by"]:
                    db.execute("INSERT INTO handoff_events(handoff_id,kind,actor,body,created_at) VALUES(?,?,?,?,?)",
                               (handoff["id"], "accepted", handoff["accepted_by"], f"{handoff['accepted_by']} accepted responsibility", handoff["accepted_at"] or handoff["created_at"]))


def get_optional_ai_config():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
    return key, base, model


def local_draft(payload):
    """Useful no-key fallback: builds an editable handoff scaffold from task context."""
    title = str(payload.get("title", "this task")).strip()[:180] or "this task"
    project = str(payload.get("project", "the project")).strip()[:120] or "the project"
    description = str(payload.get("description", "")).strip()[:500]
    status = str(payload.get("status", "In progress"))
    blockers = str(payload.get("blockers", "")).strip()
    completed = ""
    if description:
        completed = f"Known task context: {description}\n\nAdd what has actually been completed before sending this handoff; the task description alone does not prove work is finished."
    else:
        completed = f"The task is tracked under {project}. Add a short note about what has already been completed and where the latest work lives."
    if status == "Blocked":
        next_steps = "1. Read the blocker and identify who can unblock it.\n2. Ask for the missing information or access.\n3. Resume the task and update its status once work can continue."
    elif status == "Done":
        next_steps = "1. Review the finished work.\n2. Share the result with the team.\n3. Capture any follow-up tasks or lessons learned."
    else:
        next_steps = "1. Open the latest project files and review the current state.\n2. Complete the next smallest unfinished step.\n3. Share an update and mark the task status when finished."
    blocker_text = blockers or ("Confirm whether anything is waiting on another person, decision, or file. Write 'None' if there are no blockers.")
    return {"completed": completed, "next_steps": next_steps, "blockers": blocker_text,
            "note": "Smart scaffold · no API key used. Edit these suggestions to reflect the real work."}


def ai_draft(payload):
    key, base_url, model = get_optional_ai_config()
    if not key:
        return local_draft(payload), "local"
    system = ("You help student teams write accurate, concise task handoffs. Never invent completed work, files, facts, or blockers. "
              "Use the supplied information only. Where facts are missing, explicitly ask the user to fill them in. "
              "Return only JSON with keys completed, next_steps, blockers. next_steps should be 2-4 numbered steps.")
    user_data = {k: str(payload.get(k, ""))[:1200] for k in ("title", "project", "owner", "status", "priority", "description", "blockers")}
    req_body = json.dumps({"model": model, "temperature": 0.3, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user_data)}]}).encode("utf-8")
    req = urllib.request.Request(base_url + "/chat/completions", data=req_body,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=18) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        clean = {k: str(parsed.get(k, "")).strip()[:2500] for k in ("completed", "next_steps", "blockers")}
        if not clean["completed"] or not clean["next_steps"]:
            raise ValueError("AI returned an incomplete draft")
        return {**clean, "note": f"AI-assisted draft · {model}. Check every detail before sharing."}, "ai"
    except Exception as exc:
        fallback = local_draft(payload)
        fallback["note"] = "AI provider unavailable; a no-key scaffold was created instead. Review before sharing."
        app.logger.warning("AI draft fallback: %s", exc)
        return fallback, "fallback"


@app.route("/")
def dashboard():
    search = request.args.get("q", "").strip()[:100]
    status_filter = request.args.get("status", "All")
    project_filter = request.args.get("project", "All")
    team_filter = request.args.get("team", "").strip()
    with db_connect() as db:
        team_rows = db.execute("SELECT id,name FROM teams ORDER BY id").fetchall()
        team_ids = {str(row["id"]) for row in team_rows}
        default_team_id = str(team_rows[0]["id"]) if team_rows else "All"
        if not team_filter:
            team_filter = str(session.get("active_team_id", default_team_id))
        if team_filter not in team_ids and team_filter != "All":
            team_filter = default_team_id
        if team_filter != "All":
            session["active_team_id"] = int(team_filter)
        else:
            session.pop("active_team_id", None)
        where, args = [], []
        if team_filter in team_ids:
            where.append("team_id = ?"); args.append(int(team_filter))
        if status_filter in STATUSES:
            where.append("status = ?"); args.append(status_filter)
        if project_filter != "All":
            where.append("project = ?"); args.append(project_filter)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title LIKE ? ESCAPE '\\' OR project LIKE ? ESCAPE '\\' OR owner LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            args.extend([f"%{escaped}%"]*4)
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END, CASE WHEN due_date IS NULL OR due_date='' THEN 1 ELSE 0 END, due_date ASC, id DESC"
        tasks = db.execute(sql, args).fetchall()
        handoff_where, handoff_args = [], []
        if team_filter in team_ids:
            handoff_where.append("team_id = ?"); handoff_args.append(int(team_filter))
        if project_filter != "All":
            handoff_where.append("project = ?"); handoff_args.append(project_filter)
        if search:
            handoff_where.append("(task_title LIKE ? ESCAPE '\\' OR project LIKE ? ESCAPE '\\' OR from_person LIKE ? ESCAPE '\\' OR to_person LIKE ? ESCAPE '\\' OR completed LIKE ? ESCAPE '\\' OR next_steps LIKE ? ESCAPE '\\' OR blockers LIKE ? ESCAPE '\\')")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            handoff_args.extend([f"%{escaped}%"]*7)
        handoff_sql = "SELECT * FROM handoffs" + (" WHERE " + " AND ".join(handoff_where) if handoff_where else "") + " ORDER BY id DESC LIMIT 8"
        handoffs = db.execute(handoff_sql, handoff_args).fetchall()
        active_team = next((row for row in team_rows if str(row["id"]) == team_filter), None)
        team_sql = " WHERE team_id = ?" if team_filter in team_ids else ""
        team_args = (int(team_filter),) if team_filter in team_ids else ()
        all_tasks = db.execute("SELECT * FROM tasks" + team_sql + " ORDER BY id DESC", team_args).fetchall()
        projects = [r[0] for r in db.execute("SELECT DISTINCT project FROM tasks" + team_sql + " ORDER BY project", team_args).fetchall()]
        total = db.execute("SELECT COUNT(*) FROM tasks" + team_sql, team_args).fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM tasks" + (team_sql + " AND status != 'Done'" if team_sql else " WHERE status != 'Done'"), team_args).fetchone()[0]
        blocked = db.execute("SELECT COUNT(*) FROM tasks" + (team_sql + " AND status = 'Blocked'" if team_sql else " WHERE status = 'Blocked'"), team_args).fetchone()[0]
        handoff_count = db.execute("SELECT COUNT(*) FROM handoffs" + team_sql, team_args).fetchone()[0]
        ready_count = db.execute("SELECT COUNT(*) FROM handoffs" + (team_sql + " AND status = 'Ready to pick up'" if team_sql else " WHERE status = 'Ready to pick up'"), team_args).fetchone()[0]
        waiting_count = ready_count
        gaps_caught = db.execute("SELECT COALESCE(SUM(gaps_flagged),0) FROM handoffs" + team_sql, team_args).fetchone()[0]
    return render_template("index.html", tasks=tasks, all_tasks=all_tasks, handoffs=handoffs,
        total=total, active=active, blocked=blocked, handoff_count=handoff_count, ready_count=ready_count,
        waiting_count=waiting_count, gaps_caught=gaps_caught,
        projects=projects, teams=team_rows, active_team_id=(int(team_filter) if team_filter in team_ids else None), search=search, status_filter=status_filter, project_filter=project_filter, team_filter=team_filter, today=datetime.now().date().isoformat(),
        statuses=STATUSES, priorities=PRIORITIES, ai_enabled=bool(get_optional_ai_config()[0]))


@app.post("/tasks")
def add_task():
    title = request.form.get("title", "").strip()
    if not title:
        flash("Add a task title first.", "error"); return redirect(url_for("dashboard") + "#task-board")
    status = request.form.get("status", "Not started")
    priority = request.form.get("priority", "Normal")
    if status not in STATUSES: status = "Not started"
    if priority not in PRIORITIES: priority = "Normal"
    due_date = request.form.get("due_date", "").strip()
    if due_date:
        try:
            due_date = datetime.strptime(due_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            flash("Use a valid due date.", "error"); return redirect(url_for("dashboard") + "#task-board")
    stamp = now_text()
    team_raw = request.form.get("team_id", "").strip() or str(session.get("active_team_id", ""))
    team_id = None
    with db_connect() as db:
        if team_raw:
            if not team_raw.isdigit() or not db.execute("SELECT id FROM teams WHERE id=?", (int(team_raw),)).fetchone():
                flash("Choose a valid team.", "error"); return redirect(url_for("dashboard") + "#task-board")
            team_id = int(team_raw)
        db.execute("""INSERT INTO tasks(title,project,owner,due_date,status,priority,description,created_at,updated_at,team_id)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (title[:180], request.form.get("project", "General").strip()[:100] or "General",
            request.form.get("owner", "You").strip()[:100] or "You", due_date, status, priority,
            request.form.get("description", "").strip()[:1200], stamp, stamp, team_id))
    flash("Task added to the board.", "success")
    return redirect(url_for("dashboard") + "#task-board")




@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
def edit_task(task_id):
    with db_connect() as db:
        task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return "Task not found.", 404
        if request.method == "GET":
            teams = db.execute("SELECT id,name FROM teams ORDER BY name COLLATE NOCASE").fetchall()
            return render_template("task_edit.html", task=task, teams=teams, statuses=STATUSES, priorities=PRIORITIES)
        title = request.form.get("title", "").strip()
        if not title:
            flash("Task title is required.", "error")
            return redirect(url_for("edit_task", task_id=task_id))
        due_date = request.form.get("due_date", "").strip()
        if due_date:
            try: due_date = datetime.strptime(due_date, "%Y-%m-%d").date().isoformat()
            except ValueError:
                flash("Use a valid due date.", "error"); return redirect(url_for("edit_task", task_id=task_id))
        status = request.form.get("status", "Not started")
        priority = request.form.get("priority", "Normal")
        if status not in STATUSES or priority not in PRIORITIES:
            flash("Choose a valid status and priority.", "error"); return redirect(url_for("edit_task", task_id=task_id))
        team_raw = request.form.get("team_id", "").strip()
        team_id = None
        if team_raw:
            if not team_raw.isdigit() or not db.execute("SELECT id FROM teams WHERE id=?", (int(team_raw),)).fetchone():
                flash("Choose a valid team.", "error"); return redirect(url_for("edit_task", task_id=task_id))
            team_id = int(team_raw)
        db.execute("""UPDATE tasks SET title=?,project=?,owner=?,due_date=?,status=?,priority=?,description=?,updated_at=?,team_id=? WHERE id=?""",
                   (title[:180], request.form.get("project", "General").strip()[:100] or "General",
                    request.form.get("owner", "You").strip()[:100] or "You", due_date, status, priority,
                    request.form.get("description", "").strip()[:1200], now_text(), team_id, task_id))
    flash("Task details updated.", "success")
    return redirect(url_for("dashboard") + "#task-board")

@app.post("/tasks/<int:task_id>/update")
def update_task(task_id):
    status = request.form.get("status", "In progress")
    if status not in STATUSES: status = "In progress"
    with db_connect() as db:
        cur = db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_text(), task_id))
        if cur.rowcount == 0:
            flash("That task could not be found.", "error")
            return redirect(url_for("dashboard") + "#task-board")
    flash("Task status updated.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.post("/tasks/<int:task_id>/delete")
def delete_task(task_id):
    with db_connect() as db:
        cur = db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        if cur.rowcount == 0:
            flash("That task could not be found.", "error")
            return redirect(url_for("dashboard") + "#task-board")
    flash("Task removed. Existing handoffs remain in the log.", "success")
    return redirect(url_for("dashboard") + "#task-board")


@app.post("/api/draft")
def draft_handoff():
    if len(request.get_data(cache=True)) > 64 * 1024:
        return jsonify({"error": "Draft request is too large."}), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object for the draft."}), 400
    title = str(payload.get("title", "")).strip()[:180]
    if not title:
        return jsonify({"error": "Choose a task first."}), 400
    draft, mode = ai_draft(payload)
    return jsonify({**draft, "mode": mode})


@app.post("/handoffs")
def create_handoff():
    task_raw = request.form.get("task_id", "").strip()
    if task_raw and (not task_raw.isdigit() or len(task_raw) > 9):
        flash("Choose a valid task.", "error"); return redirect(url_for("dashboard") + "#handoff-composer")
    task_id = int(task_raw) if task_raw else None
    task = None
    with db_connect() as db:
        if task_id is not None:
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                flash("That task no longer exists. Refresh and try again.", "error")
                return redirect(url_for("dashboard") + "#handoff-composer")
            open_handoff = db.execute("SELECT id FROM handoffs WHERE task_id=? AND status='Ready to pick up'", (task_id,)).fetchone()
            if open_handoff:
                flash("This task already has a handoff waiting for pickup. Open it before creating another.", "error")
                return redirect(url_for("handoff_detail", handoff_id=open_handoff["id"]))
        title = (task["title"] if task else request.form.get("task_title", "").strip())[:180]
        if not title:
            flash("Choose a task or write a handoff title.", "error"); return redirect(url_for("dashboard") + "#handoff-composer")
        completed = request.form.get("completed", "").strip()[:3000]
        next_steps = request.form.get("next_steps", "").strip()[:3000]
        to_person = request.form.get("to_person", "").strip()[:100]
        if not completed or not next_steps or not to_person:
            flash("Add what is done, what happens next, and who will pick it up.", "error")
            return redirect(url_for("dashboard") + "#handoff-composer")
        deadline = request.form.get("deadline", "").strip()
        if deadline:
            try: deadline = datetime.strptime(deadline, "%Y-%m-%d").date().isoformat()
            except ValueError:
                flash("Use a valid handoff deadline.", "error"); return redirect(url_for("dashboard") + "#handoff-composer")
        project = task["project"] if task else (request.form.get("project", "General").strip()[:100] or "General")
        team_id = task["team_id"] if task else None
        raw_team_value = request.form.get("team_id", "").strip() or str(session.get("active_team_id", ""))
        if team_id is None and raw_team_value:
            raw_team = raw_team_value
            if not raw_team.isdigit() or not db.execute("SELECT id FROM teams WHERE id=?", (int(raw_team),)).fetchone():
                flash("Choose a valid team.", "error"); return redirect(url_for("dashboard") + "#handoff-composer")
            team_id = int(raw_team)
        fields = {"completed": completed, "next_steps": next_steps, "blockers": request.form.get("blockers", "").strip()[:1500], "links": request.form.get("links", "").strip()[:1500], "to_person": to_person}
        gaps = check_handoff_context(fields)
        cur = db.execute("""INSERT INTO handoffs(task_id,task_title,project,from_person,to_person,completed,next_steps,blockers,links,status,created_at,deadline,gaps_flagged,team_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (task_id, title, project,
            request.form.get("from_person", "You").strip()[:100] or "You", to_person,
            completed, next_steps, fields["blockers"], fields["links"], "Ready to pick up", now_text(), deadline, len(gaps), team_id))
        handoff_id = cur.lastrowid
        db.execute("INSERT INTO handoff_events(handoff_id,kind,actor,body,created_at) VALUES(?,?,?,?,?)", (handoff_id, "created", request.form.get("from_person", "You").strip()[:100] or "You", f"Handoff sent to {to_person}", now_text()))
        if task and task["status"] == "Not started":
            db.execute("UPDATE tasks SET status='In progress', updated_at=? WHERE id=?", (now_text(), task_id))
    if gaps:
        flash(f"Handoff saved with {len(gaps)} context gap(s) to review. Open the briefing to see details.", "success")
    else:
        flash("Handoff saved. Context is ready for the next person.", "success")
    return redirect(url_for("handoff_detail", handoff_id=handoff_id))


@app.get("/handoffs/<int:handoff_id>")
def handoff_detail(handoff_id):
    with db_connect() as db:
        h = db.execute("SELECT h.*, t.owner AS task_owner, t.status AS task_status, teams.name AS team_name FROM handoffs h LEFT JOIN tasks t ON t.id=h.task_id LEFT JOIN teams ON teams.id=h.team_id WHERE h.id=?", (handoff_id,)).fetchone()
        if not h:
            return "Handoff not found.", 404
        events = db.execute("SELECT * FROM handoff_events WHERE handoff_id=? ORDER BY id", (handoff_id,)).fetchall()
    gaps = check_handoff_context(dict(h))
    return render_template("handoff.html", h=h, events=events, gaps=gaps)


@app.post("/handoffs/<int:handoff_id>/accept")
def accept_handoff(handoff_id):
    receiver = request.form.get("receiver", "").strip()[:100]
    if not receiver:
        flash("Enter your name to confirm pickup.", "error")
        return redirect(url_for("handoff_detail", handoff_id=handoff_id))
    with db_connect() as db:
        h = db.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
        if not h:
            return "Handoff not found.", 404
        if h["status"] != "Ready to pick up":
            flash("This handoff has already been picked up or completed.", "error")
            return redirect(url_for("handoff_detail", handoff_id=handoff_id))
        stamp = now_text()
        transfer = db.execute("UPDATE handoffs SET status='Picked up', accepted_by=?, accepted_at=? WHERE id=? AND status='Ready to pick up'", (receiver, stamp, handoff_id))
        if transfer.rowcount != 1:
            flash("Another receiver has already accepted this handoff.", "error")
            return redirect(url_for("handoff_detail", handoff_id=handoff_id))
        if h["task_id"] is not None:
            db.execute("UPDATE tasks SET owner=?, status='In progress', updated_at=? WHERE id=?", (receiver, stamp, h["task_id"]))
        db.execute("INSERT INTO handoff_events(handoff_id,kind,actor,body,created_at) VALUES(?,?,?,?,?)", (handoff_id, "accepted", receiver, f"{receiver} accepted responsibility", stamp))
    flash(f"Pickup confirmed. {receiver} is now responsible for this work.", "success")
    return redirect(url_for("handoff_detail", handoff_id=handoff_id))


@app.post("/handoffs/<int:handoff_id>/question")
def ask_handoff_question(handoff_id):
    actor = request.form.get("actor", "").strip()[:100]
    question = request.form.get("question", "").strip()[:1000]
    if not actor or len(question) < 3:
        flash("Add your name and a clear question.", "error")
        return redirect(url_for("handoff_detail", handoff_id=handoff_id))
    with db_connect() as db:
        if not db.execute("SELECT id FROM handoffs WHERE id=?", (handoff_id,)).fetchone():
            return "Handoff not found.", 404
        db.execute("INSERT INTO handoff_events(handoff_id,kind,actor,body,created_at) VALUES(?,?,?,?,?)", (handoff_id, "question", actor, question, now_text()))
    flash("Question added to the handoff event trail.", "success")
    return redirect(url_for("handoff_detail", handoff_id=handoff_id))


@app.post("/api/check")
def check_handoff():
    if len(request.get_data(cache=True)) > 32 * 1024:
        return jsonify({"error": "Request is too large."}), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object for the context check."}), 400
    safe = {key: str(payload.get(key, ""))[:3000] for key in ("completed", "next_steps", "blockers", "links", "to_person")}
    gaps = check_handoff_context(safe)
    return jsonify({"gaps": gaps, "count": len(gaps), "label": "Rule-based context check · not AI"})


@app.post("/demo/reset")
def reset_demo():
    with db_connect() as db:
        db.execute("DELETE FROM handoff_events")
        db.execute("DELETE FROM handoffs")
        db.execute("DELETE FROM tasks")
        db.execute("DELETE FROM sqlite_sequence WHERE name IN ('tasks','handoffs','handoff_events')")
    init_db(force_seed=True)
    flash("Demo workspace reset to its sample tasks and handoffs.", "success")
    return redirect(url_for("dashboard"))


@app.get("/handoffs/<int:handoff_id>/export")
def export_handoff(handoff_id):
    with db_connect() as db:
        h = db.execute("SELECT h.*, teams.name AS team_name FROM handoffs h LEFT JOIN teams ON teams.id=h.team_id WHERE h.id=?", (handoff_id,)).fetchone()
        events = db.execute("SELECT * FROM handoff_events WHERE handoff_id=? ORDER BY id", (handoff_id,)).fetchall() if h else []
    if not h:
        return "Handoff not found.", 404
    event_text = "\n".join(f"- {e['created_at']} — {e['kind'].replace('_', ' ').title()} by {e['actor']}: {e['body']}" for e in events) or "No events recorded."
    content = f"""# Handoff: {h['task_title']}\n\n**Project:** {h['project']}  \n**Team:** {h['team_name'] or 'Unassigned'}  \n**From:** {h['from_person']}  \n**To:** {h['to_person'] or 'Unassigned'}  \n**Created:** {h['created_at']}  \n**Deadline:** {h['deadline'] or 'Not set'}  \n**Status:** {h['status']}  \n**Accepted by:** {h['accepted_by'] or 'Not yet accepted'}\n\n## What is done\n{h['completed']}\n\n## Next steps\n{h['next_steps']}\n\n## Blockers / decisions needed\n{h['blockers'] or 'None listed'}\n\n## Links and files\n{h['links'] or 'None listed'}\n\n## Event trail\n{event_text}\n"""
    response = make_response(content)
    response.headers["Content-Type"] = "text/markdown; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=handoff-{handoff_id}.md"
    return response


@app.post("/handoffs/<int:handoff_id>/status")
def update_handoff_status(handoff_id):
    status = request.form.get("status", "Ready to pick up")
    if status not in ("Ready to pick up", "Picked up", "Completed"):
        flash("Choose a valid handoff status.", "error"); return redirect(url_for("dashboard") + "#handoffs")
    with db_connect() as db:
        h = db.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
        if not h:
            return "Handoff not found.", 404
        if status == "Picked up" and h["status"] != "Picked up":
            flash("Use the briefing page to confirm pickup and transfer ownership.", "error")
            return redirect(url_for("handoff_detail", handoff_id=handoff_id))
        if status == "Completed" and h["status"] == "Ready to pick up":
            flash("The receiver must accept this handoff before it can be completed.", "error")
            return redirect(url_for("handoff_detail", handoff_id=handoff_id))
        db.execute("UPDATE handoffs SET status=? WHERE id=?", (status, handoff_id))
        db.execute("INSERT INTO handoff_events(handoff_id,kind,actor,body,created_at) VALUES(?,?,?,?,?)", (handoff_id, "status", "Workspace", f"Status changed to {status}", now_text()))
    flash("Handoff status updated.", "success")
    return redirect(url_for("handoff_detail", handoff_id=handoff_id))


@app.get("/teams")
def teams_page():
    with db_connect() as db:
        teams = db.execute("""SELECT tm.*, (SELECT COUNT(*) FROM team_members m WHERE m.team_id=tm.id) AS member_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.team_id=tm.id) AS task_count,
            (SELECT COUNT(*) FROM handoffs h WHERE h.team_id=tm.id) AS handoff_count
            FROM teams tm ORDER BY tm.name COLLATE NOCASE""").fetchall()
    return render_template("teams.html", teams=teams)


@app.post("/teams")
def create_team():
    name = request.form.get("name", "").strip()[:80]
    description = request.form.get("description", "").strip()[:500]
    if len(name) < 2:
        flash("Team name must be at least 2 characters.", "error")
        return redirect(url_for("teams_page"))
    try:
        with db_connect() as db:
            cur = db.execute("INSERT INTO teams(name,description,created_at) VALUES(?,?,?)", (name, description, now_text()))
            team_id = cur.lastrowid
            starter = request.form.get("starter", "").strip()[:100]
            if starter:
                db.execute("INSERT INTO team_members(team_id,name,role,created_at) VALUES(?,?,?,?)", (team_id, starter, "Team lead", now_text()))
    except sqlite3.IntegrityError:
        flash("A team with that name already exists. Choose a different name.", "error")
        return redirect(url_for("teams_page"))
    session["active_team_id"] = team_id
    flash(f"Team '{name}' created.", "success")
    return redirect(url_for("team_detail", team_id=team_id))


@app.get("/teams/<int:team_id>")
def team_detail(team_id):
    with db_connect() as db:
        team = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team:
            return "Team not found.", 404
        session["active_team_id"] = team_id
        members = db.execute("SELECT * FROM team_members WHERE team_id=? ORDER BY CASE role WHEN 'Team lead' THEN 0 ELSE 1 END, name COLLATE NOCASE", (team_id,)).fetchall()
        tasks = db.execute("SELECT * FROM tasks WHERE team_id=? ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END, id DESC", (team_id,)).fetchall()
        handoffs = db.execute("SELECT * FROM handoffs WHERE team_id=? ORDER BY id DESC LIMIT 10", (team_id,)).fetchall()
    return render_template("team_detail.html", team=team, members=members, tasks=tasks, handoffs=handoffs, statuses=STATUSES)


@app.post("/teams/<int:team_id>/members")
def add_team_member(team_id):
    name = request.form.get("name", "").strip()[:100]
    role = request.form.get("role", "Member").strip()[:40] or "Member"
    if role not in {"Team lead", "Member", "Reviewer", "Coordinator"}:
        role = "Member"
    with db_connect() as db:
        if not db.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone():
            return "Team not found.", 404
        if len(name) < 2:
            flash("Enter a member name with at least 2 characters.", "error")
            return redirect(url_for("team_detail", team_id=team_id))
        try:
            db.execute("INSERT INTO team_members(team_id,name,role,created_at) VALUES(?,?,?,?)", (team_id, name, role, now_text()))
        except sqlite3.IntegrityError:
            flash("That person is already on this team.", "error")
            return redirect(url_for("team_detail", team_id=team_id))
    flash(f"{name} added to the team roster.", "success")
    return redirect(url_for("team_detail", team_id=team_id))


@app.post("/teams/<int:team_id>/members/<int:member_id>/delete")
def remove_team_member(team_id, member_id):
    with db_connect() as db:
        cur = db.execute("DELETE FROM team_members WHERE id=? AND team_id=?", (member_id, team_id))
        if cur.rowcount == 0:
            return "Team member not found.", 404
    flash("Team member removed.", "success")
    return redirect(url_for("team_detail", team_id=team_id))


@app.post("/teams/<int:team_id>/delete")
def delete_team(team_id):
    with db_connect() as db:
        team = db.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team:
            return "Team not found.", 404
        db.execute("DELETE FROM teams WHERE id=?", (team_id,))
    with db_connect() as db:
        remaining = db.execute("SELECT id FROM teams ORDER BY id LIMIT 1").fetchone()
    if remaining:
        session["active_team_id"] = remaining["id"]
    else:
        session.pop("active_team_id", None)
    flash(f"Team '{team['name']}' deleted. Its tasks and handoffs were kept as unassigned.", "success")
    return redirect(url_for("teams_page"))


@app.get("/health")
def health():
    return jsonify({"ok": True, "app": "Handoff", "ai": "configured" if get_optional_ai_config()[0] else "local-fallback"})


init_db()

if __name__ == "__main__":
    import threading
    import webbrowser
    port = int(os.environ.get("PORT", "5000"))
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="127.0.0.1", port=port)
