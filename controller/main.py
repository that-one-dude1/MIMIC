import asyncio
import json
import socket
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from controller.bridge import create_bridge

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
AUDIO_DIR = BASE_DIR / "audio"

Role = Literal["commander", "receiver", "operator"]

ASSET_DISPLAY = {
    "hold_position": "HOLD POSITION",
    "proceed_to_alpha": "PROCEED TO ALPHA",
    "report_status": "REPORT STATUS",
    "return_to_checkpoint": "RETURN TO CHECKPOINT",
}

MODES = {"FORWARD", "DELAY", "MASK", "SWAP"}
DELAY_PRESETS_MS = {1500, 2000, 3000}
MASK_EFFECTS = {"STATIC_SHORT", "STATIC_LONG", "NOISE_BURST", "DROPOUT_SHORT"}

SWAP_TABLE = {
    "proceed_to_alpha": "return_to_checkpoint",
    "hold_position": "move_to_beta",
    "report_status": "stand_by",
    "return_to_checkpoint": "stand_by",
}

SWAP_DISPLAY = {
    "return_to_checkpoint": "RETURN TO CHECKPOINT",
    "move_to_beta": "MOVE TO BETA",
    "stand_by": "STAND BY",
}

# Maps the effective asset (the swap target when in SWAP mode, otherwise the
# commanded asset itself) to a go2_sim.py gait. Reuses its 3 poses across all
# 6 possible ids.
GAIT_COMMANDS = {
    "hold_position": "stand",
    "proceed_to_alpha": "walk",
    "report_status": "stand",
    "return_to_checkpoint": "sit",
    "move_to_beta": "walk",
    "stand_by": "stand",
}

DEFAULT_TELEMETRY = {"connected": True, "status": "IDLE", "position": {"x": 0.0, "y": 0.0, "z": 0.0}}


class RegisterMessage(BaseModel):
    type: Literal["register"]
    client: Role


class MessageMessage(BaseModel):
    type: Literal["message"]
    asset: str


class SetModeMessage(BaseModel):
    type: Literal["set_mode"]
    mode: str
    delay_ms: int | None = None
    effect: str | None = None


class InvalidSwapError(Exception):
    pass


def resolve(asset: str, mode: str, effect: str | None) -> str:
    if mode in ("FORWARD", "DELAY"):
        return f"source/{asset}.wav"
    if mode == "MASK":
        return f"masked/{asset}__{effect}.wav"
    if mode == "SWAP":
        target = SWAP_TABLE.get(asset)
        if target is None:
            raise InvalidSwapError(asset)
        return f"swaps/{target}.wav"
    raise ValueError(f"unknown mode {mode}")


def wav_duration_seconds(relative_path: str) -> float:
    with wave.open(str(AUDIO_DIR / relative_path), "rb") as f:
        return f.getnframes() / f.getframerate()


def lan_ip() -> str:
    # UDP connect doesn't send any packets, it just asks the OS which local
    # address it would use to route to that destination.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@dataclass
class State:
    mode: str = "FORWARD"
    delay_ms: int = 0
    effect: str | None = None
    queue: list = field(default_factory=list)
    events: list = field(default_factory=list)
    clients: dict = field(default_factory=dict)  # WebSocket -> role
    tasks: list = field(default_factory=list)  # asyncio.Task, delay + completion timers
    active_message: dict | None = None
    telemetry: dict = field(default_factory=lambda: dict(DEFAULT_TELEMETRY))

    def to_dict(self):
        return {
            "mode": self.mode,
            "delay_ms": self.delay_ms,
            "effect": self.effect,
            "queue": self.queue,
            "events": self.events,
            "online": sorted(set(self.clients.values())),
            "active_message": self.active_message,
            "telemetry": self.telemetry,
        }

    async def broadcast(self):
        payload = json.dumps({"type": "state", "state": self.to_dict()})
        for ws in list(self.clients):
            await ws.send_text(payload)


state = State()
bridge = create_bridge()


def add_event(kind: str, **details):
    state.events.append({"type": kind, "timestamp": time.time(), **details})


async def dispatch_play(message_id: str, asset: str, filename: str, mode: str, effect: str | None, delay_ms: int):
    input_display = ASSET_DISPLAY[asset]
    if mode == "SWAP":
        effective_asset = SWAP_TABLE[asset]
        output_display = SWAP_DISPLAY[effective_asset]
    else:
        effective_asset = asset
        output_display = input_display

    bridge.trigger(GAIT_COMMANDS[effective_asset])

    play_payload = json.dumps(
        {
            "type": "play",
            "file": filename,
            "asset": asset,
            "display": input_display,
            "message_id": message_id,
        }
    )
    for ws, role in state.clients.items():
        if role == "receiver":
            await ws.send_text(play_payload)

    add_event("playing", message_id=message_id, asset=asset, file=filename)
    state.active_message = {
        "message_id": message_id,
        "asset": asset,
        "mode": mode,
        "effect": effect,
        "delay_ms": delay_ms,
        "file": filename,
        "input_display": input_display,
        "output_display": output_display,
    }
    await state.broadcast()

    duration = wav_duration_seconds(filename)

    async def complete_after_playback():
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            return
        add_event("complete", message_id=message_id, asset=asset)
        if state.active_message and state.active_message["message_id"] == message_id:
            state.active_message = None
        await state.broadcast()

    task = asyncio.create_task(complete_after_playback())
    task.add_done_callback(lambda t: state.tasks.remove(t) if t in state.tasks else None)
    state.tasks.append(task)


