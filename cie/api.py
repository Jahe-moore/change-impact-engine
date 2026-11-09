"""HTTP API: three mock systems of record plus the impact engine.

Run:  uvicorn cie.api:create_app --factory      then open http://127.0.0.1:8000
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audit, changes, events, impact
from .db import SYSTEM_TABLES, connect, get_item, init_db, tx
from .seed import seed

STATIC = Path(__file__).with_name("static")


class ItemPatch(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=3, max_length=500, description="What changed and why")
    changed_by: str = Field(default="system", max_length=80)


class ChangeIn(BaseModel):
    target_id: str = Field(pattern=r"^(REQ|PRT|TST)-\d{3}$")
    description: str = Field(min_length=3, max_length=500)
    requested_by: str = Field(min_length=2, max_length=80)


class DecisionIn(BaseModel):
    approver: str = Field(min_length=2, max_length=80)
    decision: str = Field(pattern=r"^(approved|rejected)$")
    comment: str = Field(default="", max_length=500)


def create_app(db_path: str | None = None, run_worker: bool = True, worker_interval: float = 1.0) -> FastAPI:
    db_path = db_path or os.environ.get("CIE_DB", "data/cie.db")
    conn = connect(db_path)
    init_db(conn)
    seed(conn)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if run_worker:
            async def loop():
                while True:
                    await asyncio.to_thread(events.process_pending, conn)
                    await asyncio.sleep(worker_interval)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Change Impact Engine", version="0.1.0", lifespan=lifespan,
                  description="Engineering change impact across requirements, parts and tests.")
    app.state.conn = conn

    def db(request: Request):
        return request.app.state.conn

    # ---- the three systems of record --------------------------------------------
    def system_router(system: str, prefix: str, collection: str) -> APIRouter:
        table = SYSTEM_TABLES[system]
        r = APIRouter(prefix=prefix, tags=[f"system: {system}"])

        @r.get(f"/{collection}")
        def list_items(c=Depends(db)):
            return [dict(x) for x in c.execute(f"SELECT * FROM {table} ORDER BY id")]

        @r.get(f"/{collection}/{{item_id}}")
        def get_one(item_id: str, c=Depends(db)):
            row = c.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise HTTPException(404, f"{item_id} not found")
            return dict(row)

        @r.patch(f"/{collection}/{{item_id}}")
        def revise(item_id: str, body: ItemPatch, c=Depends(db)):
            """Revise an item. Writes the change and an item.changed event in one transaction."""
            with tx(c):
                row = c.execute(f"SELECT revision FROM {table} WHERE id = ?", (item_id,)).fetchone()
                if row is None:
                    raise HTTPException(404, f"{item_id} not found")
                rev = row["revision"] + 1
                c.execute(f"UPDATE {table} SET title = ?, revision = ? WHERE id = ?", (body.title, rev, item_id))
                event_id = events.emit(c, "item.changed", {"item_id": item_id, "system": system, "revision": rev,
                                                           "summary": body.summary, "changed_by": body.changed_by})
                audit.record(c, body.changed_by, "item.revised", item_id, {"revision": rev, "title": body.title})
            return {"id": item_id, "revision": rev, "event_id": event_id}

        return r

    app.include_router(system_router("requirements", "/alm", "requirements"))
    app.include_router(system_router("parts", "/plm", "parts"))
    app.include_router(system_router("tests", "/tms", "tests"))

    @app.get("/links", tags=["digital thread"])
    def links(c=Depends(db)):
        return [dict(x) for x in c.execute("SELECT * FROM trace_link ORDER BY link_type, src, dst")]

    # ---- impact engine ---------------------------------------------------------------
    @app.get("/impact/{item_id}", tags=["impact"])
    def preview(item_id: str, c=Depends(db)):
        """What would a change to this item touch? Read-only, files nothing."""
        if get_item(c, item_id) is None:
            raise HTTPException(404, f"{item_id} not found")
        items = impact.analyze(c, item_id)
        approvers = sorted({get_item(c, item_id)["owner"], changes.CONFIG_MANAGER, *(i["owner"] for i in items)})
        return {"target": item_id, "impacted": items, "approvers": approvers}

    @app.post("/changes", status_code=202, tags=["changes"])
    def file_change(body: ChangeIn, c=Depends(db)):
        try:
            with tx(c):
                cid = changes.submit(c, body.target_id, body.description, body.requested_by)
        except changes.ChangeError as e:
            raise HTTPException(404, str(e))
        return {"id": cid, "state": "submitted", "note": "impact is assessed asynchronously by the worker"}

    @app.get("/changes", tags=["changes"])
    def list_changes(c=Depends(db)):
        return changes.list_all(c)

    @app.get("/changes/{change_id}", tags=["changes"])
    def get_change(change_id: int, c=Depends(db)):
        d = changes.detail(c, change_id)
        if d is None:
            raise HTTPException(404, f"CR-{change_id} not found")
        return d

    @app.post("/changes/{change_id}/decisions", tags=["changes"])
    def decide(change_id: int, body: DecisionIn, c=Depends(db)):
        try:
            with tx(c):
                state = changes.decide(c, change_id, body.approver, body.decision, body.comment)
        except changes.ChangeError as e:
            raise HTTPException(409, str(e))
        return {"id": change_id, "state": state}

    # ---- events and audit -----------------------------------------------------------
    @app.post("/admin/process-events", tags=["events"])
    def process(c=Depends(db)):
        return events.process_pending(c)

    @app.get("/admin/events", tags=["events"])
    def event_status(c=Depends(db)):
        return {
            "outbox": [dict(x) for x in c.execute(
                "SELECT id, event_type, status, attempts, last_error, created_at FROM outbox "
                "ORDER BY created_at DESC, rowid DESC LIMIT 50")],
            "dead_letter": [dict(x) for x in c.execute("SELECT * FROM dead_letter ORDER BY failed_at DESC")],
        }

    @app.get("/audit", tags=["audit"])
    def audit_log(entity: str | None = None, c=Depends(db)):
        q, args = "SELECT * FROM audit_log", ()
        if entity:
            q, args = q + " WHERE entity = ?", (entity,)
        return [dict(x) for x in c.execute(q + " ORDER BY seq DESC LIMIT 200", args)]

    @app.get("/audit/verify", tags=["audit"])
    def audit_verify(c=Depends(db)):
        return audit.verify(c)

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app

