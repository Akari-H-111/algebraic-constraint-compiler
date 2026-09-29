"""A family proof notebook: verified results that persist across sessions.

Only bundles that pass the independent verifier are stored. Stored verdicts
are never trusted on the way out: progress reports replay every stored bundle
again before counting it. Standard-library sqlite3; one local file.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading

from .ir import InputError, canonical, require
from .linear_verifier import verify_bundle

NAME = re.compile(r"^[a-z0-9][a-z0-9 _.-]{0,39}$")
MAX_ENTRIES = 500
MAX_BUNDLE_BYTES = 200_000
SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook TEXT NOT NULL,
    learner TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    task TEXT NOT NULL,
    claim TEXT NOT NULL,
    verdict TEXT NOT NULL,
    title TEXT NOT NULL,
    hint TEXT,
    certificate_sha256 TEXT NOT NULL,
    bundle TEXT NOT NULL,
    UNIQUE (notebook, learner, certificate_sha256)
);
CREATE INDEX IF NOT EXISTS entries_by_notebook ON entries (notebook, learner, created_at);
"""


def default_path():
    configured = os.environ.get("SYW_NOTEBOOK_PATH")
    if configured:
        return configured
    return str(Path.home() / ".show-your-work" / "notebook.sqlite3")


def clean_name(value, what="notebook", optional=False):
    if optional and (value is None or value == ""):
        return ""
    require(type(value) is str, "INVALID_SCHEMA", f"{what} must be a short name")
    name = " ".join(value.strip().lower().split())
    require(NAME.match(name) is not None, "INVALID_SCHEMA",
            f"{what} must be 1-40 letters, digits, spaces, '.', '_' or '-'")
    return name


class Notebook:
    def __init__(self, path=None):
        self.path = path or default_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()

    def save(self, notebook, bundle, verdict, title, learner="", hint=None, now=None):
        """Store a bundle only after the independent verifier accepts it."""
        notebook, learner = clean_name(notebook), clean_name(learner, "learner", optional=True)
        encoded = canonical(bundle)
        require(len(encoded) <= MAX_BUNDLE_BYTES, "RESOURCE_LIMIT", "Bundle too large for the notebook")
        report = verify_bundle(bundle)
        require(report["certificate_verified"], "UNVERIFIED_BUNDLE", "Only independently verified results are saved")
        cert = bundle["certificate"]
        created = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        with self._lock:
            count = self._db.execute("SELECT COUNT(*) FROM entries WHERE notebook = ?", (notebook,)).fetchone()[0]
            require(count < MAX_ENTRIES, "RESOURCE_LIMIT", "Notebook is full")
            self._db.execute(
                "INSERT OR IGNORE INTO entries (notebook, learner, created_at, task, claim, verdict, title, hint,"
                " certificate_sha256, bundle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (notebook, learner, created, cert["task"], cert["claim"], verdict, title[:200], hint,
                 cert["certificate_sha256"], encoded.decode("utf-8")))
            self._db.commit()
            row = self._db.execute(
                "SELECT id, created_at FROM entries WHERE notebook = ? AND learner = ? AND certificate_sha256 = ?",
                (notebook, learner, cert["certificate_sha256"])).fetchone()
        return {"entry_id": row["id"], "notebook": notebook, "learner": learner, "saved_at": row["created_at"],
                "certificate_sha256": cert["certificate_sha256"]}

    def _rows(self, notebook, learner=None, since=None, limit=50):
        query = "SELECT * FROM entries WHERE notebook = ?"
        params = [clean_name(notebook)]
        if learner:
            query += " AND learner = ?"
            params.append(clean_name(learner, "learner"))
        if since:
            query += " AND created_at >= ?"
            params.append(since.isoformat(timespec="seconds"))
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), MAX_ENTRIES)))
        with self._lock:
            return self._db.execute(query, params).fetchall()

    def history(self, notebook, learner=None, limit=10):
        return [{"entry_id": r["id"], "learner": r["learner"], "saved_at": r["created_at"], "task": r["task"],
                 "verdict": r["verdict"], "title": r["title"], "hint": r["hint"],
                 "certificate_sha256": r["certificate_sha256"]}
                for r in self._rows(notebook, learner, limit=limit)]

    def bundle(self, notebook, entry_id):
        with self._lock:
            row = self._db.execute("SELECT bundle FROM entries WHERE notebook = ? AND id = ?",
                                   (clean_name(notebook), int(entry_id))).fetchone()
        require(row is not None, "NOT_FOUND", "No such notebook entry")
        return json.loads(row["bundle"])

    def progress(self, notebook, learner=None, days=7, now=None):
        """Summarize recent work, replaying every stored certificate first."""
        require(type(days) is int and 1 <= days <= 365, "INVALID_SCHEMA", "days must be 1-365")
        since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        rows = self._rows(notebook, learner, since=since, limit=MAX_ENTRIES)
        replayed, failed, verdicts, hints, learners = 0, [], {}, {}, {}
        for r in rows:
            report = verify_bundle(json.loads(r["bundle"]))
            if not report["certificate_verified"]:
                failed.append(r["id"])
                continue
            replayed += 1
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
            learners[r["learner"] or "(family)"] = learners.get(r["learner"] or "(family)", 0) + 1
            if r["hint"]:
                hints[r["hint"]] = hints.get(r["hint"], 0) + 1
        return {"notebook": clean_name(notebook), "learner": clean_name(learner, "learner", optional=True),
                "days": days, "entries": len(rows), "replayed_and_verified": replayed,
                "failed_replay": failed, "verdicts": verdicts, "hint_patterns": hints, "by_learner": learners,
                "latest": [{"saved_at": r["created_at"], "verdict": r["verdict"], "title": r["title"]}
                           for r in rows[:5]]}


_shared = None
_shared_lock = threading.Lock()


def shared():
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Notebook()
        return _shared


def reset_shared(path=None):
    """Tests and servers may point the process-wide notebook at another file."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
        _shared = Notebook(path) if path else None
        return _shared


__all__ = ["Notebook", "InputError", "clean_name", "shared", "reset_shared"]