async def release_after_delay(message_id: str, asset: str, filename: str, delay_ms: int, effect: str | None):
    try:
        await asyncio.sleep(delay_ms / 1000)
    except asyncio.CancelledError:
        return

    state.queue = [entry for entry in state.queue if entry["message_id"] != message_id]
    add_event("released", message_id=message_id, asset=asset)
    await dispatch_play(message_id, asset, filename, "DELAY", effect, delay_ms)


async def handle_message(message: MessageMessage):
    message_id = uuid.uuid4().hex[:8]
    asset = message.asset

    add_event("received", message_id=message_id, asset=asset)

    if asset not in ASSET_DISPLAY:
        add_event("error", message_id=message_id, asset=asset, detail="unknown asset")
        await state.broadcast()
        return

    try:
        filename = resolve(asset, state.mode, state.effect)
    except InvalidSwapError:
        add_event("error", message_id=message_id, asset=asset, detail="INVALID_SWAP")
        await state.broadcast()
        return

    if state.mode == "DELAY":
        state.queue.append(
            {"message_id": message_id, "asset": asset, "release_at": time.time() + state.delay_ms / 1000}
        )
        add_event("queued", message_id=message_id, asset=asset, delay_ms=state.delay_ms)
        await state.broadcast()
        task = asyncio.create_task(
            release_after_delay(message_id, asset, filename, state.delay_ms, state.effect)
        )
        task.add_done_callback(lambda t: state.tasks.remove(t) if t in state.tasks else None)
        state.tasks.append(task)
        return

    await dispatch_play(message_id, asset, filename, state.mode, state.effect, state.delay_ms)


async def handle_set_mode(message: SetModeMessage):
    if message.mode not in MODES:
        add_event("error", detail="unknown mode", mode=message.mode)
        await state.broadcast()
        return

    state.mode = message.mode

    if message.delay_ms is not None:
        if message.delay_ms not in DELAY_PRESETS_MS:
            add_event("error", detail="unknown delay_ms", delay_ms=message.delay_ms)
        else:
            state.delay_ms = message.delay_ms

    if message.effect is not None:
        if message.effect not in MASK_EFFECTS:
            add_event("error", detail="unknown effect", effect=message.effect)
        else:
            state.effect = message.effect

    await state.broadcast()


app = FastAPI()

app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


async def on_telemetry(telemetry: dict):
    state.telemetry = telemetry
    await state.broadcast()


@app.on_event("startup")
async def startup_event():
    app.state.telemetry_task = asyncio.create_task(bridge.telemetry_loop(on_telemetry))


@app.on_event("shutdown")
async def shutdown_event():
    app.state.telemetry_task.cancel()
    bridge.close()


@app.get("/commander")
def commander_page():
    return FileResponse(STATIC_DIR / "commander.html")


@app.get("/receiver")
def receiver_page():
    return FileResponse(STATIC_DIR / "receiver.html")


@app.get("/operator")
def operator_page():
    return FileResponse(STATIC_DIR / "operator.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/network")
def network_info(request: Request):
    return {"host": lan_ip(), "port": request.url.port or 80}


@app.get("/api/state")
def get_state():
    return state.to_dict()


@app.post("/api/demo/reset")
async def demo_reset():
    global state
    for task in state.tasks:
        task.cancel()
    clients = state.clients
    state = State()
    state.clients = clients
    await state.broadcast()
    return state.to_dict()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
        register = RegisterMessage.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError, WebSocketDisconnect):
        await websocket.close(code=1008)
        return

    state.clients[websocket] = register.client
    await state.broadcast()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "message":
                try:
                    message = MessageMessage.model_validate(data)
                except ValidationError:
                    continue
                await handle_message(message)
            elif data.get("type") == "set_mode":
                try:
                    set_mode = SetModeMessage.model_validate(data)
                except ValidationError:
                    continue
                await handle_set_mode(set_mode)
            elif data.get("type") == "playback_error":
                add_event("error", message_id=data.get("message_id"), detail=f"playback failed: {data.get('detail')}")
                await state.broadcast()
    except WebSocketDisconnect:
        state.clients.pop(websocket, None)
        await state.broadcast()
