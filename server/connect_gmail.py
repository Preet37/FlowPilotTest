from __future__ import annotations
import os, base64, email
from typing import List, Dict, Any
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar"
]

BASE = os.path.dirname(__file__)
CRED = os.path.join(BASE, "credentials.json")
TOKEN = os.path.join(BASE, "token.json")

def _service_gmail():
    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CRED, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN,"w") as f:
            f.write(creds.to_json())
    return build("gmail","v1",credentials=creds, cache_discovery=False)

def list_recent_threads(max_threads: int = 10) -> List[Dict[str,Any]]:
    svc = _service_gmail()
    threads_resp = svc.users().threads().list(userId="me", maxResults=max_threads).execute()
    threads = []
    for t in threads_resp.get("threads", []):
        thr = svc.users().threads().get(userId="me", id=t["id"], format="full").execute()
        # Use last message in thread
        msg = thr["messages"][-1]
        headers = {h["name"].lower():h["value"] for h in msg["payload"]["headers"]}
        subject = headers.get("subject","(no subject)")
        snippet = msg.get("snippet","")
        body_text = ""
        parts = [msg["payload"]]
        while parts:
            p = parts.pop()
            if p.get("parts"):
                parts.extend(p["parts"])
            data = p.get("body", {}).get("data")
            if data:
                try:
                    decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    body_text += "\n" + decoded
                except Exception:
                    pass
        threads.append({
            "thread_id": t["id"],
            "subject": subject,
            "snippet": snippet,
            "body": body_text.strip()
        })
    return threads
