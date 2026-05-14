from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from .database import Base


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    location = Column(String(50), unique=True, nullable=False)
    raw_material = Column(Integer, default=0)
    head = Column(Integer, default=0)
    body = Column(Integer, default=0)
    arm = Column(Integer, default=0)
    leg = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
