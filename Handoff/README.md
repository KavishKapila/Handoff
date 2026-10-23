# Handoff — Keep the context moving

**A lightweight team handoff board for student projects.** Handoff helps students answer three questions before passing work along: what changed, what happens next, and what's in the way?

## Run locally

### Windows
1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During setup, enable **Add Python to PATH**.
2. Extract the ZIP.
3. Double-click `run_windows.bat`. It creates a local virtual environment, installs Flask, and starts the app.
4. Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Keep the terminal window open while using it.

### macOS / Linux
Run these commands in the extracted project folder:

```bash
chmod +x run_mac_linux.sh
./run_mac_linux.sh
```

Or start manually:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

The local SQLite database `handoff.db` is created automatically and seeded with example tasks on first launch. Use **Reset demo data** in the dashboard to restore the sample workspace. This clears the current local tasks and handoffs, so export anything you want to keep first.

## What works

- **Team system:** create multiple teams with descriptions, add/remove roster members, assign a team lead or member role, and open a team workspace with its members, tasks, and recent handoffs. The starter “Student Studio” team is created automatically; new teams can be created from **Teams** in the navigation.
- **Team-scoped work:** assign tasks and handoffs to teams, filter the main board by team, and keep work separated across class projects, clubs, and hackathon groups. Existing data is migrated into the starter team without deleting it.
- **Shared task board:** create, edit, delete, reprioritize, assign, and update tasks; validate dates and preserve existing handoffs when a task is deleted.
- **Search and filters:** find tasks by title, project, owner, description, status, and project. Undated tasks sort after dated tasks at the same priority.
- **Structured handoffs:** capture completed work, concrete next steps, blockers, links, sender, required recipient, and an optional deadline.
- **Pick-up receipt:** the receiver opens a full briefing and explicitly accepts responsibility. Acceptance updates the linked task owner, records the receiver and time, and adds an event-trail entry.
- **Context-gap checker:** an offline, rule-based checklist flags common omissions and vague phrases while writing. It is not AI and cannot verify whether claims are true.
- **Full briefing page:** read the complete handoff, ask a question, review the event trail, print a one-page briefing, or export Markdown including acceptance details and events.
- **Smart draft assistant:** creates an editable scaffold using task context. It runs locally with no API key; if configured, it can use an OpenAI-compatible chat-completions API. Provider failures fall back to local drafting.
- **Live readiness meter and readable mode:** checklist for done/next/blockers/links, larger text option, stronger contrast, keyboard focus indicators, modal keyboard support, and responsive layouts.
- **Real dashboard metric:** “Context gaps flagged” sums the gap counts stored when handoffs were sent; it is a count of flagged gaps, not a claim that every gap was fixed.
- **Demo reset:** restore the sample workspace when you need a clean demo. Normal restarts do not resurrect deleted sample data.
- **Local safety checks:** local Host/Origin guard, bounded request size, server-side validation, missing-record handling, random per-install Flask secret, and SQLite schema migration to version 3, including teams and team membership.

## Optional generative AI setup

Handoff is useful without a model key. The built-in no-key fallback produces a contextual scaffold and asks the user to verify unknown details. To enable model-generated drafts, copy `.env.example` to `.env` in the project folder, then add your own key and model settings. The app reads `.env` automatically; environment variables already set by your shell take precedence:

```text
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

Use a compatible provider's own base URL and model name if appropriate. Keep keys in environment variables—not in source code, the ZIP, screenshots, or Git. Requests send the selected task fields to the configured AI provider. Review its privacy/retention terms before using real team data. When the provider errors or times out, Handoff falls back to its local scaffold.

## Project structure

```text
Handoff/
├── app.py                 # Flask routes, SQLite persistence, migrations, optional AI provider
├── handoff_lint.py        # Offline, deterministic context-gap checks
├── requirements.txt       # Runtime dependency list
├── requirements-dev.txt   # Optional pytest dependency
├── templates/index.html   # Server-rendered dashboard
├── templates/handoff.html # Full briefing, pickup receipt, question trail, print view
├── templates/task_edit.html # Task editing screen
├── templates/teams.html     # Team directory and team creation
├── templates/team_detail.html # Team roster, tasks, and handoffs
├── static/style.css       # Responsive visual system
├── static/app.js          # Modal behavior, draft assistant, live context check
├── run_windows.bat        # Windows quick start
├── run_mac_linux.sh       # macOS/Linux quick start
├── .env.example           # Optional configuration template (no real secrets)
└── SUBMISSION.md          # Devpost-ready story, demo script, disclosures
```

## Important limitations

- This is a local hackathon prototype, not a hosted multi-user collaboration service. Teams and rosters organize local data, but member names are labels only: no invitation emails, login accounts, role-based access control, real-time sync, or notifications are implemented. All teams share one local SQLite database on the machine running the app.
- The included workspace and people are illustrative demo data, not real collaborators.
- The local draft engine is a rule-based scaffold, not an LLM. Generative drafting is optional and requires the user's own compatible API credentials.
- The app includes accessibility-oriented improvements but has not undergone a formal screen-reader or WCAG conformance audit. Browser-based keyboard, mobile, and contrast verification still needs to be completed manually.
- The development server is intended for local demos only. For production deployment, use a production WSGI server, HTTPS, authentication, CSRF protection, managed secrets, access controls, and a persistent database strategy.

## Automated smoke tests

With the app dependencies installed, run:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

The tests cover dashboard rendering, task creation/search/editing, team creation and roster management, team assignment and deletion behavior, handoff save/export, no-key drafting, pickup acceptance and ownership transfer, event trail, context-gap checks, invalid IDs/dates, and cross-origin blocking. The tests still need to be executed with real pytest on a machine with Flask and pytest installed.

## Verify it

Open `http://127.0.0.1:5000/health` after launch. A healthy response looks like:

```json
{"ai":"local-fallback","app":"Handoff","ok":true}
```

The AI field reports `configured` if `OPENAI_API_KEY` is set; a provider error uses the local fallback for that draft.
