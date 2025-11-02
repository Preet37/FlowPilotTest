from fastapi import FastAPI
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv

# Load .env located inside /server folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

from .storage import init_db, SessionLocal, Task
from .connect_gmail import list_recent_threads
from .extractor import extract_from_email
from .scheduler_engine import plan_all_pending
from .digest import daily_digest_text

app = FastAPI(title="FlowPilot", version="0.1")

# -------------------------
# App startup
# -------------------------
@app.on_event("startup")
def startup_event():
    init_db()

# -------------------------
# Health check
# -------------------------
@app.get("/health")
def health():
    return {"ok": True}

# -------------------------
# Debug: show which LLM is being used
# -------------------------
@app.get("/whoami")
def whoami():
    provider = os.getenv("LLM_PROVIDER")
    has_groq = bool(os.getenv("GROQ_API_KEY"))
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    return {
        "provider": provider if provider else ("groq(auto)" if has_groq else "openai(auto)"),
        "has_groq_key": has_groq,
        "has_openai_key": has_openai
    }

# -------------------------
# Ingest Gmail → Extract Tasks
# -------------------------
@app.post("/ingest/gmail")
def ingest_gmail(max_threads: int = 10):
    threads = list_recent_threads(max_threads=max_threads)

    db = SessionLocal()
    new_count = 0

    for th in threads:
        extracted = extract_from_email(th["subject"], th["snippet"], th["body"])
        for task in extracted:
            t = Task(
                title=task["title"],
                due=task["due"],
                duration_min=task["duration_min"],
                priority=task["priority"],
                status="pending",
                source="email",
            )
            db.add(t)
            new_count += 1

    db.commit()
    db.close()
    return {"ok": True, "tasks_created": new_count}

# -------------------------
# View tasks  (fixed to match your Task columns)
# -------------------------
@app.get("/tasks")
def get_tasks():
    db = SessionLocal()
    rows = db.query(Task).all()
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "title": r.title,
            "due": str(r.due) if r.due else None,
            "duration_min": r.duration_min,
            "priority": r.priority,
            "status": r.status,
            "planned_at": str(r.planned_at) if getattr(r, "planned_at", None) else None,
            "calendar_event_id": getattr(r, "calendar_event_id", None),
            "source": getattr(r, "source", None),
        })
    db.close()
    return out

# -------------------------
# Run scheduler: auto-place time blocks
# -------------------------
@app.post("/schedule/plan")
def schedule_plan():
    plan_all_pending()
    return {"ok": True}

# -------------------------
# Daily digest report
# -------------------------
@app.get("/digest")
def digest():
    txt = daily_digest_text()
    return {"ok": True, "text": txt}

# -------------------------
# Root
# -------------------------
@app.get("/")
def index():
    return {"name": "FlowPilot", "message": "Hit /docs to explore API."}
