# Devpost submission starter — Handoff

## Project name
**Handoff — Keep the context moving**

## Team members
Add every registered team member and their role here before submitting.

## One-line pitch
A clearer way for student teams to pass work along without losing the decisions, files, blockers, or next steps that make work possible.

## Inspiration
Group projects rarely break down only because people don't work. They break down in the gaps between work sessions: a teammate leaves, a class ends, a task changes hands, and the next person has to reconstruct the context from scattered messages. Handoff is designed around that small but expensive moment.

## What it does
Handoff pairs a lightweight task board with a simple team system and structured handoff log. Create separate teams for classes, clubs, or hackathon projects; add a local roster; and connect tasks and handoffs to the right team. A person records what's done, the next actions, blockers, links, a recipient, and an optional deadline. A rule-based context-gap checker flags common omissions before the handoff is sent. The recipient opens a full briefing, can ask a question, and explicitly accepts the handoff. That pickup receipt updates the linked task owner and records who accepted and when. Every question and transfer is recorded in an event trail. The briefing can be printed or exported as portable Markdown. The smart-draft button uses task context to create an editable starting point, with a no-key fallback so the core experience does not depend on a paid AI service. A Readable Mode toggle increases type size and contrast for users who want a roomier interface.

## Who it's for
Student teams doing presentations, research assignments, clubs, and hackathon projects—especially teams working asynchronously or splitting work across busy schedules.

## The barrier
**Lost context during task handoff.** Existing task lists often show ownership and status, but don't consistently capture the “what changed / what next / what's blocking us?” information the next person needs.

## How to try it
1. Run the project locally using the included quick-start script.
2. Open **Teams**, create a team for your own group, add a team lead and members, then open its workspace.
3. On the Overview page, inspect the seeded Science fair and History project examples; try the team, status, and project filters.
4. Open the seeded “Finish the science fair slides” handoff and inspect its full briefing, context-gap check, and event trail.
5. Create a handoff for a task that does not already have a pending handoff. Assign a recipient, use **Draft with smart assist** if helpful, and watch the rule-based context checker update while typing.
6. Open the briefing, ask a question, then accept pickup. Confirm that the task owner changes and the event trail records the receipt.
7. Print the briefing or export Markdown. Use **Reset demo data** to restore a clean walkthrough.

## Tools and implementation
- Python and Flask for the server-side app and routes.
- SQLite for local persistence.
- Jinja templates, vanilla JavaScript, and custom responsive CSS for the interface.
- An optional OpenAI-compatible chat-completions endpoint for generative drafts; built-in rule-based fallback if no API key is configured or the provider is unavailable.

## What's original / implementation notes
The project focuses on the transition between people rather than only tracking the task. Its signature is a two-sided pickup receipt: a handoff stays pending until a named receiver accepts it, which changes linked task ownership and creates an auditable local event. A deterministic context-gap checker helps catch vague instructions without requiring a paid AI service. The task board and handoff history are connected through SQLite, so a saved handoff references its task when applicable. A versioned migration adds teams and rosters while preserving existing tasks and handoffs. A device-local Readable Mode preference supports larger type and stronger contrast.

## What we learned
A handoff isn't just a status update. Useful context has a small structure: what changed, the next concrete action, and any blocker or dependency. A draft assistant is safest when it helps with wording but leaves the final truth-check to the person who did the work.

## Limitations and next steps
This prototype runs locally. Teams and rosters organize work but member names are labels only; there are no login accounts, invitation emails, role-based permissions, live sync, notifications, or multi-user hosting. The local draft assistant is rule-based; optional model-generated drafts require a separately configured API key. Next steps would include authenticated team invitations and membership, shared hosted storage, notification integrations, keyboard/screen-reader usability testing, and privacy-preserving analytics for handoff completion.

## AI assistance and pre-existing work disclosure
AI assistance was used to help generate and refine code, UI copy, and submission documentation for this prototype. The optional AI drafting feature is clearly separated from the local rule-based fallback. No third-party project code was intentionally copied into this submission. List any additional libraries, assets, or pre-existing work you personally add before submitting, and follow the hackathon's disclosure rules.

## Suggested demo video narration (about 75 seconds)
“Meet Handoff. In student projects, a task can be technically assigned and still be hard to continue because the context lives in someone's head or chat history. Here is our Science fair task board. I can see that slide work is in progress and citations are blocked. Instead of sending a vague ‘here you go,’ I create a structured handoff. Handoff can draft a starting point from task context, but the person reviews and edits every field. The note captures what is done, the next steps, and the blocker. Once saved, the named receiver accepts the handoff; Handoff records who accepted, updates task ownership, and keeps the event trail. The whole briefing can be printed or exported as Markdown to work with the team's existing tools. Handoff isn't trying to become another giant project-management suite. It makes one overlooked moment—the transfer of context—easier, clearer, and more accessible.”

## Short description
Handoff helps student teams pass work along without losing context. Track a task, capture what's done and what's next, surface blockers, and export a tidy handoff for the next teammate. A no-key smart-draft fallback keeps the core flow usable, with optional generative AI when configured.

## Suggested tags
Productivity, Accessibility, Collaboration, Education, Student Tools, Task Management, Workflow, AI, Open Source
