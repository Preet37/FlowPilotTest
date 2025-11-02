from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import datetime
import dateparser

from .llm_client import chat_json


# JSON schema for tasks
SCHEMA_PROPS = {
    "tasks": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title":        {"type":"string"},
                "due":          {"type":["string","null"], "description":"deadline like 'Fri 11:59pm' or ISO"},
                "duration_min": {"type":["integer","null"], "description":"estimated minutes"},
                "priority":     {"type":["integer","null"], "description":"1..5 (3=normal)"},
            },
            "required": ["title"]
        }
    }
}

def _norm_due(text_due: Optional[str]) -> Optional[datetime]:
    if not text_due:
        return None
    dt = dateparser.parse(text_due, settings={"RETURN_AS_TIMEZONE_AWARE": False})
    return dt

def extract_from_email(subject: str, snippet: str, body: str) -> List[Dict[str, Any]]:
    """
    Portable extraction that works with OpenAI or Groq using JSON mode.
    """
    user_payload = f"Subject: {subject}\nSnippet: {snippet}\nBody:\n{body}"
    data = chat_json(
        messages=[
            {"role":"user","content": (
                "Extract actionable tasks from the email content below. "
                "Infer concise titles and due dates from phrases like 'by Friday 11:59pm', "
                "'tomorrow', or explicit timestamps. "
                "If missing, set due=null. Default duration=60 and priority=3.\n\n"
                + user_payload
            )}
        ],
        schema_title="TaskExtraction",
        schema_props=SCHEMA_PROPS
    )
    out = []
    for t in data.get("tasks", []):
        out.append({
            "title": (t.get("title") or "").strip(),
            "due": _norm_due(t.get("due")),
            "duration_min": int(t.get("duration_min") or 60),
            "priority": int(t.get("priority") or 3),
        })
    return out
