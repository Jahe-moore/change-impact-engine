"""Impact analysis: given one changed item, walk the trace graph and return everything it touches.

Rules (see SPEC.md):
  part change        -> parent assemblies (up the BOM), requirements those parts satisfy,
                        tests that exercise those parts, tests that verify those requirements
  requirement change -> derived requirements (down), parts that satisfy them, tests that verify them
"""
from .db import system_of

# Part change: climb the BOM, then fan out to requirements and tests.
PART_SQL = """
WITH RECURSIVE asm(id, depth, reason) AS (
    SELECT :target, 0, 'changed item'
    UNION
    SELECT l.src, a.depth + 1, 'assembly contains ' || a.id
    FROM trace_link l JOIN asm a ON l.dst = a.id AND l.link_type = 'contains'
    WHERE a.depth < 20  -- guard against a bad cyclic BOM
),
reqs AS (
    SELECT l.src AS id, a.depth + 1 AS depth, 'satisfied by ' || a.id AS reason
    FROM trace_link l JOIN asm a ON l.dst = a.id AND l.link_type = 'satisfied_by'
),
hits AS (
    SELECT id, depth, reason FROM asm
    UNION ALL SELECT id, depth, reason FROM reqs
    UNION ALL
    SELECT l.src, a.depth + 1, 'exercises ' || a.id
    FROM trace_link l JOIN asm a ON l.dst = a.id AND l.link_type = 'exercises'
    UNION ALL
    SELECT l.dst, r.depth + 1, 'verifies ' || r.id
    FROM trace_link l JOIN reqs r ON l.src = r.id AND l.link_type = 'verified_by'
)
SELECT id, depth, reason FROM hits ORDER BY depth, id
"""

# Requirement change: descend the derivation tree, then fan out to parts and tests.
REQ_SQL = """
WITH RECURSIVE der(id, depth, reason) AS (
    SELECT :target, 0, 'changed item'
    UNION
    SELECT l.dst, d.depth + 1, 'derived from ' || d.id
    FROM trace_link l JOIN der d ON l.src = d.id AND l.link_type = 'derives'
    WHERE d.depth < 20
),
hits AS (
    SELECT id, depth, reason FROM der
    UNION ALL
    SELECT l.dst, d.depth + 1,
           CASE l.link_type WHEN 'satisfied_by' THEN 'satisfies ' ELSE 'verifies ' END || d.id
    FROM trace_link l JOIN der d ON l.src = d.id AND l.link_type IN ('satisfied_by', 'verified_by')
)
SELECT id, depth, reason FROM hits ORDER BY depth, id
"""


def analyze(conn, target_id: str) -> list[dict]:
    """Return impacted items (excluding the target itself), each at its shortest depth."""
    system = system_of(target_id)
    if system == "tests":
        sql = None  # a test-procedure change only affects itself in v1
    else:
        sql = PART_SQL if system == "parts" else REQ_SQL
    rows = conn.execute(sql, {"target": target_id}).fetchall() if sql else []

    best: dict[str, dict] = {}
    for r in rows:
        if r["id"] == target_id:
            continue
        if r["id"] not in best or r["depth"] < best[r["id"]]["depth"]:
            best[r["id"]] = {"item_id": r["id"], "depth": r["depth"], "reason": r["reason"]}

    items = []
    for hit in best.values():
        meta = conn.execute("SELECT system, title, owner FROM item WHERE id = ?", (hit["item_id"],)).fetchone()
        items.append({**hit, "system": meta["system"], "title": meta["title"], "owner": meta["owner"]})
    items.sort(key=lambda i: (i["depth"], i["item_id"]))
    return items
