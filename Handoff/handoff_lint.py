"""Deterministic, offline checks for missing handoff context. Not AI."""
import re

VAGUE_PHRASES = (
    "as discussed", "the file", "the thing", "etc.", "asap", "soon", "do the needful",
    "finish it", "fix this", "handle this", "you know", "same as before",
)
ACTION_VERBS = re.compile(r"\b(add|write|review|send|check|create|update|test|compare|ask|confirm|open|publish|submit|remove|run|share|finish|collect|choose|document|replace|verify|fix|schedule|attach|upload|download|merge|deploy)\b", re.I)
URL_RE = re.compile(r"https?://\S+", re.I)


def check(fields):
    """Return actionable context gaps without claiming to understand project truth."""
    gaps = []
    completed = str(fields.get("completed", "")).strip()
    next_steps = str(fields.get("next_steps", "")).strip()
    blockers = str(fields.get("blockers", "")).strip()
    links = str(fields.get("links", "")).strip()
    recipient = str(fields.get("to_person", "")).strip()
    all_text = " ".join((completed, next_steps, blockers)).lower()

    if not recipient:
        gaps.append({"code": "recipient", "title": "Name the receiver", "detail": "A handoff needs a clear person who can pick it up."})
    if len(completed) < 25:
        gaps.append({"code": "completed_short", "title": "Add more completed-work context", "detail": "Explain what changed, what is ready, or where the work currently stands."})
    if len(next_steps) < 20 or not ACTION_VERBS.search(next_steps):
        gaps.append({"code": "next_action", "title": "Make the next step actionable", "detail": "Start with a concrete action such as review, add, test, send, or confirm."})
    if not blockers:
        gaps.append({"code": "blockers", "title": "Clarify blockers", "detail": "Describe what is blocking progress, or explicitly write “None”."})
    for phrase in VAGUE_PHRASES:
        if phrase in all_text:
            gaps.append({"code": "vague:" + phrase, "title": f"Replace vague phrase: “{phrase}”", "detail": "Replace it with a specific file, decision, person, or action."})
            break
    if re.search(r"\b(doc|document|drive|folder|link|slides|spreadsheet|repo|repository)\b", all_text, re.I) and not links and not URL_RE.search(all_text):
        gaps.append({"code": "missing_link", "title": "Add a file or location", "detail": "You mention a resource; include its link or explain where to find it."})
    dependency = re.search(r"\b(waiting on|blocked by|depends on|need .{0,30}from|awaiting)\b", blockers, re.I)
    named_contact = re.search(r"\b(?:from|by|on)\s+(?:the\s+)?[A-Z][a-z]{2,}\b", blockers)
    if dependency and not named_contact:
        gaps.append({"code": "blocker_owner", "title": "Identify who can unblock this", "detail": "If another person owns the dependency, name them or explain how to reach them."})
    return gaps
