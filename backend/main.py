import os
import json
import uuid
import re
import requests
import typing
from dotenv import load_dotenv
from icalendar import Calendar
from datetime import datetime, timedelta, date, time
from typing import List, Optional, Dict, Any
import base64

# --- Load .env *first* ---
load_dotenv()

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# -----------------------------------------------------------------
# START: models.py
# -----------------------------------------------------------------

class Task(BaseModel):
    id: str = str(uuid.uuid4())
    title: str
    dueDate: Optional[datetime] = None
    duration: Optional[int] = 60
    planDay: str = "unscheduled" # unscheduled, today, tomorrow
    needsClarification: bool = False
    pendingQuestions: List[str] = []
    calendarEventId: Optional[str] = None 
    isExternal: bool = False

class AppState(BaseModel):
    tasks: List[Task] = []

class AgentTextBody(BaseModel):
    text: str

class DeleteBody(BaseModel):
    taskId: str

class ClarifyBody(BaseModel):
    taskID: str
    question: str
    answer: str

class IcsBody(BaseModel):
    url: str # This is for Canvas

class NewEvent(BaseModel):
    title: str
    start_iso: str
    end_iso: str
    description: Optional[str] = None
    attendees: Optional[List[str]] = None

# -----------------------------------------------------------------
# END: models.py
# START: contacts.py
# -----------------------------------------------------------------

class Contacts:
    def __init__(self):
        self.by_name = {}

    def find(self, name: str):
        key = name.strip().lower()
        return self.by_name.get(key)

    def learn(self, name: str, email: str):
        key = name.strip().lower()
        self.by_name[key] = {"email": email}
        print(f"CONTACTS: Learned {name} = {email}")

# -----------------------------------------------------------------
# END: contacts.py
# START: parser.py (Groq)
# -----------------------------------------------------------------

MODEL = "llama-3.1-8b-instant"

try:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
except Exception as e:
    print(f"CRITICAL: Failed to initialize Groq client. {e}")
    client = None

SYS_PROMPT_VOICE = (
    "You are an intent parser. Respond with ONLY a valid JSON object. "
    "The JSON object must match this schema: "
    '{"tasks": [{"title": "string", "due": "ISO8601 string or null", "duration_minutes": "integer or null", "needs_contact_name": "boolean"}]}. '
    "--- TITLE LOGIC (CRITICAL) ---"
    "The 'title' MUST include the action verb (like 'Email', 'Call', 'Meet'). "
    "Example: 'email elkaim about amazon' -> {'title': 'Email elkaim about amazon', ...} "
    "--- TIME RANGE HANDLING (CRITICAL) ---"
    "If the user gives a range like 'from 9:30 to 10:30', 'due' MUST be the START time (9:30) and 'duration_minutes' MUST be the calculated duration (60). "
    "If no duration or range is mentioned, 'duration_minutes' MUST be 60 for meetings/tasks, and 15 for reminders."
    "--- DATE & TIME LOGIC ---"
    "If they say 'tomorrow at 3 PM', set 'due' to tomorrow at 3 PM (15:00). "
    "--- CONTACT LOGIC (CRITICAL) ---"
    "If the task is an 'email' or 'call' task, 'needs_contact_name' MUST be true. "
    "Example: 'Email Elkaim' -> {'title': 'Email Elkaim', ..., 'needs_contact_name': true}. "
    "Example: 'Meet Anika' -> {'title': 'Meet Anika', ..., 'needs_contact_name': false}. "
    "Always respond with a valid JSON."
)

SYS_PROMPT_GMAIL = (
    "You are a priority agent. I will give you a list of email subjects and snippets. "
    "Your job is to find *actionable tasks* and *deadlines*. "
    "You MUST ignore spam, newsletters, marketing, and simple confirmations. "
    "Only find tasks with *explicit action verbs* (like 'apply', 'submit', 'review', 'complete') OR *explicit deadlines* (like 'due', 'deadline')."
    "Respond with ONLY a valid JSON object: "
    '{"tasks": [{"title": "string", "due": "ISO8601 string or null", "duration_minutes": "integer or null"}]}. '
    "Example: 'Subject: URGENT: YC App due Friday' -> {'title': 'Apply to YC (from Email)', 'due': 'this Friday at 5 PM', 'duration_minutes': 120}. "
    "Example: 'Subject: Meeting confirmation' -> (This is junk, IGNORE IT. Return []). "
    "Example: 'Snippet: Complete homework 4' -> {'title': 'Complete Homework 4 (from Email)', 'due': null, 'duration_minutes': 180}. "
    "Example: 'Subject: Task submission' -> (This is junk, IGNORE IT. Return []). "
    "Example: 'Subject: Meeting with john' -> (This is junk, IGNORE IT. Return []). "
    "If no *actionable* tasks are found, return {\"tasks\": []}."
)

