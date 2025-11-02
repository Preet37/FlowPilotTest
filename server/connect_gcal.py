from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo  # Py3.9+
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Scopes needed: read/write calendar
SCOPES = ["https://www.googleapis.com/auth/calendar"]

DIR = Path(__file__).resolve().parent
TOKEN_PATH = DIR / "token.json"          # generated after OAuth
CLIENT_SECRET = DIR / "client_secret.json"  # provide your OAuth client file here

TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles"))

def _rfc3339(dt: datetime | str) -> str:
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _get_creds() -> Credentials:
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET.exists():
                raise RuntimeError(
                    f"Google OAuth client secret not found at {CLIENT_SECRET}. "
                    "Download JSON and place it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds

def google_calendar_service():
    creds = _get_creds()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

def list_events_between(time_min_iso: str | datetime, time_max_iso: str | datetime):
    service = google_calendar_service()
    events_result = service.events().list(
        calendarId="primary",
        timeMin=_rfc3339(time_min_iso),
        timeMax=_rfc3339(time_max_iso),
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()
    return events_result.get("items", [])

def create_event_summary(summary: str, start_dt: datetime, end_dt: datetime, description: str = ""):
    service = google_calendar_service()
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": _rfc3339(start_dt)},
        "end":   {"dateTime": _rfc3339(end_dt)},
    }
    ev = service.events().insert(calendarId="primary", body=body).execute()
    return ev.get("id")
