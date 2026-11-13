"""Exception queue: work that stalled or failed, made visible, owned and resolvable.

Three kinds, each opened automatically:
  dead_letter       an event failed every retry; Integration Support retries or dismisses it
  approval_overdue  a change has sat in review past REVIEW_SLA_DAYS; the Configuration Manager chases it
  untraced_change   a change targets an item with no trace links, so its impact list may be incomplete

At most one open exception per (kind, entity), enforced by a partial unique index. Overdue
exceptions close themselves once their change leaves review. Every open, reassign and resolve
goes to the audit trail.
"""
from datetime import datetime, timedelta

from . import audit
from .db import now

REVIEW_SLA_DAYS = 5
INTEGRATION_SUPPORT = "Integration Support"
CONFIG_MANAGER = "Configuration Manager"
OWNER = {"dead_letter": INTEGRATION_SUPPORT, "approval_overdue": CONFIG_MANAGER, "untraced_change": CONFIG_MANAGER}


class ExceptionError(ValueError):
    pass


def open_exception(conn, kind: str, entity: str, summary: str) -> int | None:
    """Call inside a transaction. Returns the new id, or None if one is already open."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO exception (kind, entity, summary, owner, opened_at) VALUES (?,?,?,?,?)",
        (kind, entity, summary, OWNER[kind], now()))
    if not cur.rowcount:
        return None
    audit.record(conn, "impact-engine", "exception.opened", f"EX-{cur.lastrowid}",
                 {"kind": kind, "entity": entity, "owner": OWNER[kind]})
    return cur.lastrowid


def _close(conn, ex_id: int, resolution: str, by: str, note: str) -> None:
    conn.execute("UPDATE exception SET state = 'resolved', resolution = ?, note = ?, resolved_by = ?, resolved_at = ? "
                 "WHERE id = ?", (resolution, note, by, now(), ex_id))
    audit.record(conn, by, f"exception.{resolution}", f"EX-{ex_id}", {"note": note})


def scan(conn, as_of: datetime | None = None) -> dict:
    """Open overdue-review exceptions and close the ones whose change has left review. Call inside a transaction."""
    as_of = as_of or datetime.fromisoformat(now())
    cutoff = (as_of - timedelta(days=REVIEW_SLA_DAYS)).isoformat(timespec="seconds")
    opened = 0
    # a dismissed overdue flag stays dismissed for that change; it doesn't reopen on the next pass
    for cr in conn.execute(
            "SELECT id, created_at FROM change_request c WHERE state = 'in_review' AND created_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM exception e WHERE e.kind = 'approval_overdue' "
            "AND e.entity = 'CR-' || c.id AND e.resolution = 'dismissed')", (cutoff,)).fetchall():
        waiting = [r["approver"] for r in conn.execute(
            "SELECT approver FROM approval WHERE change_id = ? AND decision IS NULL ORDER BY approver", (cr["id"],))]
        days = (as_of - datetime.fromisoformat(cr["created_at"])).days
        if open_exception(conn, "approval_overdue", f"CR-{cr['id']}",
                          f"In review {days} days (limit {REVIEW_SLA_DAYS}); waiting on {', '.join(waiting)}"):
            opened += 1
    closed = 0
    for ex in conn.execute(
            "SELECT e.id FROM exception e JOIN change_request c ON 'CR-' || c.id = e.entity "
            "WHERE e.kind = 'approval_overdue' AND e.state = 'open' AND c.state <> 'in_review'").fetchall():
        _close(conn, ex["id"], "auto_closed", "impact-engine", "change left review")
        closed += 1
    return {"opened": opened, "auto_closed": closed}


def get(conn, ex_id: int):
    row = conn.execute("SELECT * FROM exception WHERE id = ?", (ex_id,)).fetchone()
    if row is None:
        raise ExceptionError(f"no such exception EX-{ex_id}")
    return row


def assign(conn, ex_id: int, owner: str, by: str) -> None:
    """Hand an open exception to someone else. Call inside a transaction."""
    ex = get(conn, ex_id)
    if ex["state"] != "open":
        raise ExceptionError(f"EX-{ex_id} is already resolved")
    conn.execute("UPDATE exception SET owner = ? WHERE id = ?", (owner, ex_id))
    audit.record(conn, by, "exception.assigned", f"EX-{ex_id}", {"from": ex["owner"], "to": owner})


def resolve(conn, ex_id: int, action: str, by: str, note: str = "") -> None:
    """action 'retry' (dead letters only) requeues the event; 'dismiss' closes with a note. Call inside a transaction."""
    ex = get(conn, ex_id)
    if ex["state"] != "open":
        raise ExceptionError(f"EX-{ex_id} is already resolved")
    if action == "retry":
        if ex["kind"] != "dead_letter":
            raise ExceptionError("only a dead-lettered event can be retried")
        conn.execute("UPDATE outbox SET status = 'pending', attempts = 0, last_error = NULL WHERE id = ?", (ex["entity"],))
        conn.execute("DELETE FROM dead_letter WHERE event_id = ?", (ex["entity"],))
        _close(conn, ex_id, "retried", by, note)
    elif action == "dismiss":
        if not note.strip():
            raise ExceptionError("a dismissal needs a note saying why")
        _close(conn, ex_id, "dismissed", by, note)
    else:
        raise ExceptionError("action must be 'retry' or 'dismiss'")


def list_all(conn, state: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM exception", ()
    if state:
        q, args = q + " WHERE state = ?", (state,)
    return [dict(r) for r in conn.execute(q + " ORDER BY state = 'resolved', opened_at, id", args)]


def metrics(conn, as_of: datetime | None = None) -> dict:
    """Open count by kind, oldest open age, and median hours to resolve."""
    as_of = as_of or datetime.fromisoformat(now())
    rows = conn.execute("SELECT kind, state, opened_at, resolved_at FROM exception").fetchall()
    open_rows = [r for r in rows if r["state"] == "open"]
    hours = sorted((datetime.fromisoformat(r["resolved_at"]) - datetime.fromisoformat(r["opened_at"])).total_seconds()
                   / 3600 for r in rows if r["state"] == "resolved")
    median = None
    if hours:
        mid = len(hours) // 2
        median = hours[mid] if len(hours) % 2 else (hours[mid - 1] + hours[mid]) / 2
    return {
        "open": len(open_rows),
        "open_by_kind": {k: sum(1 for r in open_rows if r["kind"] == k) for k in OWNER},
        "oldest_open_hours": round(max(((as_of - datetime.fromisoformat(r["opened_at"])).total_seconds() / 3600
                                        for r in open_rows), default=0), 1) if open_rows else None,
        "resolved": len(hours),
        "median_hours_to_resolve": round(median, 1) if median is not None else None,
    }