SYS_PROMPT_AI_PLANNER = (
    "You are an autonomous planning agent for a busy student. "
    "I will give you a list of current calendar events and a new task with no deadline. "
    "Your *only* job is to estimate a reasonable due date for this task. "
    "Respond with ONLY a valid JSON object: "
    '{"due_date": "ISO8601 string"}. '
    "--- CONTEXT ---"
    "The user is a busy student. 'ASAP' means Today or Tomorrow. "
    "If a task mentions an event (e.g., 'YC Jam') and that event is *today*, the task is due *today*. "
    "--- EXAMPLES ---"
    "Current Events: 'YC Hackathon (All-day, Today, Nov 1)'."
    "Task: 'Submit credits for yc agent jam 25'"
    "Response: {\"due_date\": \"[Today's Date]T23:00:00\"} (It's urgent, due by end of day) "
    
    "Current Events: []"
    "Task: 'Apply for GM intern'"
    "Response: {\"due_date\": \"[Date 7 days from now]T17:00:00\"} (Not urgent) "

    "Current Events: 'omiHacks (All-day, Tomorrow, Nov 2)'."
    "Task: 'Join sf telegram chat for omihacks 2025'"
    "Response: {\"due_date\": \"[Today's Date]T20:00:00\"} (It's prep for tomorrow, due today) "
)

def _get_now() -> datetime:
    """Helper to get timezone-aware 'now'."""
    try:
        from zoneinfo import ZoneInfo
        PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
        return datetime.now(PACIFIC_TIME)
    except ImportError:
        # Fallback for environments without zoneinfo
        local_now = datetime.now()
        # This is a naive implementation if zoneinfo is not available
        # You might need a library like 'pytz' for robust, older Python support
        return local_now

def _normalize_due(text_due: str | None) -> datetime | None:
    if not text_due:
        return None
    
    now = _get_now()
    t = str(text_due).lower()
    
    try:
        parsed_date = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if parsed_date.tzinfo is None:
            # Assume local time if no timezone is given
            parsed_date = parsed_date.replace(tzinfo=now.tzinfo)
        return parsed_date
    except Exception:
        pass 

    if t == "today":
        return now.replace(hour=17, minute=0, second=0, microsecond=0)
    if t == "tonight":
        return now.replace(hour=23, minute=59, second=0, microsecond=0)
    if t == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    if "this friday" in t:
        today = now.weekday()
        days_to_friday = (4 - today + 7) % 7
        return (now + timedelta(days=days_to_friday)).replace(hour=17, minute=0, second=0, microsecond=0)
    if t == "next week":
        return (now + timedelta(days=7)).replace(hour=17, minute=0, second=0, microsecond=0)
    
    match = re.search(r"tomorrow at (\d+):?(\d*) ?(am|pm)", t)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if ampm == 'pm' and hour < 12: hour += 12
        if ampm == 'am' and hour == 12: hour = 0
        return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    match_today = re.search(r"today at (\d+):?(\d*) ?(am|pm)", t)
    if match_today:
        hour = int(match_today.group(1))
        minute = int(match_today.group(2) or 0)
        ampm = match_today.group(3)
        if ampm == 'pm' and hour < 12: hour += 12
        if ampm == 'am' and hour == 12: hour = 0
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    match_relative = re.search(r"in (\d+) (hour|minute)s?", t)
    if match_relative:
        amount = int(match_relative.group(1))
        unit = match_relative.group(2)
        if unit == "hour":
            return now + timedelta(hours=amount)
        if unit == "minute":
            return now + timedelta(minutes=amount)

    return None

