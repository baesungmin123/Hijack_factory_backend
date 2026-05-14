import asyncio
from .database import SessionLocal
from .models import Inventory
from .websocket_manager import manager

# ── 간격 설정 ─────────────────────────────────────────────────────────────────
TRANSFER_INTERVAL          = 180   # 원자재 창고 → 부품공장 이송
TRANSFER_AMOUNT            = 10
REFILL_AMOUNT              = 100

PARTS_PRODUCTION_INTERVAL  = 20    # 부품공장 생산 주기 (초)
PARTS_PRODUCTION_COST      = 2     # 원자재 소모량
PARTS_PRODUCTION_YIELD     = 5     # 부품 생산량

ASSEMBLY1_INTERVAL         = 10    # 조립공장1 주기
ASSEMBLY2_INTERVAL         = 10    # 조립공장2 주기

ASSEMBLY_REFILL_THRESHOLD  = 2
ASSEMBLY_REFILL_AMOUNT     = 10
ASSEMBLY_SUPPLY_SOURCE     = {"body": "parts_a", "arm": "parts_b", "leg": "parts_b"}
HANGAR_LAUNCH_COUNT        = 10


# ── 공통 유틸 ─────────────────────────────────────────────────────────────────

def _row_dict(row: Inventory) -> dict:
    return {
        "location":     row.location,
        "raw_material": row.raw_material,
        "head":         row.head,
        "body":         row.body,
        "arm":          row.arm,
        "leg":          row.leg,
    }


def _all_payload(db) -> dict:
    return {r.location: _row_dict(r) for r in db.query(Inventory).all()}


def _refill_assembly_if_low(asm: Inventory, db):
    """assembly body/arm/leg 중 임계값 이하인 부품을 parts_a/b에서 이송."""
    for part in ("body", "arm", "leg"):
        if getattr(asm, part) <= ASSEMBLY_REFILL_THRESHOLD:
            source = db.query(Inventory).filter_by(
                location=ASSEMBLY_SUPPLY_SOURCE[part]
            ).first()
            if source:
                transfer = min(ASSEMBLY_REFILL_AMOUNT, getattr(source, part))
                setattr(source, part, getattr(source, part) - transfer)
                setattr(asm, part, getattr(asm, part) + transfer)


# ── 1단계: 원자재 창고 → 부품공장 이송 (180초) ───────────────────────────────

def _do_transfer() -> dict | None:
    db = SessionLocal()
    try:
        raw = db.query(Inventory).filter_by(location="raw_material").first()
        pa  = db.query(Inventory).filter_by(location="parts_a").first()
        pb  = db.query(Inventory).filter_by(location="parts_b").first()
        if not (raw and pa and pb):
            return None

        amt_a = min(TRANSFER_AMOUNT, raw.raw_material)
        raw.raw_material -= amt_a
        pa.raw_material  += amt_a

        amt_b = min(TRANSFER_AMOUNT, raw.raw_material)
        raw.raw_material -= amt_b
        pb.raw_material  += amt_b

        if raw.raw_material <= 0:
            raw.raw_material = REFILL_AMOUNT

        db.commit()
        return _all_payload(db)
    finally:
        db.close()


# ── 2단계: 부품공장 생산 (20초) ───────────────────────────────────────────────

def _do_parts_production() -> dict | None:
    """parts_a/b 각각 원자재 2개 소모 → 부품 5개 생산."""
    db = SessionLocal()
    try:
        warehouse = db.query(Inventory).filter_by(location="raw_material").first()
        pa    = db.query(Inventory).filter_by(location="parts_a").first()
        pb    = db.query(Inventory).filter_by(location="parts_b").first()
        final = db.query(Inventory).filter_by(location="final_assembly").first()
        if not (warehouse and pa and pb and final):
            return None

        changed = False

        # 부품공장1: body + head 생산
        if pa.raw_material >= PARTS_PRODUCTION_COST:
            pa.raw_material  -= PARTS_PRODUCTION_COST
            warehouse.raw_material = max(0, warehouse.raw_material - PARTS_PRODUCTION_COST)
            pa.body          += PARTS_PRODUCTION_YIELD
            final.head       += PARTS_PRODUCTION_YIELD
            changed = True

        # 부품공장2: arm + leg 생산
        if pb.raw_material >= PARTS_PRODUCTION_COST:
            pb.raw_material  -= PARTS_PRODUCTION_COST
            warehouse.raw_material = max(0, warehouse.raw_material - PARTS_PRODUCTION_COST)
            pb.arm           += PARTS_PRODUCTION_YIELD
            pb.leg           += PARTS_PRODUCTION_YIELD
            changed = True

        if not changed:
            return None

        db.commit()
        return _all_payload(db)
    finally:
        db.close()


# ── 3단계: 조립공장1 (10초) ───────────────────────────────────────────────────

def _do_assembly1() -> dict | None:
    """assembly.body/arm/leg -1 → final_assembly.body +1."""
    db = SessionLocal()
    try:
        asm   = db.query(Inventory).filter_by(location="assembly").first()
        final = db.query(Inventory).filter_by(location="final_assembly").first()
        if not (asm and final):
            return None

        _refill_assembly_if_low(asm, db)

        if asm.body <= 0 or asm.arm <= 0 or asm.leg <= 0:
            return None

        asm.body = max(0, asm.body - 1)
        asm.arm  = max(0, asm.arm  - 1)
        asm.leg  = max(0, asm.leg  - 1)
        _refill_assembly_if_low(asm, db)
        final.body += 1

        db.commit()
        return _all_payload(db)
    finally:
        db.close()


# ── 4단계: 조립공장2 (10초) ───────────────────────────────────────────────────

def _do_assembly2() -> tuple[dict | None, bool]:
    """final_assembly.body/head -1 → hangar +1. 10대 시 launch."""
    db = SessionLocal()
    try:
        final  = db.query(Inventory).filter_by(location="final_assembly").first()
        hangar = db.query(Inventory).filter_by(location="hangar").first()
        if not (final and hangar):
            return None, False

        if final.body <= 0 or final.head <= 0:
            return None, False

        final.body = max(0, final.body - 1)
        final.head = max(0, final.head - 1)
        hangar.raw_material += 1

        launched = hangar.raw_material >= HANGAR_LAUNCH_COUNT
        if launched:
            hangar.raw_material = 0

        db.commit()
        return _all_payload(db), launched
    finally:
        db.close()


# ── 비동기 루프들 ─────────────────────────────────────────────────────────────

async def transfer_loop():
    while True:
        await asyncio.sleep(TRANSFER_INTERVAL)
        payload = await asyncio.to_thread(_do_transfer)
        if payload:
            await manager.broadcast({"type": "inventory_update", "payload": payload})


async def parts_production_loop():
    while True:
        await asyncio.sleep(PARTS_PRODUCTION_INTERVAL)
        payload = await asyncio.to_thread(_do_parts_production)
        if payload:
            await manager.broadcast({"type": "inventory_update", "payload": payload})


async def assembly1_loop():
    while True:
        await asyncio.sleep(ASSEMBLY1_INTERVAL)
        payload = await asyncio.to_thread(_do_assembly1)
        if payload:
            await manager.broadcast({"type": "inventory_update", "payload": payload})


async def assembly2_loop():
    while True:
        await asyncio.sleep(ASSEMBLY2_INTERVAL)
        result = await asyncio.to_thread(_do_assembly2)
        payload, launched = result
        if payload:
            await manager.broadcast({"type": "inventory_update", "payload": payload})
        if launched:
            await manager.broadcast({"type": "hangar_launch"})
