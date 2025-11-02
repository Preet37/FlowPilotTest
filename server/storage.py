from __future__ import annotations
import os
from datetime import datetime
from typing import Optional, List

from sqlalchemy import create_engine, String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, sessionmaker

DB_URL = os.getenv("DB_URL", "sqlite:///./flowpilot.db")

engine = create_engine(DB_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
Base = declarative_base()

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(64))            # 'gmail', 'notion', etc.
    raw_ref: Mapped[Optional[str]] = mapped_column(String(256)) # gmail msg id etc.
    due: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_min: Mapped[int] = mapped_column(Integer, default=int(os.getenv("DEFAULT_BLOCK_MINUTES", "60")))
    priority: Mapped[int] = mapped_column(Integer, default=1)  # 1=normal, higher=more important
    planned_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # when we scheduled it
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="pending") # 'pending','planned','done'
    notes: Mapped[Optional[str]] = mapped_column(Text)

def init_db():
    Base.metadata.create_all(engine)