def parse_into_tasks(note: str, contacts: Contacts) -> List[Task]:
    """Parses a single voice command."""
    if not client: return []
    try:
        now_str = _get_now().isoformat()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYS_PROMPT_VOICE},
                {"role": "user", "content": f"It is currently {now_str}. Parse this: {note}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw_json = resp.choices[0].message.content
        print(f"AGENT: Groq raw response: {raw_json}")
        data: Dict[str, Any] = json.loads(raw_json)
        out: List[Task] = []
        for item in data.get("tasks", []):
            title = item.get("title", "").strip()
            if not title: continue
            due = _normalize_due(item.get("due"))
            duration = item.get("duration_minutes") 
            if duration is None: duration = 60
            needs_clarification = bool(item.get("needs_contact_name"))
            pending_questions: List[str] = []

            # --- *** "HARSH" CONTACT FIX *** ---
            # Use a regex that captures multi-word names
            if needs_clarification:
                name_match = re.search(r"(email|call) (.*)", title.lower())
                if name_match:
                    name = name_match.group(2).strip() # e.g., "harsh karia"
                    if contacts.find(name.lower()) is None: # Check "harsh karia"
                        name_for_question = name.title() # e.g., "Harsh Karia"
                        pending_questions.append(f"Who is {name_for_question}? Provide an email/phone.")
                    else:
                        needs_clarification = False # Found them!
                else:
                    needs_clarification = False
            # --- *** END "HARSH" CONTACT FIX *** ---

            out.append(Task(
                id=str(uuid.uuid4()), title=title.capitalize(), dueDate=due,
                duration=duration, planDay="unscheduled",
                needsClarification=needs_clarification, pendingQuestions=pending_questions,
            ))
        return out
    except Exception as e:
        print(f"Error in Groq parser: {e}")
        return []

def parse_gmail_tasks(email_list_str: str) -> List[Task]:
    """Parses a list of emails into tasks."""
    if not client: return []
    try:
        now_str = _get_now().isoformat()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYS_PROMPT_GMAIL},
                {"role": "user", "content": f"It is currently {now_str}. Parse this email list:\n{email_list_str}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw_json = resp.choices[0].message.content
        print(f"AGENT: Groq Gmail raw response: {raw_json}")
        data: Dict[str, Any] = json.loads(raw_json)
        out: List[Task] = []
        for item in data.get("tasks", []):
            title = item.get("title", "").strip()
            if not title: continue
            due = _normalize_due(item.get("due"))
            duration = item.get("duration_minutes") 
            if duration is None: duration = 60
            out.append(Task(
                id=str(uuid.uuid4()), title=title.capitalize(), dueDate=due,
                duration=duration, planDay="unscheduled", isExternal=True,
            ))
        return out
    except Exception as e:
        print(f"Error in Groq Gmail parser: {e}")
        return []

def get_ai_deadline(task_title: str, context: str) -> Optional[datetime]:
    """
    Uses Groq to estimate a deadline for tasks with no due date.
    NOW includes calendar context!
    """
    if not client: return None
    try:
        now_str = _get_now().isoformat()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYS_PROMPT_AI_PLANNER},
                {"role": "user", "content": f"It is currently {now_str}. My calendar context is: {context}. Now, estimate deadline for: {task_title}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw_json = resp.choices[0].message.content
        print(f"AGENT: AI Planner response: {raw_json}")
        data: Dict[str, Any] = json.loads(raw_json)
        
        if data.get("due_date"):
            return _normalize_due(data.get("due_date"))
        
        days = data.get("days_to_complete", 3) 
        return (_get_now() + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)

    except Exception as e:
        print(f"Error in AI Planner: {e}")
        return None

# -----------------------------------------------------------------
# END: parser.py
# START: planner.py
# -----------------------------------------------------------------

def simple_plan(state: AppState):
    """
    This is the new "smart" planner.
    It correctly sorts tasks based on their *actual* due date.
    """
    if not hasattr(state, 'tasks'):
        return
    
    now = _get_now()
    
    for task in state.tasks:
        if task.needsClarification:
            task.planDay = "unscheduled" 
            continue

        if task.dueDate:
            if task.dueDate.date() == now.date():
                task.planDay = "today"
            elif task.dueDate.date() == (now + timedelta(days=1)).date():
                task.planDay = "tomorrow"
            else:
                task.planDay = "unscheduled" # Future or past tasks
        else:
            task.planDay = "unscheduled"

# -----------------------------------------------------------------
# END: planner.py
# START: google_services.py
# -----------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/gmail.readonly"
]

