"""Append-only, hash-chained audit trail.

Each row's hash covers the previous row's hash plus its own content, so changing any
past row breaks every hash after it. Triggers in schema.sql block UPDATE and DELETE.
"""
import hashlib
import json

from .db import now

GENESIS = "0" * 64


def _digest(prev_hash: str, ts: str, actor: str, action: str, entity: str, detail: str) -> str:
    body = json.dumps([prev_hash, ts, actor, action, entity, detail], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def record(conn, actor: str, action: str, entity: str, detail: dict) -> None:
    """Call inside an open transaction."""
    row = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    ts = now()
    detail_s = json.dumps(detail, sort_keys=True)
    h = _digest(prev, ts, actor, action, entity, detail_s)
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, entity, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
        (ts, actor, action, entity, detail_s, prev, h),
    )


def verify(conn) -> dict:
    prev = GENESIS
    count = 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
        expected = _digest(prev, r["ts"], r["actor"], r["action"], r["entity"], r["detail"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"valid": False, "rows_checked": count, "broken_at_seq": r["seq"]}
        prev = r["hash"]
        count += 1
    return {"valid": True, "rows_checked": count, "broken_at_seq": None}
