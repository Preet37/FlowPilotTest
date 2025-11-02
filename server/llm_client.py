from __future__ import annotations
import os, json
from typing import Dict, Any, List

LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL  = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
GROQ_MODEL    = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

def _client_and_model():
    provider = LLM_PROVIDER
    if provider == "" and GROQ_API_KEY:
        provider = "groq"

    if provider == "groq":
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY missing")
        from groq import Groq
        print(f"[LLM] Provider=groq model={GROQ_MODEL}")
        return Groq(api_key=GROQ_API_KEY), GROQ_MODEL, "groq"

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    from openai import OpenAI
    print(f"[LLM] Provider=openai model={OPENAI_MODEL}")
    return OpenAI(api_key=OPENAI_API_KEY), OPENAI_MODEL, "openai"

def chat_json(messages: List[Dict[str, str]], schema_title: str, schema_props: Dict[str, Any]) -> Dict[str, Any]:
    client, configured_model, provider = _client_and_model()

    system = "You are an extraction engine. Return ONLY valid JSON per schema."
    schema = {"title": schema_title, "type": "object", "properties": schema_props, "required": ["tasks"]}

    # Try configured model first, then fallbacks
    preferred = [configured_model]
    if provider == "groq":
        preferred += ["llama-3.3-70b-versatile", "llama-3.3-70b-specdec", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
    else:
        preferred += ["gpt-4o-mini"]

    last_err = None
    for model in preferred:
        try:
            if provider == "groq":
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role":"system","content":system},
                        {"role":"user","content":"Schema:\n"+json.dumps(schema)+"\n\nExtract according to this schema:"},
                        *messages
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                raw = resp.choices[0].message.content
            else:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role":"system","content":system},
                        {"role":"user","content":"Schema:\n"+json.dumps(schema)+"\n\nExtract according to this schema:"},
                        *messages
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                raw = resp.choices[0].message.content

            try:
                return json.loads(raw)
            except Exception:
                txt = raw.strip()
                if txt.startswith("```"):
                    txt = txt.strip("`")
                    if txt.lower().startswith("json"):
                        txt = txt[4:]
                return json.loads(txt.strip())
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("All LLM attempts failed")
