#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import subprocess
import sys

DB = os.path.expanduser("~/.secondbrain/brain.db")
MODEL = "phi4-mini:latest"
CONF_THRESHOLD = 0.60
MAX_RETRIES = 2

ALIASES_PATH = os.path.expanduser("~/.secondbrain/aliases.json")


def load_aliases():
    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k.lower(): v for k, v in data.items()}
    except Exception:
        return {}


ALIASES = load_aliases()


def infer_person_name(raw: str) -> str:
    s = raw.strip().lower()
    m = re.search(r"\b(mom|mother|dad|father)\b", s)
    if m:
        key = m.group(1)
        return ALIASES.get(key, key.title())
    return ""


PROMPT = r"""
You are a strict JSON generator for a personal second-brain sorter.

Task: classify the user text into one of: people, projects, ideas, admin.
Then extract the relevant fields for that category.

Return ONLY valid JSON. No markdown. No commentary.

Schema:
{
  "category": "people|projects|ideas|admin|unknown",
  "confidence": 0.0-1.0,
  "fields": { ...category-specific fields... },
  "title": "short human-friendly name"
}

Category field rules:

people.fields:
- name: string (required; if missing -> category "unknown" with low confidence)
- context: string (optional)
- follow_up: string (optional; specific next follow-up)
- last_contact: string (optional; ISO date YYYY-MM-DD if clearly present)

projects.fields:
- name: string (required)
- status: active|waiting|blocked|someday|done (default active)
- next_action: string (optional but preferred; must be concrete if possible)
- notes: string (optional)

ideas.fields:
- title: string (required)
- one_liner: string (optional; <= 25 words)
- notes: string (optional)

admin.fields:
- task: string (required)
- due_date: string (optional; ISO date YYYY-MM-DD if clear)
- status: open|done (default open)

If uncertain, set category="unknown" and confidence <= 0.50.

User text:
"""


def extract_json(s: str):
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass

    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except Exception:
        return None


