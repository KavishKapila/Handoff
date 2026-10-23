"""Smoke tests. Run with: python -m pip install -r requirements.txt pytest && pytest -q"""
import importlib.util
import os
from pathlib import Path
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("handoff_app", ROOT / "app.py")
app_module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = app_module
spec.loader.exec_module(app_module)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "test-handoff.db")
    app_module.init_db()
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with app_module.app.test_client() as client:
        yield client


def test_dashboard_renders_seeded_workspace(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Handoff" in response.data
    assert b"Team task board" in response.data
    assert b"Finish the science fair slides" in response.data


def test_add_task_and_filter(client):
    response = client.post("/tasks", data={
        "title": "Write demo script", "project": "Taskforge", "owner": "You",
        "status": "Not started", "priority": "High", "description": "Prepare a short walkthrough"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Write demo script" in response.data
    filtered = client.get("/?q=demo&status=Not+started")
    assert filtered.status_code == 200
    assert b"Write demo script" in filtered.data


def test_save_and_export_handoff(client):
    response = client.post("/handoffs", data={
        "task_id": "", "task_title": "Publish research summary", "project": "History",
        "from_person": "Sam", "to_person": "Lee", "completed": "Outline and citations are ready.",
        "next_steps": "1. Proofread.\n2. Submit.", "blockers": "None", "links": "summary.md"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Publish research summary" in response.data
    with app_module.db_connect() as db:
        handoff_id = db.execute("SELECT id FROM handoffs WHERE task_title=?", ("Publish research summary",)).fetchone()[0]
    export = client.get(f"/handoffs/{handoff_id}/export")
    assert export.status_code == 200
    assert b"## Next steps" in export.data
    assert b"Proofread" in export.data


def test_local_draft_needs_no_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/api/draft", json={
        "title": "Make poster accessible", "project": "Science fair",
        "status": "In progress", "description": "Review contrast and font sizes"
    })
    assert response.status_code == 200
    result = response.get_json()
    assert result["mode"] == "local"
    assert result["completed"]
    assert result["next_steps"]


def test_handoff_status_update_requires_pickup_receipt(client):
    with app_module.db_connect() as db:
        handoff_id = db.execute("SELECT id FROM handoffs WHERE status='Ready to pick up' LIMIT 1").fetchone()[0]
    response = client.post(f"/handoffs/{handoff_id}/status", data={"status": "Picked up"})
    assert response.status_code == 302
    with app_module.db_connect() as db:
        status = db.execute("SELECT status FROM handoffs WHERE id=?", (handoff_id,)).fetchone()[0]
    assert status == "Ready to pick up"
    accepted = client.post(f"/handoffs/{handoff_id}/accept", data={"receiver": "Maya"})
    assert accepted.status_code == 302
    with app_module.db_connect() as db:
        status = db.execute("SELECT status FROM handoffs WHERE id=?", (handoff_id,)).fetchone()[0]
    assert status == "Picked up"


def test_bad_handoff_task_id_does_not_500(client):
    response = client.post('/handoffs', data={
        'task_id': '99999999', 'to_person': 'Lee', 'completed': 'Work has been completed and reviewed.',
        'next_steps': 'Review the final document and submit it.',
    })
    assert response.status_code == 302
    assert b'Location' in response.headers


def test_cross_origin_post_is_blocked(client):
    response = client.post('/tasks', data={'title': 'Nope'}, headers={'Origin': 'http://evil.example'})
    assert response.status_code == 403


def test_invalid_task_date_is_rejected(client):
    response = client.post('/tasks', data={'title': 'Bad date', 'due_date': 'not-a-date'})
    assert response.status_code == 302
    with app_module.db_connect() as db:
        assert db.execute("SELECT id FROM tasks WHERE title='Bad date'").fetchone() is None


def test_handoff_acceptance_transfers_owner_and_records_event(client):
    with app_module.db_connect() as db:
        task_id = db.execute("SELECT id FROM tasks WHERE title='Finish the science fair slides'").fetchone()[0]
        handoff_id = db.execute("SELECT id FROM handoffs WHERE task_id=? AND status='Ready to pick up'", (task_id,)).fetchone()[0]
    page = client.get(f'/handoffs/{handoff_id}')
    assert page.status_code == 200
    response = client.post(f'/handoffs/{handoff_id}/accept', data={'receiver': 'Kai'})
    assert response.status_code == 302
    with app_module.db_connect() as db:
        task = db.execute('SELECT owner FROM tasks WHERE id=?', (task_id,)).fetchone()
        handoff = db.execute('SELECT status,accepted_by,accepted_at FROM handoffs WHERE id=?', (handoff_id,)).fetchone()
        event_count = db.execute("SELECT COUNT(*) FROM handoff_events WHERE handoff_id=? AND kind='accepted'", (handoff_id,)).fetchone()[0]
    assert task['owner'] == 'Kai'
    assert handoff['status'] == 'Picked up'
    assert handoff['accepted_by'] == 'Kai'
    assert handoff['accepted_at']
    assert event_count == 1


def test_double_accept_does_not_transfer_twice(client):
    with app_module.db_connect() as db:
        handoff_id = db.execute("SELECT id FROM handoffs WHERE status='Ready to pick up' LIMIT 1").fetchone()[0]
    client.post(f'/handoffs/{handoff_id}/accept', data={'receiver': 'Kai'})
    client.post(f'/handoffs/{handoff_id}/accept', data={'receiver': 'Mira'})
    with app_module.db_connect() as db:
        handoff = db.execute('SELECT accepted_by FROM handoffs WHERE id=?', (handoff_id,)).fetchone()
        count = db.execute("SELECT COUNT(*) FROM handoff_events WHERE handoff_id=? AND kind='accepted'", (handoff_id,)).fetchone()[0]
    assert handoff['accepted_by'] == 'Kai'
    assert count == 1


def test_rule_based_context_check(client):
    good = client.post('/api/check', json={
        'to_person': 'Lee', 'completed': 'The draft report is complete and reviewed.',
        'next_steps': 'Review the chart and submit the final report.', 'blockers': 'None',
        'links': 'https://example.com/report'
    })
    assert good.status_code == 200
    assert good.get_json()['count'] == 0
    vague = client.post('/api/check', json={
        'to_person': '', 'completed': 'As discussed, the file is mostly done.',
        'next_steps': 'Finish it ASAP.', 'blockers': ''
    })
    assert vague.status_code == 200
    assert vague.get_json()['count'] >= 3


def test_question_is_added_to_event_trail(client):
    with app_module.db_connect() as db:
        handoff_id = db.execute('SELECT id FROM handoffs LIMIT 1').fetchone()[0]
    response = client.post(f'/handoffs/{handoff_id}/question', data={'actor': 'Lee', 'question': 'Where is the latest chart?'})
    assert response.status_code == 302
    with app_module.db_connect() as db:
        event = db.execute("SELECT body FROM handoff_events WHERE handoff_id=? AND kind='question'", (handoff_id,)).fetchone()
    assert event['body'] == 'Where is the latest chart?'


def test_missing_task_update_returns_no_false_success(client):
    response = client.post('/tasks/999999/update', data={'status': 'Done'})
    assert response.status_code == 302
    with app_module.db_connect() as db:
        assert db.execute('SELECT id FROM tasks WHERE id=999999').fetchone() is None


def test_task_edit_updates_existing_task(client):
    with app_module.db_connect() as db:
        task_id = db.execute('SELECT id FROM tasks LIMIT 1').fetchone()[0]
    assert client.get(f'/tasks/{task_id}/edit').status_code == 200
    response = client.post(f'/tasks/{task_id}/edit', data={
        'title': 'Updated title', 'project': 'Taskforge', 'owner': 'Kai', 'due_date': '',
        'status': 'In progress', 'priority': 'Urgent', 'description': 'Updated context'
    })
    assert response.status_code == 302
    with app_module.db_connect() as db:
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    assert task['title'] == 'Updated title'
    assert task['owner'] == 'Kai'


def test_oversized_check_request_rejected(client):
    response = client.post('/api/check', data='x' * (33 * 1024), content_type='application/json')
    assert response.status_code in (400, 413)


def test_completed_task_not_overdue_in_dashboard(client):
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with app_module.db_connect() as db:
        task_id = db.execute("SELECT id FROM tasks WHERE status='Done' LIMIT 1").fetchone()[0]
        db.execute("UPDATE tasks SET due_date=? WHERE id=?", (yesterday, task_id))
        title = db.execute("SELECT title FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    response = client.get('/')
    assert response.status_code == 200
    page = response.data.decode()
    row = page[page.find(title):page.find(title) + 800]
    assert 'class="due-date overdue"' not in row


def test_empty_workspace_does_not_reseed_on_restart(client):
    with app_module.db_connect() as db:
        db.execute('DELETE FROM handoff_events')
        db.execute('DELETE FROM handoffs')
        db.execute('DELETE FROM tasks')
    app_module.init_db()
    with app_module.db_connect() as db:
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0] == 0


def test_reset_demo_restores_sample_data(client):
    response = client.post('/demo/reset')
    assert response.status_code == 302
    with app_module.db_connect() as db:
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] >= 5
        assert db.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0] >= 2


def test_v1_database_migrates_without_losing_task(client, tmp_path, monkeypatch):
    old_path = tmp_path / 'v1.db'
    db = __import__('sqlite3').connect(old_path)
    db.executescript('''
        CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,project TEXT NOT NULL DEFAULT 'General',owner TEXT NOT NULL DEFAULT 'Me',due_date TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'Not started',priority TEXT NOT NULL DEFAULT 'Normal',description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE handoffs(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER,task_title TEXT NOT NULL,project TEXT NOT NULL DEFAULT 'General',from_person TEXT NOT NULL DEFAULT 'Me',to_person TEXT NOT NULL DEFAULT '',completed TEXT NOT NULL,next_steps TEXT NOT NULL,blockers TEXT NOT NULL DEFAULT '',links TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'Ready to pick up',created_at TEXT NOT NULL);
        INSERT INTO tasks(title,created_at,updated_at) VALUES('Legacy task','2026-01-01 10:00','2026-01-01 10:00');
    ''')
    db.commit(); db.close()
    monkeypatch.setattr(app_module, 'DB_PATH', old_path)
    app_module.init_db()
    with app_module.db_connect() as db:
        assert db.execute("SELECT title FROM tasks WHERE title='Legacy task'").fetchone()[0] == 'Legacy task'
        cols = {row[1] for row in db.execute('PRAGMA table_info(handoffs)').fetchall()}
        assert {'accepted_by', 'accepted_at', 'deadline', 'gaps_flagged'} <= cols
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3


def test_create_team_and_add_member(client):
    response = client.post("/teams", data={
        "name": "Robotics Crew", "description": "Build the demo robot", "starter": "Ari"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Robotics Crew" in response.data
    with app_module.db_connect() as db:
        team = db.execute("SELECT * FROM teams WHERE name=?", ("Robotics Crew",)).fetchone()
        assert team is not None
        member_count = db.execute("SELECT COUNT(*) FROM team_members WHERE team_id=?", (team["id"],)).fetchone()[0]
        assert member_count == 1
    response = client.post(f"/teams/{team['id']}/members", data={"name": "Lee", "role": "Reviewer"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Lee" in response.data


def test_task_can_be_assigned_to_team_and_team_delete_keeps_task(client):
    client.post("/teams", data={"name": "Debate Team", "description": "Prepare arguments"})
    with app_module.db_connect() as db:
        team_id = db.execute("SELECT id FROM teams WHERE name=?", ("Debate Team",)).fetchone()[0]
    response = client.post("/tasks", data={
        "title": "Prepare opening statement", "project": "Debate", "owner": "Rin",
        "status": "Not started", "priority": "High", "team_id": str(team_id)
    })
    assert response.status_code == 302
    with app_module.db_connect() as db:
        task = db.execute("SELECT * FROM tasks WHERE title=?", ("Prepare opening statement",)).fetchone()
        assert task["team_id"] == team_id
    deleted = client.post(f"/teams/{team_id}/delete")
    assert deleted.status_code == 302
    with app_module.db_connect() as db:
        task = db.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        assert task is not None
        assert task["team_id"] is None


def test_team_duplicate_names_are_rejected(client):
    first = client.post("/teams", data={"name": "Design Team"})
    second = client.post("/teams", data={"name": "design team"}, follow_redirects=True)
    assert first.status_code == 302
    assert second.status_code == 200
    assert b"already exists" in second.data


def test_team_workspace_can_be_created_and_managed(client):
    page = client.get('/teams')
    assert page.status_code == 200
    assert b'Create a team' in page.data
    created = client.post('/teams', data={
        'name': 'Robotics Crew', 'description': 'Build and test our robot.', 'starter': 'Kai'
    })
    assert created.status_code == 302
    with app_module.db_connect() as db:
        team = db.execute("SELECT * FROM teams WHERE name='Robotics Crew'").fetchone()
        assert team is not None
        team_id = team['id']
        lead = db.execute('SELECT name,role FROM team_members WHERE team_id=?', (team_id,)).fetchone()
    assert lead['name'] == 'Kai'
    assert lead['role'] == 'Team lead'
    detail = client.get(f'/teams/{team_id}')
    assert detail.status_code == 200
    assert b'Robotics Crew' in detail.data
    client.post(f'/teams/{team_id}/members', data={'name': 'Mira', 'role': 'Reviewer'})
    with app_module.db_connect() as db:
        assert db.execute("SELECT 1 FROM team_members WHERE team_id=? AND name='Mira'", (team_id,)).fetchone()


def test_tasks_are_scoped_to_the_selected_team(client):
    with app_module.db_connect() as db:
        team_id = db.execute("SELECT id FROM teams WHERE name='Student Studio'").fetchone()['id']
    client.post('/teams', data={'name': 'Debate Club', 'description': 'Tournament prep', 'starter': 'Lee'})
    with app_module.db_connect() as db:
        other_team_id = db.execute("SELECT id FROM teams WHERE name='Debate Club'").fetchone()['id']
    response = client.post('/tasks', data={
        'title': 'Prepare opening statement', 'project': 'Tournament', 'owner': 'Lee',
        'status': 'Not started', 'priority': 'High', 'team_id': str(other_team_id)
    })
    assert response.status_code == 302
    other_page = client.get(f'/?team={other_team_id}')
    assert other_page.status_code == 200
    assert b'Prepare opening statement' in other_page.data
    assert b'Finish the science fair slides' not in other_page.data
    default_page = client.get(f'/?team={team_id}')
    assert b'Finish the science fair slides' in default_page.data
    assert b'Prepare opening statement' not in default_page.data
