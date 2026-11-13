from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cie import events, exceptions
from cie.api import create_app
from cie.db import tx


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"), run_worker=False)
    with TestClient(app) as c:
        c.conn = app.state.conn
        yield c


def file(client, target="PRT-111"):
    return client.post("/changes", json={"target_id": target, "description": "Motor swap",
                                         "requested_by": "Mechanical Lead"}).json()["id"]


def run(client):
    return client.post("/admin/process-events").json()


def scan_at(client, days_later):
    with tx(client.conn):
        return exceptions.scan(client.conn, datetime.now(timezone.utc) + timedelta(days=days_later))


def test_dead_letter_opens_exception_and_retry_recovers(client, monkeypatch):
    real = events.HANDLERS["change.submitted"]
    monkeypatch.setitem(events.HANDLERS, "change.submitted", lambda c, p: (_ for _ in ()).throw(RuntimeError("PLM down")))
    cid = file(client)
    for _ in range(3):
        run(client)
    [ex] = client.get("/exceptions?state=open").json()
    assert ex["kind"] == "dead_letter" and ex["owner"] == "Integration Support" and "PLM down" in ex["summary"]

    monkeypatch.setitem(events.HANDLERS, "change.submitted", real)  # downstream is back
    r = client.post(f"/exceptions/{ex['id']}/resolve", json={"action": "retry", "by": "Integration Support"})
    assert r.json()["resolution"] == "retried"
    assert client.get("/admin/events").json()["dead_letter"] == []
    assert run(client)["processed"] == 1
    assert client.get(f"/changes/{cid}").json()["state"] == "in_review"
    assert client.get("/exceptions?state=open").json() == []


def test_overdue_review_opens_once_and_closes_itself(client):
    cid = file(client)
    run(client)
    assert scan_at(client, 1) == {"opened": 0, "auto_closed": 0}
    assert scan_at(client, 6)["opened"] == 1
    assert scan_at(client, 7)["opened"] == 0  # already open: no duplicate
    [ex] = client.get("/exceptions?state=open").json()
    assert ex["kind"] == "approval_overdue" and ex["entity"] == f"CR-{cid}" and "Configuration Manager" in ex["summary"]

    client.post(f"/changes/{cid}/decisions", json={"approver": "Test Lead", "decision": "rejected", "comment": "no"})
    assert scan_at(client, 7)["auto_closed"] == 1
    assert client.get("/exceptions").json()[0]["resolution"] == "auto_closed"


def test_dismissed_overdue_does_not_reopen(client):
    file(client)
    run(client)
    scan_at(client, 6)
    ex_id = client.get("/exceptions").json()[0]["id"]
    client.post(f"/exceptions/{ex_id}/resolve",
                json={"action": "dismiss", "by": "Configuration Manager", "note": "Waiting on supplier data, agreed"})
    assert scan_at(client, 8)["opened"] == 0
    assert client.get("/exceptions?state=open").json() == []


def test_untraced_item_flags_incomplete_impact(client):
    with tx(client.conn):
        client.conn.execute("INSERT INTO part (id, title, owner) VALUES ('PRT-150', 'Bracket', 'Mechanical Lead')")
    cid = file(client, "PRT-150")
    run(client)
    [ex] = client.get("/exceptions").json()
    assert ex["kind"] == "untraced_change" and ex["entity"] == f"CR-{cid}"
    # retry is only for dead letters; dismiss needs a reason
    assert client.post(f"/exceptions/{ex['id']}/resolve", json={"action": "retry", "by": "CM"}).status_code == 409
    assert client.post(f"/exceptions/{ex['id']}/resolve", json={"action": "dismiss", "by": "CM"}).status_code == 409
    r = client.post(f"/exceptions/{ex['id']}/resolve",
                    json={"action": "dismiss", "by": "Configuration Manager", "note": "Bracket is new; links added"})
    assert r.json()["state"] == "resolved"
    assert client.post(f"/exceptions/{ex['id']}/resolve",
                       json={"action": "dismiss", "by": "CM", "note": "again"}).status_code == 409


def test_assign_metrics_and_audit(client):
    file(client)
    run(client)
    scan_at(client, 6)
    ex_id = client.get("/exceptions").json()[0]["id"]
    r = client.post(f"/exceptions/{ex_id}/assign", json={"owner": "Mechanical Lead", "by": "Configuration Manager"})
    assert r.json()["owner"] == "Mechanical Lead"
    m = client.get("/exceptions/metrics").json()
    assert m["open"] == 1 and m["open_by_kind"]["approval_overdue"] == 1 and m["median_hours_to_resolve"] is None
    actions = {e["action"] for e in client.get(f"/audit?entity=EX-{ex_id}").json()}
    assert actions == {"exception.opened", "exception.assigned"}
    assert client.get("/audit/verify").json()["valid"]
