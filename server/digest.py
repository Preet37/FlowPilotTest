from __future__ import annotations
from datetime import datetime
from .storage import SessionLocal, Task


def daily_digest_text() -> str:
    db = SessionLocal()
    planned_today = db.query(Task).filter(Task.status=="planned").count()
    pending = db.query(Task).filter(Task.status=="pending").count()
    items = db.query(Task).order_by(Task.id.desc()).limit(10).all()
    db.close()
    lines = [
        f"FlowPilot Daily Digest · {datetime.now():%Y-%m-%d}",
        f"✅ Planned today: {planned_today}",
        f"🕒 Pending tasks: {pending}",
        "",
        "Recent tasks:",
    ]
    for t in items:
        lines.append(f"- [{t.status}] {t.title} · due={t.due} · dur={t.duration_min}m")
    return "\n".join(lines)
