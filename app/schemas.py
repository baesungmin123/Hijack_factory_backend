from pydantic import BaseModel


class InventoryResponse(BaseModel):
    location: str
    raw_material: int = 0
    head: int = 0
    body: int = 0
    arm: int = 0
    leg: int = 0

    model_config = {"from_attributes": True}


class UseRawRequest(BaseModel):
    location: str
    count: int = 1


class UsePartRequest(BaseModel):
    location: str
    part: str
    count: int = 1


class CompletePartRequest(BaseModel):
    location: str
    part: str
    count: int = 1


class CompleteAssemblyRequest(BaseModel):
    stage: str


class CompleteHijackRequest(BaseModel):
    pass
