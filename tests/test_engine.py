import sqlite3

import pytest
from fastapi.testclient import TestClient

from cie import events
from cie.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"), run_worker=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


def ids(items):
    return {i["item_id"] for i in items}


def run_worker(client):
    return client.post("/admin/process-events").json()


# ---- impact rules -------------------------------------------------------------------

def test_sensor_swap_impact(client):
    """Swapping the pressure sensor: climbs the BOM, finds the requirement and the tests."""
    imp = client.get("/impact/PRT-122").json()["impacted"]
    assert ids(imp) == {"PRT-120", "PRT-100", "REQ-010", "TST-202", "TST-207"}
    depth = {i["item_id"]: i["depth"] for i in imp}
    assert depth["PRT-120"] == 1 and depth["PRT-100"] == 2 and depth["REQ-010"] == 1


def test_requirement_change_flows_down(client):
    """Tightening the occlusion requirement reaches derived requirements, their parts and tests."""
    imp = client.get("/impact/REQ-002").json()["impacted"]
    assert ids(imp) == {"REQ-010", "REQ-011", "PRT-122", "PRT-123", "PRT-124", "TST-202", "TST-203"}


def test_firmware_change_is_wide(client):
    imp = ids(client.get("/impact/PRT-124").json()["impacted"])
    assert {"REQ-001", "REQ-005", "REQ-010", "TST-201", "TST-202", "TST-206", "TST-207"} <= imp


def test_unknown_item_404(client):
    assert client.get("/impact/PRT-999").status_code == 404


# ---- change lifecycle -----------------------------------------------------------------

def test_change_lifecycle_approved(client):
    r = client.post("/changes", json={"target_id": "PRT-122", "description": "Second-source pressure sensor",
                                      "requested_by": "Electrical Lead"})
    assert r.status_code == 202
    cid = r.json()["id"]
    assert client.get(f"/changes/{cid}").json()["state"] == "submitted"

    assert run_worker(client)["processed"] == 1
    d = client.get(f"/changes/{cid}").json()
    assert d["state"] == "in_review"
    assert set(d["retest_required"]) == {"TST-202", "TST-207"}
    approvers = [a["approver"] for a in d["approvals"]]
    assert set(approvers) == {"Configuration Manager", "Electrical Lead", "Mechanical Lead", "Firmware Lead", "V&V Lead"}

    for a in approvers:
        state = client.post(f"/changes/{cid}/decisions", json={"approver": a, "decision": "approved"}).json()["state"]
    assert state == "approved"


def test_one_rejection_rejects(client):
    cid = client.post("/changes", json={"target_id": "PRT-130", "description": "Thinner housing wall",
                                        "requested_by": "Mechanical Lead"}).json()["id"]
    run_worker(client)
    r = client.post(f"/changes/{cid}/decisions", json={"approver": "V&V Lead", "decision": "rejected",
                                                       "comment": "Drop test margin too thin"})
    assert r.json()["state"] == "rejected"
    # no more decisions once closed
    r = client.post(f"/changes/{cid}/decisions", json={"approver": "Configuration Manager", "decision": "approved"})
    assert r.status_code == 409


def test_non_approver_and_double_vote_refused(client):
    cid = client.post("/changes", json={"target_id": "PRT-140", "description": "New cell supplier",
                                        "requested_by": "Electrical Lead"}).json()["id"]
    run_worker(client)
    assert client.post(f"/changes/{cid}/decisions",
                       json={"approver": "Software Lead", "decision": "approved"}).status_code == 409
    client.post(f"/changes/{cid}/decisions", json={"approver": "Electrical Lead", "decision": "approved"})
    assert client.post(f"/changes/{cid}/decisions",
                       json={"approver": "Electrical Lead", "decision": "approved"}).status_code == 409


def test_revising_an_item_opens_a_change_automatically(client):
    r = client.patch("/plm/parts/PRT-123", json={"title": "Alarm speaker, 8 ohm", "summary": "Supplier EOL",
                                                 "changed_by": "Electrical Lead"})
    assert r.json()["revision"] == 2
    run_worker(client)  # item.changed -> detected change, assessed
    crs = client.get("/changes").json()
    assert len(crs) == 1 and crs[0]["source"] == "detected" and crs[0]["state"] == "in_review"
    assert run_worker(client)["processed"] == 1  # its change.submitted event is a no-op, not a duplicate
    assert len(client.get("/changes").json()) == 1


# ---- events -----------------------------------------------------------------------------

def test_redelivered_event_is_not_processed_twice(client):
    cid = client.post("/changes", json={"target_id": "PRT-111", "description": "Motor swap",
                                        "requested_by": "Mechanical Lead"}).json()["id"]
    run_worker(client)
    before = client.get(f"/changes/{cid}").json()
    # simulate the queue delivering the same event again after a crash
    client.conn.execute("UPDATE outbox SET status = 'pending'")
    stats = run_worker(client)
    assert stats["processed"] == 0 and stats["skipped_duplicate"] >= 1
    assert client.get(f"/changes/{cid}").json() == before


def test_failing_event_retries_then_dead_letters(client, monkeypatch):
    def boom(conn, payload):
        raise RuntimeError("downstream system unavailable")
    monkeypatch.setitem(events.HANDLERS, "change.submitted", boom)
    cid = client.post("/changes", json={"target_id": "PRT-111", "description": "Motor swap",
                                        "requested_by": "Mechanical Lead"}).json()["id"]
    assert run_worker(client)["failed"] == 1
    assert run_worker(client)["failed"] == 1
    assert run_worker(client)["dead_lettered"] == 1
    ev = client.get("/admin/events").json()
    assert ev["dead_letter"][0]["error"] == "downstream system unavailable"
    # the failed handler's partial work rolled back each time
    assert client.get(f"/changes/{cid}").json()["state"] == "submitted"
    assert client.conn.execute("SELECT COUNT(*) FROM impacted_item").fetchone()[0] == 0


# ---- audit ---------------------------------------------------------------------------------

def test_audit_is_append_only(client):
    client.post("/changes", json={"target_id": "PRT-122", "description": "x" * 5, "requested_by": "Electrical Lead"})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        client.conn.execute("UPDATE audit_log SET actor = 'someone else'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        client.conn.execute("DELETE FROM audit_log")


def test_audit_chain_detects_tampering(client):
    cid = client.post("/changes", json={"target_id": "PRT-122", "description": "Sensor swap",
                                        "requested_by": "Electrical Lead"}).json()["id"]
    run_worker(client)
    client.post(f"/changes/{cid}/decisions", json={"approver": "V&V Lead", "decision": "approved"})
    v = client.get("/audit/verify").json()
    assert v["valid"] and v["rows_checked"] >= 4
    # someone with direct database access drops the trigger and rewrites history
    client.conn.execute("DROP TRIGGER audit_no_update")
    client.conn.execute("UPDATE audit_log SET actor = 'Configuration Manager' WHERE action = 'change.approved'")
    v = client.get("/audit/verify").json()
    assert not v["valid"] and v["broken_at_seq"] is not None


def test_systems_of_record_endpoints(client):
    assert len(client.get("/alm/requirements").json()) == 8
    assert len(client.get("/plm/parts").json()) == 11
    assert len(client.get("/tms/tests").json()) == 7
    assert client.get("/plm/parts/PRT-122").json()["owner"] == "Electrical Lead"
    assert client.get("/plm/parts/PRT-999").status_code == 404
    assert client.post("/changes", json={"target_id": "bad", "description": "xyz",
                                         "requested_by": "me"}).status_code == 422
