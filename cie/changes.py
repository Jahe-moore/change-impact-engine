"""Change requests: filing, impact assignment and approval decisions."""
from . import audit, impact
from .db import get_item, now
from .events import emit

CONFIG_MANAGER = "Configuration Manager"


class ChangeError(ValueError):
    pass


def submit(conn, target_id: str, description: str, requested_by: str, source: str = "manual") -> int:
    """File a change request. Call inside a transaction. Impact is computed later by the worker."""
    if get_item(conn, target_id) is None:
        raise ChangeError(f"no such item {target_id}")
    cur = conn.execute(
        "INSERT INTO change_request (target_id, description, requested_by, source, created_at) VALUES (?,?,?,?,?)",
        (target_id, description, requested_by, source, now()),
    )
    change_id = cur.lastrowid
    emit(conn, "change.submitted", {"change_id": change_id})
    audit.record(conn, requested_by, "change.submitted", f"CR-{change_id}",
                 {"target": target_id, "description": description, "source": source})
    return change_id


def assign_impact(conn, change_id: int) -> None:
    """Worker step: compute impact and route approvals. Call inside a transaction."""
    cr = conn.execute("SELECT * FROM change_request WHERE id = ?", (change_id,)).fetchone()
    if cr is None:
        raise ChangeError(f"no such change {change_id}")
    if cr["state"] != "submitted":
        return
    items = impact.analyze(conn, cr["target_id"])
    conn.executemany(
        "INSERT INTO impacted_item (change_id, item_id, system, depth, reason) VALUES (?,?,?,?,?)",
        [(change_id, i["item_id"], i["system"], i["depth"], i["reason"]) for i in items],
    )
    target_owner = get_item(conn, cr["target_id"])["owner"]
    approvers = sorted({target_owner, CONFIG_MANAGER, *(i["owner"] for i in items)})
    conn.executemany("INSERT INTO approval (change_id, approver) VALUES (?,?)", [(change_id, a) for a in approvers])
    conn.execute("UPDATE change_request SET state = 'in_review' WHERE id = ?", (change_id,))
    audit.record(conn, "impact-engine", "impact.assessed", f"CR-{change_id}",
                 {"impacted": [i["item_id"] for i in items], "approvers": approvers})


def decide(conn, change_id: int, approver: str, decision: str, comment: str = "") -> str:
    """Record one approver's decision. Returns the change's new state. Call inside a transaction."""
    if decision not in ("approved", "rejected"):
        raise ChangeError("decision must be 'approved' or 'rejected'")
    cr = conn.execute("SELECT state FROM change_request WHERE id = ?", (change_id,)).fetchone()
    if cr is None:
        raise ChangeError(f"no such change {change_id}")
    if cr["state"] != "in_review":
        raise ChangeError(f"change is {cr['state']}, not in review")
    row = conn.execute("SELECT decision FROM approval WHERE change_id = ? AND approver = ?",
                       (change_id, approver)).fetchone()
    if row is None:
        raise ChangeError(f"{approver} is not an approver on CR-{change_id}")
    if row["decision"] is not None:
        raise ChangeError(f"{approver} already decided")
    conn.execute("UPDATE approval SET decision = ?, comment = ?, decided_at = ? WHERE change_id = ? AND approver = ?",
                 (decision, comment, now(), change_id, approver))
    audit.record(conn, approver, f"change.{decision}", f"CR-{change_id}", {"comment": comment})

    decisions = [r["decision"] for r in conn.execute("SELECT decision FROM approval WHERE change_id = ?", (change_id,))]
    state = "in_review"
    if "rejected" in decisions:
        state = "rejected"
    elif all(d == "approved" for d in decisions):
        state = "approved"
    if state != "in_review":
        conn.execute("UPDATE change_request SET state = ? WHERE id = ?", (state, change_id))
        audit.record(conn, "impact-engine", f"change.closed.{state}", f"CR-{change_id}", {})
    return state


def detail(conn, change_id: int) -> dict | None:
    cr = conn.execute("SELECT * FROM change_request WHERE id = ?", (change_id,)).fetchone()
    if cr is None:
        return None
    target = get_item(conn, cr["target_id"])
    impacted = [dict(r) for r in conn.execute(
        "SELECT ii.item_id, ii.system, ii.depth, ii.reason, it.title, it.owner "
        "FROM impacted_item ii JOIN item it ON it.id = ii.item_id "
        "WHERE ii.change_id = ? ORDER BY ii.system, ii.depth, ii.item_id", (change_id,))]
    approvals = [dict(r) for r in conn.execute(
        "SELECT approver, decision, comment, decided_at FROM approval WHERE change_id = ? ORDER BY approver",
        (change_id,))]
    return {
        **dict(cr),
        "target": dict(target),
        "impacted": impacted,
        "counts": {s: sum(1 for i in impacted if i["system"] == s) for s in ("requirements", "parts", "tests")},
        "retest_required": [i["item_id"] for i in impacted if i["system"] == "tests"],
        "approvals": approvals,
    }


def list_all(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM impacted_item i WHERE i.change_id = c.id) AS impacted_count, "
        "(SELECT title FROM item WHERE id = c.target_id) AS target_title "
        "FROM change_request c ORDER BY c.id DESC")]

