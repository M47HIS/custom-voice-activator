"""
Voice Module Backend — optional coordination layer for the native macOS app.

Provides action/settings APIs and WebSocket status relay. The native
VoiceActivator.app handles hotkey, recording, and transcription directly
via its Python worker. This backend is optional — the app works without it.
"""

import asyncio
from collections import deque
import json
import logging
import os
import secrets
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from transcriber import Transcriber, TranscriptionError

# ── Configuration ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("voice-backend")

ENGINE = os.getenv("ENGINE", "faster-whisper")
LANGUAGE = os.getenv("LANGUAGE", "en")

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config" / "default_actions.json"
DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent / "data")))
ACTIONS_PATH = DATA_DIR / "actions.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_TRANSCRIPT_CHARS = int(os.getenv("MAX_TRANSCRIPT_CHARS", "20000"))
MAX_TRANSCRIPTIONS_PER_MINUTE = int(os.getenv("MAX_TRANSCRIPTIONS_PER_MINUTE", "10"))
ALLOWED_REMOTE_ACTION_TYPES = {"clipboard"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# ── App ─────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Voice Module Backend", version="2.0.0")


def _loopback_hostname(value: str | None) -> bool:
    if not value:
        return False
    try:
        hostname = urlsplit(f"//{value}").hostname
    except ValueError:
        return False
    return hostname in LOOPBACK_HOSTS


def _allowed_origin(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in LOOPBACK_HOSTS


@app.middleware("http")
async def enforce_loopback_request_boundary(request: Request, call_next):
    """Reject DNS-rebinding and cross-origin browser requests."""
    if not _loopback_hostname(request.headers.get("host")):
        return JSONResponse(status_code=400, content={"detail": "Loopback Host required."})
    if not _allowed_origin(request.headers.get("origin")):
        return JSONResponse(status_code=403, content={"detail": "Origin is not allowed."})
    if request.url.path == "/api/transcribe":
        if not _has_valid_bearer(request.headers.get("authorization")):
            return JSONResponse(status_code=401, content={"detail": "Authentication required."})
        content_length = request.headers.get("content-length")
        if content_length is None:
            return JSONResponse(status_code=411, content={"detail": "Content-Length is required."})
        try:
            declared_size = int(content_length)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
        if declared_size > MAX_UPLOAD_BYTES + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Audio upload is too large."})
    return await call_next(request)

# ── Global State ────────────────────────────────────────────────────────────────

class ServerState:
    """Thread-safe server state shared across WebSocket and REST handlers."""

    def __init__(self):
        self.state: str = "idle"  # idle | listening | transcribing
        self.transcription_history: list[dict] = []
        self.connected_clients: set[WebSocket] = set()
        self.engine: str = ENGINE
        self.last_activity: float = time.time()

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected WebSocket clients."""
        data = json.dumps(message)
        dead: set[WebSocket] = set()
        tasks = []
        for ws in self.connected_clients:
            try:
                tasks.append(asyncio.create_task(ws.send_text(data)))
            except Exception:
                dead.add(ws)
        if dead:
            self.connected_clients -= dead
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def set_state(self, new_state: str) -> None:
        self.state = new_state
        self.last_activity = time.time()
        await self.broadcast({"type": "status", "state": new_state})

    def add_transcription(self, text: str) -> None:
        entry = {
            "text": text,
            "timestamp": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.transcription_history.insert(0, entry)
        # Keep last 50
        if len(self.transcription_history) > 50:
            self.transcription_history = self.transcription_history[:50]


state = ServerState()
transcriber = Transcriber()

# ── Auth Token ──────────────────────────────────────────────────────────────────

AUTH_TOKEN_PATH = DATA_DIR / "auth_token"
_auth_token: str | None = None


def _store_auth_token(token: str) -> None:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    fd = os.open(AUTH_TOKEN_PATH, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w") as output:
            fd = -1
            output.write(token)
    finally:
        if fd >= 0:
            os.close(fd)


def _load_or_create_auth_token() -> str:
    """Load existing auth token or create a new one on first startup."""
    global _auth_token
    configured_token = os.getenv("VOICE_MODULE_AUTH_TOKEN", "").strip()
    if configured_token:
        _auth_token = configured_token
        _store_auth_token(_auth_token)
        logger.info("Auth token loaded from VOICE_MODULE_AUTH_TOKEN.")
        return _auth_token

    if AUTH_TOKEN_PATH.exists():
        try:
            os.chmod(DATA_DIR, 0o700)
            os.chmod(AUTH_TOKEN_PATH, 0o600)
            _auth_token = AUTH_TOKEN_PATH.read_text().strip()
            if not _auth_token:
                raise ValueError("empty token file")
            logger.info("Auth token loaded from file.")
            return _auth_token
        except Exception as e:
            logger.warning(f"Could not read auth token file ({e}), generating new one.")

    _auth_token = secrets.token_urlsafe(32)
    _store_auth_token(_auth_token)
    logger.info("Auth token created and stored in the backend data directory.")
    return _auth_token


def _has_valid_bearer(authorization: str | None) -> bool:
    if _auth_token is None or authorization is None or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization.split(" ", 1)[1], _auth_token)


def verify_auth_token(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: require valid Bearer token for mutation endpoints."""
    if _auth_token is None:
        raise HTTPException(status_code=500, detail="Auth token not configured on server.")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1]
    if not secrets.compare_digest(token, _auth_token):
        raise HTTPException(status_code=403, detail="Invalid auth token.")
    return token


# ── Startup ─────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info(f"Voice Module backend starting (engine: {ENGINE})")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Load or create auth token
    _load_or_create_auth_token()
    # Load actions
    load_actions()
    # Load settings
    load_settings()
    logger.info("Backend ready.")


# ── Actions Management ──────────────────────────────────────────────────────────

_actions: list[dict] = []


def _is_safe_remote_action(action: object) -> bool:
    return (
        isinstance(action, dict)
        and action.get("type") in ALLOWED_REMOTE_ACTION_TYPES
        and isinstance(action.get("name"), str)
        and bool(action["name"].strip())
        and isinstance(action.get("config", {}), dict)
    )


def load_actions():
    global _actions
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if ACTIONS_PATH.exists():
        try:
            with open(ACTIONS_PATH) as f:
                loaded = json.load(f)
            _actions = [action for action in loaded if _is_safe_remote_action(action)]
            logger.info(f"Loaded {len(_actions)} actions from {ACTIONS_PATH}")
        except (json.JSONDecodeError, PermissionError) as e:
            logger.warning(f"Could not parse {ACTIONS_PATH}: {e}")
            _actions = []
    elif DEFAULT_CONFIG_PATH.exists():
        logger.info(f"Seeding actions from {DEFAULT_CONFIG_PATH}")
        try:
            with open(DEFAULT_CONFIG_PATH) as f:
                loaded = json.load(f)
            _actions = [action for action in loaded if _is_safe_remote_action(action)]
            save_actions()
            logger.info(f"Seeded {len(_actions)} default actions to {ACTIONS_PATH}")
        except (json.JSONDecodeError, PermissionError) as e:
            logger.warning(f"Could not load defaults: {e}")
            _actions = []
    else:
        logger.warning("No default actions file found, starting empty.")
        _actions = []


def save_actions():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_PATH, "w") as f:
        json.dump(_actions, f, indent=2)


