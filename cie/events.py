"""Outbox events and the worker that consumes them.

A system of record writes its change and its event in one transaction (the outbox pattern),
so an event is never lost and never sent for a change that rolled back. The worker is:
  - idempotent: each event id is recorded in processed_event; a redelivered event is skipped
  - retrying: a failing event is retried up to MAX_ATTEMPTS, then parked in dead_letter
In production the outbox would be relayed to SQS / EventBridge; the consumer logic stays the same.
"""
import json
import uuid
from typing import Callable

from . import audit
from .db import now, tx

MAX_ATTEMPTS = 3

HANDLERS: dict[str, Callable] = {}


def handler(event_type: str):
    def register(fn):
        HANDLERS[event_type] = fn
        return fn
    return register


def emit(conn, event_type: str, payload: dict) -> str:
    """Call inside the caller's transaction."""
    event_id = str(uuid.uuid4())
    conn.execute("INSERT INTO outbox (id, event_type, payload, created_at) VALUES (?,?,?,?)",
                 (event_id, event_type, json.dumps(payload), now()))
    return event_id


def process_pending(conn, limit: int = 100) -> dict:
    stats = {"processed": 0, "skipped_duplicate": 0, "failed": 0, "dead_lettered": 0}
    pending = conn.execute(
        "SELECT * FROM outbox WHERE status = 'pending' ORDER BY created_at, rowid LIMIT ?", (limit,)).fetchall()
    for ev in pending:
        try:
            with tx(conn):
                if conn.execute("SELECT 1 FROM processed_event WHERE event_id = ?", (ev["id"],)).fetchone():
                    conn.execute("UPDATE outbox SET status = 'done' WHERE id = ?", (ev["id"],))
                    stats["skipped_duplicate"] += 1
                    continue
                fn = HANDLERS.get(ev["event_type"])
                if fn is None:
                    raise LookupError(f"no handler for {ev['event_type']}")
                fn(conn, json.loads(ev["payload"]))
                conn.execute("INSERT INTO processed_event (event_id, processed_at) VALUES (?,?)", (ev["id"], now()))
                conn.execute("UPDATE outbox SET status = 'done' WHERE id = ?", (ev["id"],))
                stats["processed"] += 1
        except Exception as exc:  # the handler's work rolled back; record the failure separately
            with tx(conn):
                attempts = ev["attempts"] + 1
                if attempts >= MAX_ATTEMPTS:
                    conn.execute("UPDATE outbox SET status = 'dead', attempts = ?, last_error = ? WHERE id = ?",
                                 (attempts, str(exc), ev["id"]))
                    conn.execute("INSERT OR REPLACE INTO dead_letter (event_id, error, failed_at) VALUES (?,?,?)",
                                 (ev["id"], str(exc), now()))
                    audit.record(conn, "worker", "event.dead_lettered", ev["id"],
                                 {"event_type": ev["event_type"], "error": str(exc)})
                    stats["dead_lettered"] += 1
                else:
                    conn.execute("UPDATE outbox SET attempts = ?, last_error = ? WHERE id = ?",
                                 (attempts, str(exc), ev["id"]))
                    stats["failed"] += 1
    return stats


# ---- handlers ------------------------------------------------------------------

@handler("change.submitted")
def _on_change_submitted(conn, payload):
    from .changes import assign_impact
    assign_impact(conn, payload["change_id"])


@handler("item.changed")
def _on_item_changed(conn, payload):
    """A system of record revised an item: open a change request automatically, so nothing
    changes in the thread without an impact review."""
    from .changes import assign_impact, submit
    change_id = submit(conn, payload["item_id"],
                       f"Detected revision {payload['revision']} in {payload['system']}: {payload['summary']}",
                       payload.get("changed_by", "system"), source="detected")
    # assess immediately; the change.submitted event this emits will find it already in review and no-op
    assign_impact(conn, change_id)
