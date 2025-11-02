from __future__ import annotations
import os
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo  # Py3.9+
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

from .storage import SessionLocal, Task
from .connect_gcal import list_events_between, create_event_summary

TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles"))

WORK_START = time(9, 0)   # 9:00 AM local
WORK_END   = time(18, 0)  # 6:00 PM local
BLOCK_PADDING_MIN = 10

def _day_bounds(d: date):
    start = datetime.combine(d, WORK_START, tzinfo=TZ)
    end   = datetime.combine(d, WORK_END, tzinfo=TZ)
    return start, end

def _rfc3339_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _busy_intervals_for_day(d: date):
    day_start, day_end = _day_bounds(d)
    events = list_events_between(_rfc3339_utc(day_start), _rfc3339_utc(day_end))
    busy = []
    for ev in events:
        # event start/end may be dateTime or date
        s = ev.get("start", {})
        e = ev.get("end", {})
        sdt = s.get("dateTime") or s.get("date")
        edt = e.get("dateTime") or e.get("date")
        if not sdt or not edt:
            continue
        try:
            sdt = datetime.fromisoformat(sdt.replace("Z", "+00:00"))
            edt = datetime.fromisoformat(edt.replace("Z", "+00:00"))
        except Exception:
            continue
        busy.append((sdt.astimezone(TZ), edt.astimezone(TZ)))
    busy.sort(key=lambda x: x[0])
    return busy

def _find_free_slot(d: date, duration_min: int) -> Optional[tuple[datetime, datetime]]:
    """Greedy scan from WORK_START to WORK_END avoiding busy intervals."""
    if duration_min <= 0:
        duration_min = 30

    day_start, day_end = _day_bounds(d)
    busy = _busy_intervals_for_day(d)

    cur = day_start
    for (bs, be) in busy:
        # gap between cur and this busy start
        gap = (bs - cur).total_seconds() / 60.0
        if gap >= duration_min:
            return (cur, cur + timedelta(minutes=duration_min))
        cur = max(cur, be)
        if cur > day_end:
            break

    # tail gap
    if (day_end - cur).total_seconds() / 60.0 >= duration_min:
        return (cur, cur + timedelta(minutes=duration_min))

    return None

def plan_all_pending():
    """Place blocks for pending tasks before their due date (or today if no due)."""
    db = SessionLocal()

    # pull pending tasks ordered by (due soonest, then priority desc)
    q = db.query(Task).filter(Task.status == "pending")
    tasks = q.all()

    # simple priority: earlier due first; if no due, treat as today+2
    def due_key(t: Task):
        return t.due or (datetime.now(TZ).date() + timedelta(days=2))

    tasks.sort(key=lambda t: (due_key(t), -(t.priority or 0)))

    for t in tasks:
        # choose day: earliest of (today..due) that has room
        today = datetime.now(TZ).date()
        last_day = due_key(t)
        cur_day = min(today, last_day)
        d = cur_day
        placed = False

        while d <= last_day:
            slot = _find_free_slot(d, t.duration_min or 60)
            if slot:
                start_dt, end_dt = slot
                # create calendar event
                eid = create_event_summary(
                    summary=f"[FlowPilot] {t.title}",
                    start_dt=start_dt,
                    end_dt=end_dt,
                    description=f"Auto-scheduled by FlowPilot. Priority={t.priority}",
                )
                # update task
                t.status = "planned"
                t.planned_at = start_dt
                t.calendar_event_id = eid
                db.add(t)
                db.commit()
                placed = True
                break
            d = d + timedelta(days=1)

        if not placed:
            # leave as pending; could escalate later
            pass

    db.close()