BASE_DIR = os.path.dirname(__file__)
CRED_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

class GoogleServices:
    def __init__(self):
        self.cal_service = None
        self.people_service = None
        self.gmail_service = None
        self.creds = None
        self._ensure() # Run auth on startup

    def _ensure(self):
        if self.creds and self.creds.valid:
            return
        creds = None
        env_creds = os.getenv("GOOGLE_CREDENTIALS")
        env_token = os.getenv("GOOGLE_TOKEN")
        if env_creds and env_token:
            print("AGENT: Loading Google credentials from Vercel environment.")
            creds_info = json.loads(env_creds).get("installed")
            token_info = json.loads(env_token)
            creds = Credentials(
                token=token_info.get("token"),
                refresh_token=token_info.get("refresh_token"),
                token_uri=creds_info.get("token_uri"),
                client_id=creds_info.get("client_id"),
                client_secret=creds_info.get("client_secret"),
                scopes=SCOPES
            )
            if not creds.valid and creds.expired and creds.refresh_token:
                print("AGENT: Vercel token expired. Refreshing...")
                creds.refresh(Request())
        elif os.path.exists(TOKEN_FILE):
            print("AGENT: Loading Google credentials from local token.json file.")
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            if not creds.valid and creds.expired and creds.refresh_token:
                print("AGENT: Local token expired. Refreshing...")
                creds.refresh(Request())
                with open(TOKEN_FILE, "w") as token:
                    token.write(creds.to_json())
        elif os.path.exists(CRED_FILE):
            print("AGENT: No token found. Running local auth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(CRED_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
        else:
            print("ERROR: Cannot find Google credentials. App will not auth.")
            return

        if creds:
            self.creds = creds
            self.cal_service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            self.people_service = build("people", "v1", credentials=creds, cache_discovery=False)
            self.gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            print("AGENT: Google Calendar, People, and Gmail services are ready.")

    def create_event(self, title: str, start_iso: str, end_iso: str, description: str | None = None, attendees: List[str] = []) -> str:
        self._ensure()
        if not self.cal_service: return "ERROR: Google Calendar service not available."
        
        event_body = {
            "summary": title, 
            "description": description or "",
            "start": {"dateTime": start_iso, "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": end_iso, "timeZone": "America/Los_Angeles"},
            "attendees": [{"email": email} for email in attendees],
        }
        
        event = self.cal_service.events().insert(
            calendarId="primary", 
            body=event_body,
            sendUpdates="all" # <-- This sends the invite!
        ).execute()
        
        print(f"AGENT: Successfully created event '{title}' and sent invites.")
        return event.get("id")

    def delete_event(self, event_id: str):
        self._ensure()
        if not self.cal_service: return
        try:
            self.cal_service.events().delete(calendarId='primary', eventId=event_id, sendUpdates="all").execute()
            print(f"AGENT: Successfully deleted event '{event_id}' from Google Calendar.")
        except Exception as e:
            print(f"AGENT: Failed to delete event '{event_id}'. Error: {e}")

    # --- *** "OVERLAP" BUG FIX *** ---
    def find_free_slot(self, start_time: datetime, duration_minutes: int) -> datetime:
        """
        Finds the next available free slot *during working hours* (9am-6pm).
        This version has the robust overlap-prevention logic.
        """
        self._ensure()
        if not self.cal_service:
            print("AGENT: No GCal service, returning original time.")
            return start_time
        
        print(f"AGENT: Finding free slot for {duration_minutes} min after {start_time.isoformat()}")
        
        WORK_START_HOUR = 9
        WORK_END_HOUR = 18 # 6 PM
        
        # Ensure start_time has timezone info
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=_get_now().tzinfo)

        if start_time.hour < WORK_START_HOUR:
            start_time = start_time.replace(hour=WORK_START_HOUR, minute=0)
        elif start_time.hour >= WORK_END_HOUR:
            start_time = (start_time.date() + timedelta(days=1)).replace(hour=WORK_START_HOUR, minute=0)
            start_time = start_time.replace(tzinfo=_get_now().tzinfo)


        time_min = start_time.isoformat()
        time_max = (start_time + timedelta(days=3)).isoformat() 
        
        try:
            events_result = self.cal_service.freebusy().query(body={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
                "timeZone": "America/Los_Angeles"
            }).execute()
            
            busy_slots = events_result.get('calendars', {}).get('primary', {}).get('busy', [])
            
            current_time = start_time
            
            for _ in range(96): # Check 96 15-minute slots (24 hours)
                end_time = current_time + timedelta(minutes=duration_minutes)
                
                # Check 1: Is this slot within working hours?
                if current_time.hour < WORK_START_HOUR or (end_time.hour > WORK_END_HOUR or (end_time.hour == WORK_END_HOUR and end_time.minute > 0)):
                    current_time = (current_time.date() + timedelta(days=1)).replace(hour=WORK_START_HOUR, minute=0)
                    current_time = current_time.replace(tzinfo=start_time.tzinfo)
                    continue 

                # Check 2: Is this slot busy?
                is_free = True
                for slot in busy_slots:
                    slot_start = datetime.fromisoformat(slot['start'])
                    slot_end = datetime.fromisoformat(slot['end'])
                    
                    # This is the overlap check
                    if (current_time < slot_end) and (end_time > slot_start):
                        is_free = False
                        
                        # --- THIS IS THE ROBUST FIX ---
                        # We are busy. The *next possible start time* is immediately after this busy slot ends.
                        current_time = slot_end 
                        
                        # (Optional but good) Round up to the next 5-minute mark
                        if current_time.minute % 5 != 0:
                            current_time = current_time + timedelta(minutes=(5 - current_time.minute % 5))
                        # --- END ROBUST FIX ---

                        break # Re-start the loop with the new, later current_time
                
                if is_free:
                    print(f"AGENT: Found free slot! {current_time.isoformat()}")
                    return current_time # Found a free slot!
            
            print("AGENT: Could not find a free slot, returning original time.")
            return start_time

        except Exception as e:
            print(f"AGENT: Error in find_free_slot: {e}")
            return start_time
    # --- *** END "OVERLAP" BUG FIX *** ---

    def list_upcoming_events(self, maxResults: int = 50) -> List[dict]:
        self._ensure()
        if not self.cal_service: return []
        now = datetime.utcnow().isoformat() + "Z"
        events_result = (
            self.cal_service.events()
            .list(calendarId="primary", timeMin=now, maxResults=maxResults, singleEvents=True, orderBy="startTime")
            .execute()
        )
        return events_result.get("items", [])

    def sync_google_contacts(self, contacts_memory: Contacts):
        self._ensure()
        if not self.people_service:
            print("ERROR: Google People service not available.")
            return
        print("AGENT: Syncing Google Contacts...")
        try:
            results = self.people_service.people().connections().list(
                resourceName='people/me', pageSize=500, personFields='names,emailAddresses'
            ).execute()
            connections = results.get('connections', [])
            count = 0
            for person in connections:
                names = person.get('names', [])
                emails = person.get('emailAddresses', [])
                if names and emails:
                    name = names[0].get('displayName')
                    email = emails[0].get('value')
                    if name and email:
                        contacts_memory.learn(name.lower(), email)
                        first_name = name.split(' ')[0]
                        contacts_memory.learn(first_name.lower(), email)
                        count += 1
            print(f"AGENT: Synced {count} contacts from Google.")
        except Exception as e:
            print(f"AGENT: Error syncing Google Contacts: {e}")

    def sync_gmail(self) -> List[Task]:
        self._ensure()
        if not self.gmail_service:
            print("ERROR: Google Gmail service not available.")
            return []
        
        print("AGENT: Syncing Gmail...")
        try:
            results = self.gmail_service.users().messages().list(userId='me', maxResults=20, q="-category:(promotions OR social OR forums)").execute()
            messages = results.get('messages', [])
            email_list_str = ""
            
            for msg in messages:
                msg_data = self.gmail_service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['subject', 'from']).execute()
                payload = msg_data.get('payload', {})
                headers = payload.get('headers', [])
                snippet = msg_data.get('snippet', '')
                
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'No Sender')
                
                email_list_str += f"From: {sender}\nSubject: {subject}\nSnippet: {snippet}\n---\n"
            
            return parse_gmail_tasks(email_list_str)

        except Exception as e:
            print(f"AGENT: Error syncing Gmail: {e}")
            return []