# ── Settings Management ────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict = {
    "hotkey": "cmd+shift+space",
    "mode": "hold",
    "action": "clipboard",
}
_settings: dict = {}


def load_settings():
    global _settings
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH) as f:
                _settings = json.load(f)
            logger.info(f"Loaded settings from {SETTINGS_PATH}")
        except (json.JSONDecodeError, PermissionError) as e:
            logger.warning(f"Could not parse {SETTINGS_PATH}: {e}")
            _settings = dict(DEFAULT_SETTINGS)
    else:
        _settings = dict(DEFAULT_SETTINGS)
        save_settings()
        logger.info(f"Seeded default settings to {SETTINGS_PATH}")


def save_settings():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(_settings, f, indent=2)


@app.get("/api/actions")
async def get_actions(_token: str = Depends(verify_auth_token)):
    return _actions


@app.post("/api/actions")
async def add_or_update_action(action: dict, _token: str = Depends(verify_auth_token)):
    if "name" not in action or "type" not in action:
        raise HTTPException(status_code=400, detail="Action requires 'name' and 'type' fields.")

    if not _is_safe_remote_action(action):
        raise HTTPException(status_code=400, detail="Only clipboard actions are accepted by the backend.")

    name = action["name"].strip()
    action = {**action, "name": name}
    existing = next((i for i, a in enumerate(_actions) if a["name"] == name), None)
    if existing is not None:
        _actions[existing] = action
        save_actions()
        return {"status": "updated", "name": name}
    else:
        _actions.append(action)
        save_actions()
        return {"status": "created", "name": name}