def ollama_call(prompt: str) -> str:
    p = subprocess.run(
        ["ollama", "run", MODEL],
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    out = p.stdout.decode("utf-8", errors="replace")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ollama failed: {err.strip()}")
    return out.strip()


def log_event(cur, event, inbox_id=None, details=""):
    cur.execute(
        "INSERT INTO log_events (event, inbox_id, details) VALUES (?, ?, ?)",
        (event, inbox_id, (details or "")[:2000]),
    )


def pre_route(raw: str):
    """
    Deterministic prefix router. Returns (category, clean_text) or (None, None).
    Supports: admin:, project(s):, idea(s):, person/people:
    """
    s = raw.strip()
    low = s.lower()

    def strip_prefix(pfx: str) -> str:
        return s[len(pfx):].strip()

    # admin
    if low.startswith("admin:"):
        return "admin", strip_prefix("admin:")

    # projects
    if low.startswith("project:"):
        return "projects", strip_prefix("project:")
    if low.startswith("projects:"):
        return "projects", strip_prefix("projects:")

    # ideas
    if low.startswith("idea:"):
        return "ideas", strip_prefix("idea:")
    if low.startswith("ideas:"):
        return "ideas", strip_prefix("ideas:")

    # people
    if low.startswith("person:"):
        return "people", strip_prefix("person:")
    if low.startswith("people:"):
        return "people", strip_prefix("people:")

    return None, None


def main(limit=10):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(
        "SELECT id, raw_text FROM inbox WHERE status='unprocessed' ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        return 0

    for r in rows:
        inbox_id = r["id"]
        raw = r["raw_text"]

        # ---------- PREFIX PRE-ROUTER ----------
        cat, clean = pre_route(raw)
        if cat:
            # If user wrote only "Admin:" with nothing else, bounce.
            if not clean:
                cur.execute(
                    "UPDATE inbox SET status='needs_review', category=?, confidence=?, model=?, error=? WHERE id=?",
                    (cat, 1.0, "prefix", "empty after prefix", inbox_id),
                )
                log_event(cur, "needs_review", inbox_id, f"prefix {cat} but empty")
                con.commit()
                continue

            if cat == "admin":
                cur.execute(
                    "INSERT INTO admin (task, due_date, status) VALUES (?, ?, ?)",
                    (clean, "", "open"),
                )

            elif cat == "projects":
                cur.execute(
                    "INSERT INTO projects (name, status, next_action, notes, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                    (clean, "active", "", "",),
                )

            elif cat == "ideas":
                cur.execute(
                    "INSERT INTO ideas (title, one_liner, notes) VALUES (?, ?, ?)",
                    (clean, "", ""),
                )

            elif cat == "people":
                # Allow "person: mom" to resolve via alias file
                name = clean.strip()
                if name.lower() in ALIASES:
                    name = ALIASES[name.lower()]
                elif name.lower() in ("mom", "mother", "dad", "father"):
                    name = infer_person_name(name) or name.title()

                if not name:
                    cur.execute(
                        "UPDATE inbox SET status='needs_review', category='people', confidence=?, model=?, error=? WHERE id=?",
                        (1.0, "prefix", "missing person name", inbox_id),
                    )
                    log_event(cur, "needs_review", inbox_id, "missing person name (prefix)")
                    con.commit()
                    continue

                # Store the *original* raw as follow-up context by default
                cur.execute(
                    "INSERT INTO people (name, context, follow_up, last_contact, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                    (name, "", raw, "",),
                )

            cur.execute(
                "UPDATE inbox SET status='processed', category=?, confidence=?, model=?, error='' WHERE id=?",
                (cat, 1.0, "prefix", inbox_id),
            )
            log_event(cur, "processed", inbox_id, f"prefix_route -> {cat}")
            con.commit()
            continue
        # ---------- END PREFIX PRE-ROUTER ----------

        # ---------- LLM ROUTING FALLBACK ----------
        result = None
        last_err = ""

        for attempt in range(MAX_RETRIES + 1):
            try:
                out = ollama_call(PROMPT + raw)
                parsed = extract_json(out)
                if parsed and isinstance(parsed, dict):
                    result = parsed
                    break
                last_err = f"invalid json (attempt {attempt})"
            except Exception as e:
                last_err = str(e)

        if not result:
            cur.execute(
                "UPDATE inbox SET status='needs_review', error=?, model=? WHERE id=?",
                (last_err, MODEL, inbox_id),
            )
            log_event(cur, "needs_review", inbox_id, last_err)
            con.commit()
            continue

        category = result.get("category", "unknown")
        confidence = float(result.get("confidence", 0.0) or 0.0)
        fields = result.get("fields", {}) or {}

        if category not in ("people", "projects", "ideas", "admin") or confidence < CONF_THRESHOLD:
            cur.execute(
                "UPDATE inbox SET status='needs_review', category=?, confidence=?, model=?, error=? WHERE id=?",
                (
                    "unknown"
                    if category not in ("people", "projects", "ideas", "admin")
                    else category,
                    confidence,
                    MODEL,
                    "",
                    inbox_id,
                ),
            )
            log_event(cur, "needs_review", inbox_id, json.dumps(result)[:2000])
            con.commit()
            continue

        if category == "people":
            name = (fields.get("name") or "").strip()
            if not name:
                name = infer_person_name(raw)

            if not name:
                cur.execute(
                    "UPDATE inbox SET status='needs_review', category='people', confidence=?, model=?, error=? WHERE id=?",
                    (confidence, MODEL, "missing person name", inbox_id),
                )
                log_event(cur, "needs_review", inbox_id, "missing person name")
                con.commit()
                continue

            cur.execute(
                "INSERT INTO people (name, context, follow_up, last_contact, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (name, fields.get("context", ""), fields.get("follow_up", ""), fields.get("last_contact", "")),
            )

        elif category == "projects":
            name = (fields.get("name") or "").strip()
            if not name:
                cur.execute(
                    "UPDATE inbox SET status='needs_review', category='projects', confidence=?, model=?, error=? WHERE id=?",
                    (confidence, MODEL, "missing project name", inbox_id),
                )
                log_event(cur, "needs_review", inbox_id, "missing project name")
                con.commit()
                continue

            status = (fields.get("status") or "active").strip()
            if status not in ("active", "waiting", "blocked", "someday", "done"):
                status = "active"

            cur.execute(
                "INSERT INTO projects (name, status, next_action, notes, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (name, status, fields.get("next_action", ""), fields.get("notes", "")),
            )

        elif category == "ideas":
            title = (fields.get("title") or result.get("title") or "").strip()
            if not title:
                cur.execute(
                    "UPDATE inbox SET status='needs_review', category='ideas', confidence=?, model=?, error=? WHERE id=?",
                    (confidence, MODEL, "missing idea title", inbox_id),
                )
                log_event(cur, "needs_review", inbox_id, "missing idea title")
                con.commit()
                continue

            cur.execute(
                "INSERT INTO ideas (title, one_liner, notes) VALUES (?, ?, ?)",
                (title, fields.get("one_liner", ""), fields.get("notes", "")),
            )

        elif category == "admin":
            task = (fields.get("task") or "").strip()
            if not task:
                cur.execute(
                    "UPDATE inbox SET status='needs_review', category='admin', confidence=?, model=?, error=? WHERE id=?",
                    (confidence, MODEL, "missing admin task", inbox_id),
                )
                log_event(cur, "needs_review", inbox_id, "missing admin task")
                con.commit()
                continue

            status = (fields.get("status") or "open").strip()
            if status not in ("open", "done"):
                status = "open"

            cur.execute(
                "INSERT INTO admin (task, due_date, status) VALUES (?, ?, ?)",
                (task, fields.get("due_date", ""), status),
            )

        cur.execute(
            "UPDATE inbox SET status='processed', category=?, confidence=?, model=?, error='' WHERE id=?",
            (category, confidence, MODEL, inbox_id),
        )
        log_event(cur, "processed", inbox_id, json.dumps(result)[:2000])
        con.commit()

    return 0


if __name__ == "__main__":
    limit = 10
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    raise SystemExit(main(limit))

