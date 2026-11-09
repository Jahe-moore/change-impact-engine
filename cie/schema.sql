-- Change Impact Engine schema (SQLite). One file per system of record would be the real world;
-- here each system owns its own table and they meet only through trace_link.

-- Requirements system (ALM)
CREATE TABLE IF NOT EXISTS requirement (
    id        TEXT PRIMARY KEY CHECK (id LIKE 'REQ-%'),
    title     TEXT NOT NULL,
    owner     TEXT NOT NULL,
    revision  INTEGER NOT NULL DEFAULT 1
);

-- Parts system (PLM / BOM)
CREATE TABLE IF NOT EXISTS part (
    id        TEXT PRIMARY KEY CHECK (id LIKE 'PRT-%'),
    title     TEXT NOT NULL,
    owner     TEXT NOT NULL,
    revision  INTEGER NOT NULL DEFAULT 1
);

-- Test management system
CREATE TABLE IF NOT EXISTS test_case (
    id        TEXT PRIMARY KEY CHECK (id LIKE 'TST-%'),
    title     TEXT NOT NULL,
    owner     TEXT NOT NULL,
    revision  INTEGER NOT NULL DEFAULT 1
);

CREATE VIEW IF NOT EXISTS item AS
    SELECT id, 'requirements' AS system, title, owner, revision FROM requirement
    UNION ALL SELECT id, 'parts', title, owner, revision FROM part
    UNION ALL SELECT id, 'tests', title, owner, revision FROM test_case;

-- The digital thread: every cross-system relationship lives here.
CREATE TABLE IF NOT EXISTS trace_link (
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    link_type  TEXT NOT NULL CHECK (link_type IN ('derives', 'satisfied_by', 'verified_by', 'contains', 'exercises')),
    PRIMARY KEY (src, dst, link_type)
);
CREATE INDEX IF NOT EXISTS ix_link_dst ON trace_link (dst, link_type);

CREATE TABLE IF NOT EXISTS change_request (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id     TEXT NOT NULL,
    description   TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'manual',   -- manual | detected
    state         TEXT NOT NULL DEFAULT 'submitted'
                  CHECK (state IN ('submitted', 'in_review', 'approved', 'rejected')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS impacted_item (
    change_id  INTEGER NOT NULL REFERENCES change_request (id),
    item_id    TEXT NOT NULL,
    system     TEXT NOT NULL,
    depth      INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    PRIMARY KEY (change_id, item_id)
);

CREATE TABLE IF NOT EXISTS approval (
    change_id   INTEGER NOT NULL REFERENCES change_request (id),
    approver    TEXT NOT NULL,
    decision    TEXT CHECK (decision IN ('approved', 'rejected')),
    comment     TEXT,
    decided_at  TEXT,
    PRIMARY KEY (change_id, approver)
);

-- Outbox: systems of record write events here in the same transaction as their own change.
CREATE TABLE IF NOT EXISTS outbox (
    id          TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'dead')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_event (
    event_id      TEXT PRIMARY KEY,
    processed_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_letter (
    event_id   TEXT PRIMARY KEY,
    error      TEXT NOT NULL,
    failed_at  TEXT NOT NULL
);

-- Audit trail: append-only, hash-chained.
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
