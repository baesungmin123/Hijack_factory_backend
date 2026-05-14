from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ...database import get_db
from ...models import Inventory
from ...schemas import (
    InventoryResponse,
    UseRawRequest,
    UsePartRequest,
    CompletePartRequest,
    CompleteAssemblyRequest,
    CompleteHijackRequest,
)
from ...websocket_manager import manager

router = APIRouter()

VALID_PARTS = {"head", "body", "arm", "leg", "raw_material"}

# 부품 완성 → 저장 목적지
# head → final_assembly, body → parts_a, arm/leg → parts_b
PART_DESTINATION = {
    "head": "final_assembly",
    "body": "parts_a",
    "arm":  "parts_b",
    "leg":  "parts_b",
}

# assembly 부품별 공급 출처
ASSEMBLY_SUPPLY_SOURCE = {
    "body": "parts_a",
    "arm":  "parts_b",
    "leg":  "parts_b",
}

HANGAR_LAUNCH_COUNT = 10
ASSEMBLY_REFILL_THRESHOLD = 2
ASSEMBLY_REFILL_AMOUNT = 10


def _refill_assembly_if_low(asm: Inventory, db: Session):
    """assembly의 body/arm/leg가 임계값 이하면 공급 출처에서 10개 이송."""
    for part in ("body", "arm", "leg"):
        if getattr(asm, part) <= ASSEMBLY_REFILL_THRESHOLD:
            source_loc = ASSEMBLY_SUPPLY_SOURCE[part]
            source = db.query(Inventory).filter_by(location=source_loc).first()
            if source:
                transfer = min(ASSEMBLY_REFILL_AMOUNT, getattr(source, part))
                setattr(source, part, getattr(source, part) - transfer)
                setattr(asm, part, getattr(asm, part) + transfer)


def _row_to_dict(row: Inventory) -> dict:
    return {
        "location":     row.location,
        "raw_material": row.raw_material,
        "head":         row.head,
        "body":         row.body,
        "arm":          row.arm,
        "leg":          row.leg,
    }


async def _broadcast_all(db: Session):
    rows = db.query(Inventory).all()
    payload = {row.location: _row_to_dict(row) for row in rows}
    await manager.broadcast({"type": "inventory_update", "payload": payload})


def _get_or_404(db: Session, location: str) -> Inventory:
    row = db.query(Inventory).filter(Inventory.location == location).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"location '{location}' not found")
    return row


# ── GET ──────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[InventoryResponse])
def get_all(db: Session = Depends(get_db)):
    return db.query(Inventory).all()


@router.get("/parts_a", response_model=InventoryResponse)
def get_parts_a(db: Session = Depends(get_db)):
    return _get_or_404(db, "parts_a")


@router.get("/parts_b", response_model=InventoryResponse)
def get_parts_b(db: Session = Depends(get_db)):
    return _get_or_404(db, "parts_b")


@router.get("/assembly", response_model=InventoryResponse)
def get_assembly(db: Session = Depends(get_db)):
    return _get_or_404(db, "assembly")


@router.get("/final_assembly", response_model=InventoryResponse)
def get_final_assembly(db: Session = Depends(get_db)):
    return _get_or_404(db, "final_assembly")


@router.get("/hangar", response_model=InventoryResponse)
def get_hangar(db: Session = Depends(get_db)):
    return _get_or_404(db, "hangar")


@router.get("/{location}", response_model=InventoryResponse)
def get_location(location: str, db: Session = Depends(get_db)):
    return _get_or_404(db, location)


# ── POST ─────────────────────────────────────────────────────────────────────

@router.post("/use-raw")
async def use_raw(req: UseRawRequest, db: Session = Depends(get_db)):
    """원자재 차감: 부품공장 애니메이션 시작 시 호출.
    parts_a/parts_b 위치인 경우 해당 공장 재고 + 원자재 창고 동시 차감."""
    row = _get_or_404(db, req.location)
    row.raw_material = max(0, row.raw_material - req.count)
    if req.location in ("parts_a", "parts_b"):
        warehouse = _get_or_404(db, "raw_material")
        warehouse.raw_material = max(0, warehouse.raw_material - req.count)
    db.commit()
    await _broadcast_all(db)
    return {"ok": True}


@router.post("/use-part")
async def use_part(req: UsePartRequest, db: Session = Depends(get_db)):
    """부품 차감: 조립 시작 시 호출.
    assembly 위치인 경우 차감 후 2개 이하 부품은 10개 자동 보충."""
    if req.part not in VALID_PARTS:
        raise HTTPException(status_code=400, detail=f"unknown part '{req.part}'")
    row = _get_or_404(db, req.location)
    current = getattr(row, req.part)
    setattr(row, req.part, max(0, current - req.count))
    if req.location == "assembly":
        _refill_assembly_if_low(row, db)
    db.commit()
    await _broadcast_all(db)
    return {"ok": True}


@router.post("/complete-part")
async def complete_part(req: CompletePartRequest, db: Session = Depends(get_db)):
    """부품 완성: 애니메이션 완료 시 호출.
    head → final_assembly.head +count
    body → assembly.body +count
    arm  → assembly.arm  +count
    leg  → assembly.leg  +count
    """
    if req.part not in VALID_PARTS:
        raise HTTPException(status_code=400, detail=f"unknown part '{req.part}'")
    dest = PART_DESTINATION.get(req.part, req.location)
    row = _get_or_404(db, dest)
    setattr(row, req.part, getattr(row, req.part) + req.count)
    db.commit()
    await _broadcast_all(db)
    return {"ok": True}


@router.post("/complete-assembly")
async def complete_assembly(req: CompleteAssemblyRequest, db: Session = Depends(get_db)):
    """조립공장1 완료:
    assembly.body/arm/leg -1 → 2개 이하면 10개 자동 보충
    final_assembly.body +1
    """
    asm = _get_or_404(db, "assembly")
    asm.body = max(0, asm.body - 1)
    asm.arm  = max(0, asm.arm  - 1)
    asm.leg  = max(0, asm.leg  - 1)
    _refill_assembly_if_low(asm, db)

    final = _get_or_404(db, "final_assembly")
    final.body += 1

    db.commit()
    await _broadcast_all(db)
    return {"ok": True, "stage": req.stage}


@router.post("/complete-hijack")
async def complete_hijack(req: CompleteHijackRequest, db: Session = Depends(get_db)):
    """조립공장2 최종 조립 완료:
    final_assembly.body -1, final_assembly.head -1
    hangar.raw_material +1
    10대 도달 시 hangar_launch 이벤트 브로드캐스트 후 hangar 초기화
    """
    final = _get_or_404(db, "final_assembly")
    final.body = max(0, final.body - 1)
    final.head = max(0, final.head - 1)

    hangar = _get_or_404(db, "hangar")
    hangar.raw_material += 1

    launched = False
    if hangar.raw_material >= HANGAR_LAUNCH_COUNT:
        hangar.raw_material = 0
        launched = True

    db.commit()
    await _broadcast_all(db)

    if launched:
        await manager.broadcast({"type": "hangar_launch"})

    return {"ok": True, "launched": launched}
