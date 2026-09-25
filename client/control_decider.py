"""Local, typed decisions for Voice AI. Never returns a shell command."""

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import quote

_agent = None

def _model_path():
    configured = os.environ.get("VOICE_AI_LAYA_MODEL", "")
    if not configured:
        try:
            config = json.loads((Path.home() / ".config/voice-module/config.json").read_text())
            configured = config.get("laya_model_path", "")
        except (OSError, ValueError, AttributeError):
            pass
    return Path(configured).expanduser() if configured else Path.home() / ".local/share/voice-ai/models/laya-multilingual-mlx"
_intents = {
    "open_app": "Open or switch to an application by name.",
    "open_url": "Open a named website or web address.",
    "search_web": "Search the web for a topic.",
    "new_document": "Create a new note, document, tab, or window in the active app.",
    "type_text": "Write or insert dictated text in the focused field.",
    "click": "Press a visible button, menu item, link, or other control.",
    "complex": "A request needing multiple steps or original prose.",
    "none": "Thanks, filler, a question without an action, or unclear speech.",
}


def free_memory_percent():
    pressure = subprocess.run(["/usr/bin/memory_pressure", "-Q"], capture_output=True, text=True, timeout=3)
    free = re.search(r"System-wide memory free percentage:\s*(\d+)%", pressure.stdout)
    return int(free.group(1)) if free else 0


def _laya():
    global _agent
    if _agent is None:
        model = _model_path()
        if not model.is_dir():
            raise RuntimeError("Local Laya checkpoint is missing; set laya_model_path in Voice AI config")
        import laya_mlx as laya
        _agent = laya.load(model, dtype="float16", batch_size=8)
    return _agent


def _choice(state, name, criteria, instructions):
    result = _laya().predict(state, {
        name: {"type": "choice", "instructions": instructions, "criteria": criteria}
    })
    answer = result["answers"][name]
    if answer.get("confidence", 0) < 0.2:
        return "none"
    return answer["choice"]


def _extract(text, pattern):
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip(" .?!\"'") if match else ""


def _bonsai(text, scene):
    # Bonsai is loaded only for requests Laya marks complex. The existing
    # localhost model service controls its own idle unloading and memory use.
    # Observed on this 16 GB Mac: waking Bonsai consumed about 49 percentage
    # points of free memory. Keep a buffer while Voxtral stays available.
    if free_memory_percent() < 70:
        raise RuntimeError("Bonsai deferred because the Mac needs more free memory")
    prompt = (
        "Return JSON only: {\"kind\": one of open_app, open_url, new_document, "
        "type_text, click, none; \"value\": string; \"id\": integer}. "
        "Choose one immediate action. Click ids must occur in the observed UI. "
        "Never invent a click target. Never use shell commands. "
        f"User request: {text}\nObserved UI: {json.dumps(scene[:25])}"
    )
    body = json.dumps({
        "model": "bonsai-2-27b", "temperature": 0, "max_tokens": 180,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:11435/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            content = json.load(response)["choices"][0]["message"]["content"]
        action = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception as exc:
        raise RuntimeError("Bonsai local service is unavailable") from exc
    if not isinstance(action, dict) or action.get("kind") not in {
        "open_app", "open_url", "new_document", "type_text", "click", "none"
    }:
        raise RuntimeError("Bonsai returned an unsupported action")
    action["model_generated"] = True
    return action


def decide(text, scene, allow_compound=True):
    text = text.strip()
    if not text:
        return {"kind": "none"}
    connector = r"\s+and(?:\s+then)?\s+(?=(?:open|launch|create|make|write|type|put|search|google)\b)"
    if allow_compound and re.search(connector, text, re.IGNORECASE):
        parts = re.split(connector, text, maxsplit=4, flags=re.IGNORECASE)
        if 1 < len(parts) <= 5:
            steps = [decide(part, scene, allow_compound=False) for part in parts]
            if all(step.get("kind") in {"open_app", "open_url", "new_document", "type_text"}
                   for step in steps):
                return {"kind": "sequence", "steps": steps}
        return _bonsai(text, scene)
    intent = _choice(text, "intent", _intents, "What immediate desktop action does the user request?")
    # Explicit grammar wins when the narrow classifier confuses nouns such as
    # "note" (new document vs. text entry) or domain names (app vs. website).
    if re.search(r"\b(?:search|google|look up)\b", text, re.IGNORECASE):
        intent = "search_web"
    elif re.search(r"\b(?:take|snap|capture)\s+(?:a\s+)?(?:picture|photo)\b", text, re.IGNORECASE):
        intent = "click"
    elif re.search(r"\b(?:put|write|type|enter|insert)\s+.+\s+in\s+", text, re.IGNORECASE):
        intent = "type_text"
    elif re.search(r"\b(?:open|go to|navigate to)\s+\S+\.\S+", text, re.IGNORECASE):
        intent = "open_url"
    if intent == "none":
        return {"kind": "none"}
    if intent == "open_app":
        name = _extract(text, r"\b(?:open|launch|switch to)\s+(?:the\s+)?(.+?)(?:\s+app)?$")
        return {"kind": "open_app", "value": name} if name else _bonsai(text, scene)
    if intent == "open_url":
        name = _extract(text, r"\b(?:open|go to|navigate to)\s+(?:the\s+)?(.+)$")
        if name:
            name = name.replace(" dot ", ".").replace(" ", "")
            if "." in name and not name.startswith(("http://", "https://")):
                name = "https://" + name
            return {"kind": "open_url", "value": name}
        return _bonsai(text, scene)
    if intent == "search_web":
        query = _extract(text, r"\b(?:search|google|look up)(?:\s+(?:google|the web))?(?:\s+for)?\s+(.+)$")
        return {"kind": "open_url", "value": "https://www.google.com/search?q=" + quote(query)} if query else _bonsai(text, scene)
    if intent == "new_document":
        return {"kind": "new_document"}
    if intent == "type_text":
        value = _extract(text, r"\b(?:write|type|enter|put|insert)\s+(.+?)(?:\s+in (?:the|this|my)\s+.+)?$")
        return {"kind": "type_text", "value": value} if value else _bonsai(text, scene)
    if intent == "click":
        controls = [row for row in scene if isinstance(row, dict) and isinstance(row.get("id"), int)][:12]
        if not controls:
            raise RuntimeError("No accessible control was found")
        criteria = {str(row["id"]): f'{row.get("role", "")}: {row.get("title", "")}' for row in controls}
        criteria["none"] = "No observed control matches the user's request"
        selected = _choice(text + "\n" + json.dumps(controls), "target", criteria,
                           "Which observed control exactly matches the requested action?")
        return {"kind": "click", "id": int(selected)} if selected != "none" else {"kind": "none"}
    return _bonsai(text, scene)


if __name__ == "__main__":
    assert _extract("open Notes", r"\b(?:open|launch)\s+(.+)$") == "Notes"
    assert "Norbert%20Wiener" in "https://www.google.com/search?q=" + quote("Norbert Wiener")