@app.delete("/api/actions/{name}")
async def delete_action(name: str, _token: str = Depends(verify_auth_token)):
    global _actions
    before = len(_actions)
    _actions = [a for a in _actions if a["name"] != name]
    if len(_actions) == before:
        raise HTTPException(status_code=404, detail=f"Action '{name}' not found.")
    save_actions()
    return {"status": "deleted", "name": name}


# ── Settings Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(_token: str = Depends(verify_auth_token)):
    return _settings


@app.post("/api/settings")
async def update_settings(settings: dict, _token: str = Depends(verify_auth_token)):
    if "hotkey" in settings:
        if not isinstance(settings["hotkey"], str) or not settings["hotkey"].strip():
            raise HTTPException(status_code=400, detail="hotkey must be a non-empty string")
        _settings["hotkey"] = settings["hotkey"].strip()

    if "mode" in settings:
        if settings["mode"] not in ("hold", "toggle"):
            raise HTTPException(status_code=400, detail="mode must be 'hold' or 'toggle'")
        _settings["mode"] = settings["mode"]

    if "action" in settings:
        if settings["action"] != "clipboard":
            raise HTTPException(status_code=400, detail="action must be 'clipboard'")
        _settings["action"] = "clipboard"

    save_settings()
    return _settings


@app.get("/api/status")
async def get_status():
    return JSONResponse({
        "state": state.state,
        "engine": state.engine,
        "language": LANGUAGE,
        "transcribe_command": bool(os.getenv("TRANSCRIBE_COMMAND", "").strip()),
        "connected_clients": len(state.connected_clients),
        "actions_loaded": len(_actions),
        "last_activity": state.last_activity,
    })


_transcription_slots = asyncio.Semaphore(1)
_transcription_requests: deque[float] = deque()


def _check_transcription_rate_limit(now: float) -> None:
    cutoff = now - 60
    while _transcription_requests and _transcription_requests[0] <= cutoff:
        _transcription_requests.popleft()
    if len(_transcription_requests) >= MAX_TRANSCRIPTIONS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Transcription rate limit exceeded.")
    _transcription_requests.append(now)