# -----------------------------------------------------------------
# END: google_services.py
# START: canvas_ics.py
# -----------------------------------------------------------------

def import_canvas_ics(ics_url: str, state: AppState) -> int:
    try:
        r = requests.get(ics_url, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Failed to fetch ICS url: {e}")
        return 0
    cal = Calendar.from_ical(r.content)
    added = 0
    for component in cal.walk():
        if component.name == "VEVENT":
            title = str(component.get("summary"))
            dtstart = component.get("dtstart").dt
            due = dtstart if isinstance(dtstart, datetime) else None
            if not due: continue
            
            # Make timezone-aware if naive
            if due.tzinfo is None:
                try:
                    # Try to assign local timezone
                    due = due.replace(tzinfo=_get_now().tzinfo)
                except Exception:
                     # Fallback to UTC if local fails
                    due = due.replace(tzinfo=timedelta(0))

            task_id = f"ics-{hash(title+str(due))}"
            duration = 60
            if component.get("dtend"):
                dtend = component.get("dtend").dt
                duration = int((dtend - dtstart).total_seconds() / 60)
            t = Task(
                id=task_id, title=title, dueDate=due, duration=duration,
                planDay="unscheduled", isExternal=True,
            )
            if not any(x.id == t.id for x in state.tasks):
                state.tasks.append(t)
                added += 1
    return added

# -----------------------------------------------------------------
# END: canvas_ics.py
# START: FastAPI App
# -----------------------------------------------------------------

app = FastAPI(title="FlowPilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE = AppState()
CONTACTS = Contacts()
GOOGLE = GoogleServices() # One class to rule them all

# --- Agent Flows ---

def run_agent_flow(text: str):
    """Handles parsing and executing a single voice command."""
    print(f"AGENT: Received text: '{text}'")
    new_tasks = parse_into_tasks(text, contacts=CONTACTS) # Check against known contacts
    if not new_tasks:
        print("AGENT: No tasks parsed.")
        return
        
    print(f"AGENT: Parsed {len(new_tasks)} tasks.")
    
    STATE.tasks.extend(new_tasks)
    
    print("AGENT: Executing actions (Google Calendar)...")
    tasks_to_schedule = [t for t in new_tasks if not t.needsClarification]
    
    for task in tasks_to_schedule:
        try:
            task_duration = task.duration if task.duration is not None else 60
            
            if task.dueDate:
                start_time = task.dueDate 
            else:
                start_time = GOOGLE.find_free_slot(_get_now(), task_duration)
            
            final_start_time = GOOGLE.find_free_slot(start_time, task_duration)
            final_end_time = final_start_time + timedelta(minutes=task_duration)

            attendees = []
            name_match = re.search(r"(with|email|call) (.*)", task.title.lower())
            if name_match:
                name = name_match.group(2).strip().lower()
                email_info = CONTACTS.find(name)
                if email_info:
                    attendees.append(email_info['email'])
                    print(f"AGENT: Found contact for {name}: {email_info['email']}")
                else:
                    print(f"AGENT: WARNING: Could not find contact for {name} (This shouldn't happen if clarify worked)")

            event_id = GOOGLE.create_event(
                title=f"FlowPilot: {task.title}",
                start_iso=final_start_time.isoformat(),
                end_iso=final_end_time.isoformat(),
                description=f"Task ID: {task.id}",
                attendees=attendees
            )
            task.calendarEventId = event_id
            task.dueDate = final_start_time 

        except Exception as e:
            print(f"AGENT: Failed to schedule task '{task.title}'. Error: {e}")
            
    simple_plan(STATE) # Re-plan all tasks
    print("AGENT: Voice flow complete.")


def run_full_sync(canvas_url: str):
    """
    This is the new "hands-off" flow. It syncs everything.
    """
    print("AGENT: --- Starting Full Auto-Sync ---")
    
    # 1. Sync Contacts first
    GOOGLE.sync_google_contacts(CONTACTS)
    
    # --- *** "AGENT MEMORY" FIX *** ---
    # 2. Sync Google Calendar
    events = GOOGLE.list_upcoming_events()
    now = _get_now()
    added_gcal = 0
    for event in events:
        title = event.get('summary', 'Untitled Event')
        
        # The faulty "if title.startswith('FlowPilot:')" line is now GONE.
        # The agent can now see its own previously created events.
        
        start_time, end_time = None, None
        
        if event.get('start', {}).get('date'):
            # Handle All-Day Events
            start_date = date.fromisoformat(event['start']['date'])
            end_date = date.fromisoformat(event['end']['date']) - timedelta(days=1)
            
            if start_date <= now.date() <= end_date:
                start_time = now.replace(hour=9, minute=0) # 9 AM
                end_time = now.replace(hour=17, minute=0) # 5 PM
            elif start_date == (now + timedelta(days=1)).date():
                start_time = (now + timedelta(days=1)).replace(hour=9, minute=0)
                end_time = (now + timedelta(days=1)).replace(hour=17, minute=0)
            else:
                continue
        
        elif event.get('start', {}).get('dateTime'):
            # Handle Regular Timed Events
            start_time = datetime.fromisoformat(event['start']['dateTime'])
            end_time = datetime.fromisoformat(event['end']['dateTime'])
        
        if not start_time or not end_time:
            continue

        duration = int((end_time - start_time).total_seconds() / 60)
        task_id = event.get('id')
        if not task_id: continue
        
        # This check prevents duplicates
        if not any(x.id == task_id for x in STATE.tasks):
            # Check if it's an agent task or external
            is_external = not title.startswith("FlowPilot:")
            
            task = Task(
                id=task_id, title=title, dueDate=start_time, duration=duration,
                planDay="unscheduled", isExternal=is_external,
                # If it's an agent task, store its GCal ID
                calendarEventId=task_id if not is_external else None
            )
            STATE.tasks.append(task)
            added_gcal += 1
    print(f"AGENT: Synced {added_gcal} new external events from Google Calendar.")
    # --- *** END "AGENT MEMORY" FIX *** ---

    # 3. Sync Canvas
    if canvas_url and "YOUR_CALENDAR_ICS_URL_HERE" not in canvas_url:
        added_canvas = import_canvas_ics(canvas_url, STATE)
        print(f"AGENT: Synced {added_canvas} new tasks from Canvas.")
    else:
        print("AGENT: Skipping Canvas sync (no URL provided).")

    # 4. Sync Gmail
    gmail_tasks = GOOGLE.sync_gmail()
    gmail_added = 0
    if gmail_tasks:
        for task in gmail_tasks:
            if not any(x.title.lower() == task.title.lower() for x in STATE.tasks):
                STATE.tasks.append(task)
                gmail_added += 1
        print(f"AGENT: Synced {gmail_added} new tasks from Gmail.")
    
    # 5. AI PLANNER
    tasks_to_plan = [t for t in STATE.tasks if t.dueDate is None and t.planDay == "unscheduled" and not t.needsClarification]
    
    calendar_context = "\n".join([f"- {t.title} (due: {t.dueDate.isoformat()})" for t in STATE.tasks if t.dueDate])

    if tasks_to_plan:
        print(f"AGENT: Found {len(tasks_to_plan)} tasks with no deadline. Asking AI to plan them...")
        for task in tasks_to_plan:
            ai_due_date = get_ai_deadline(task.title, calendar_context)
            if ai_due_date:
                task.dueDate = ai_due_date
                print(f"AGENT: AI auto-scheduled '{task.title}' for {ai_due_date.isoformat()}")

    # --- *** "TOMORROW" SORTING FIX *** ---
    # 6. Schedule ALL tasks that just received an AI deadline
    
    # Get tasks from AI planner
    tasks_to_schedule = [t for t in tasks_to_plan if t.dueDate] 
    
    # Get any new Gmail tasks that had a date
    gmail_tasks_with_dates = [t for t in gmail_tasks if t.dueDate and t not in tasks_to_schedule]
    tasks_to_schedule.extend(gmail_tasks_with_dates)
    
    print(f"AGENT: Found {len(tasks_to_schedule)} tasks to auto-schedule on calendar...")
    
    for task in tasks_to_schedule:
        try:
            start_time = task.dueDate
            task_duration = task.duration if task.duration is not None else 60
            
            final_start_time = GOOGLE.find_free_slot(start_time, task_duration)
            final_end_time = final_start_time + timedelta(minutes=task_duration)

            event_id = GOOGLE.create_event(
                title=f"FlowPilot: {task.title}",
                start_iso=final_start_time.isoformat(),
                end_iso=final_end_time.isoformat(),
                description=f"Task ID: {task.id} (Auto-scheduled)"
            )
            task.calendarEventId = event_id
            task.dueDate = final_start_time # CRITICAL: Update task with the *actual* time
        except Exception as e:
            print(f"AGENT: Failed to schedule auto-task '{task.title}'. Error: {e}")
    # --- *** END "TOMORROW" SORTING FIX *** ---
    
    # 7. Final plan
    simple_plan(STATE)
    print("AGENT: --- Full Auto-Sync Complete ---")


# --- API ENDPOINTS ---

@app.get("/api")
async def root():
    return {"message": "FlowPilot API is running"}

@app.get("/api/demo_state", response_model=AppState)
async def demo_state():
    """Returns the current state *after* re-running the planner."""
    simple_plan(STATE) # <-- Re-run plan just before sending
    return STATE

@app.post("/api/sync_all")
async def sync_all(body: IcsBody, background_tasks: BackgroundTasks):
    """
    This is the REAL hands-off agent.
    It syncs Contacts, GCal, Gmail, and Canvas all at once.
    """
    background_tasks.add_task(run_full_sync, body.url)
    return {"ok": True, "message": "Full sync started."}

@app.post("/api/parse_and_plan")
async def parse_and_plan(body: AgentTextBody, background_tasks: BackgroundTasks):
    """Handles a single voice command."""
    background_tasks.add_task(run_agent_flow, body.text)
    return {"ok": True, "message": "Agent flow started."}

@app.post("/api/delete_task")
async def delete_task(body: DeleteBody, background_tasks: BackgroundTasks):
    print(f"AGENT: Received request to delete task {body.taskId}")
    task_to_delete = None
    for task in STATE.tasks:
        if task.id == body.taskId:
            task_to_delete = task
            break
    if task_to_delete:
        STATE.tasks.remove(task_to_delete)
        # Only delete from GCal if it's NOT external and has a calendar ID
        if not task_to_delete.isExternal and task_to_delete.calendarEventId:
            background_tasks.add_task(GOOGLE.delete_event, task_to_delete.calendarEventId)
        return {"ok": True, "message": "Task deleted."}
    return {"ok": False, "message": "Task not found."}

@app.post("/api/clarify")
async def clarify(body: ClarifyBody, background_tasks: BackgroundTasks):
    task_found = False
    for t in STATE.tasks:
        if t.id == body.taskID and t.needsClarification:
            original_question = next((q for q in t.pendingQuestions if q.startswith("Who is")), None)
            if original_question:
                t.pendingQuestions = [] 
                t.needsClarification = False
                task_found = True

                # --- "HARSH" CLARIFY FIX ---
                name_match = re.search(r"Who is (.*?)\?", original_question)
                
                if name_match:
                    name = name_match.group(1) # This will now be "Harsh Karia"
                    CONTACTS.learn(name.lower(), body.answer) # Learns "harsh karia"
                    
                    # --- NEW: Immediately schedule the task in the background ---
                    # We have the name and email, so we can run the flow
                    cloned_task = t.copy(deep=True)
                    background_tasks.add_task(schedule_clarified_task, cloned_task, body.answer)
                    
                break
    
    if not task_found:
        return {"ok": False, "message": "Task not found"}
        
    return {"ok": True}

def schedule_clarified_task(task: Task, email: str):
    """
    This runs in the background to schedule a task *after* it's been clarified.
    """
    print(f"AGENT: Scheduling clarified task: {task.title}")
    try:
        task_duration = task.duration if task.duration is not None else 60
        if task.dueDate:
            start_time = task.dueDate
        else:
            start_time = GOOGLE.find_free_slot(_get_now(), task_duration)
        
        final_start_time = GOOGLE.find_free_slot(start_time, task_duration)
        final_end_time = final_start_time + timedelta(minutes=task_duration)

        event_id = GOOGLE.create_event(
            title=f"FlowPilot: {task.title}",
            start_iso=final_start_time.isoformat(),
            end_iso=final_end_time.isoformat(),
            description=f"Task ID: {task.id}",
            attendees=[email] # <-- Use the email from the clarification!
        )
        
        # Now, find the original task in the STATE and update it
        for t in STATE.tasks:
            if t.id == task.id:
                t.calendarEventId = event_id
                t.dueDate = final_start_time 
                break
        
        simple_plan(STATE) # Re-plan all tasks
        print(f"AGENT: Clarified task '{task.title}' scheduled successfully.")

    except Exception as e:
        print(f"AGENT: Failed to schedule clarified task. Error: {e}")