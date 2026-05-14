import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine, SessionLocal
from .models import Inventory  # noqa: F401
from .api.v1.inventory import router as inventory_router
from .websocket_manager import manager
from .tasks import transfer_loop, parts_production_loop, assembly1_loop, assembly2_loop


def _seed():
    """서버 시작 시 모든 재고를 초기 상태로 리셋."""
    db = SessionLocal()
    try:
        defaults = {
            "raw_material":  {"raw_material": 100, "head": 0, "body": 0, "arm": 0, "leg": 0},
            "parts_a":       {"raw_material": 10,  "head": 0, "body": 0, "arm": 0, "leg": 0},
            "parts_b":       {"raw_material": 10,  "head": 0, "body": 0, "arm": 0, "leg": 0},
            "assembly":      {"raw_material": 0,   "head": 0, "body": 0, "arm": 0, "leg": 0},
            "final_assembly": {"raw_material": 0,  "head": 0, "body": 0, "arm": 0, "leg": 0},
            "hangar":        {"raw_material": 0,   "head": 0, "body": 0, "arm": 0, "leg": 0},
        }
        for loc, fields in defaults.items():
            row = db.query(Inventory).filter_by(location=loc).first()
            if not row:
                row = Inventory(location=loc)
                db.add(row)
            for col, val in fields.items():
                setattr(row, col, val)
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed()
    tasks = [
        asyncio.create_task(transfer_loop()),
        asyncio.create_task(parts_production_loop()),
        asyncio.create_task(assembly1_loop()),
        asyncio.create_task(assembly2_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Hijack Factory API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory_router, prefix="/api/v1/inventory", tags=["inventory"])


@app.websocket("/ws/factory")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