async def _copy_bounded_upload(file: UploadFile, destination) -> int:
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Audio upload is too large.")
        destination.write(chunk)
    return total


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...), _token: str = Depends(verify_auth_token)):
    suffix = Path(file.filename or "recording.webm").suffix or ".webm"
    temp_path: Path | None = None

    _check_transcription_rate_limit(time.monotonic())
    async with _transcription_slots:
        await state.set_state("transcribing")
        try:
            with NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOAD_DIR) as tmp:
                temp_path = Path(tmp.name)
                size = await _copy_bounded_upload(file, tmp)
            if size == 0:
                raise HTTPException(status_code=400, detail="Audio upload is empty.")

            logger.info(f"Transcribing uploaded audio: {temp_path.name}")
            text = await asyncio.to_thread(transcriber.transcribe_file, temp_path)
            text = text.strip()
            if text:
                state.add_transcription(text)
                await state.broadcast({
                    "type": "transcription",
                    "text": text,
                    "is_final": True,
                })
            return JSONResponse({
                "text": text,
                "engine": transcriber.engine,
                "model": getattr(transcriber, "model_name", ""),
                "language": LANGUAGE,
            })
        except HTTPException:
            raise
        except TranscriptionError as e:
            logger.error(f"Transcription failed: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e
        except Exception as e:
            logger.exception("Unexpected transcription error")
            raise HTTPException(status_code=500, detail="Transcription failed.") from e
        finally:
            await state.set_state("idle")
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    logger.warning(f"Could not remove temp upload: {temp_path}")


@app.get("/api/config")
async def get_config(_token: str = Depends(verify_auth_token)):
    return JSONResponse({
        "engine": ENGINE,
        "language": LANGUAGE,
        "ws_url": "ws://localhost:8080/ws",
        "actions": _actions,
        "settings": _settings,
    })


@app.get("/api/history")
async def get_history(_token: str = Depends(verify_auth_token)):
    return state.transcription_history[:20]


# ── WebSocket ───────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not _loopback_hostname(ws.headers.get("host")) or not _allowed_origin(ws.headers.get("origin")):
        await ws.close(code=1008, reason="Loopback origin required")
        return
    if not _has_valid_bearer(ws.headers.get("authorization")):
        await ws.close(code=1008, reason="Authentication required")
        return
    await ws.accept()
    state.connected_clients.add(ws)
    logger.info(f"WebSocket client connected ({len(state.connected_clients)} total)")

    # Send current state so new clients are in sync
    await ws.send_text(json.dumps({
        "type": "status",
        "state": state.state,
    }))

    role: str | None = None

    try:
        while True:
            message = await ws.receive()

            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    logger.warning("Ignoring malformed WebSocket message")
                    continue
                if not isinstance(data, dict):
                    logger.warning("Ignoring non-object WebSocket message")
                    continue
                msg_type = data.get("type")

                if msg_type == "hello":
                    requested_role = data.get("role", "ui")
                    if requested_role not in {"client", "ui"}:
                        await ws.close(code=1008, reason="Invalid client role")
                        return
                    role = requested_role
                    logger.info(f"Client registered as '{role}'")
                    await ws.send_text(json.dumps({
                        "type": "welcome",
                        "role": role,
                        "engine": state.engine,
                    }))

                elif msg_type == "status":
                    if role != "client":
                        await ws.close(code=1008, reason="Producer role required")
                        return
                    # Client reports its state change
                    new_state = data.get("state", "idle")
                    if new_state not in {"idle", "listening", "transcribing"}:
                        await ws.close(code=1008, reason="Invalid state")
                        return
                    await state.set_state(new_state)
                    logger.info(f"State changed to '{new_state}' by {role}")

                elif msg_type == "transcription":
                    if role != "client":
                        await ws.close(code=1008, reason="Producer role required")
                        return
                    # Client sends transcription result for history/logging
                    text = data.get("text", "")
                    is_final = data.get("is_final", True)
                    if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT_CHARS or not isinstance(is_final, bool):
                        await ws.close(code=1008, reason="Invalid transcription payload")
                        return
                    if text and is_final:
                        state.add_transcription(text)
                    # Broadcast to all observers (UI dashboards)
                    await state.broadcast({
                        "type": "transcription",
                        "text": text,
                        "is_final": is_final,
                    })
                    logger.info("Transcription received (%d characters)", len(text))

                elif msg_type == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        state.connected_clients.discard(ws)
