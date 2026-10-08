# M.I.M.I.C.

A software-only, 48-hour Wizard-of-Oz demo of a message manipulation pipeline.
A commander sends a predefined message, a controller processes it in one of four
modes, a receiver plays the result, and a simulated Unitree Go2 robot reacts.

Everything here is faked. It only uses pre-recorded demo clips. There is no
radio, no SDR, no interception of real traffic (since that's illegal) and no voice cloning.

This is made for Ubuntu-based systems, I'm unsure if MuJoCo works on Arch or Debian.

## How it works

```
commander  --\                                      /--> receiver (plays audio)
operator   ---+--> WebSocket --> FastAPI controller +
receiver   --/                                      \--> bridge --> MuJoCo Go2 sim
```

- **Browser pages** (`commander`, `operator`, `receiver`) are plain HTML/JS. Each opens one
  WebSocket to `/ws` and registers its role.
- **Controller** (`controller/main.py`) holds a single shared `State` and broadcasts the
  whole thing to every client on each change.
- **Bridge** (`controller/bridge.py`) turns the processed message into a gait command
  (`stand`, `walk`, `sit`). `FakeBridge` just logs it, `MujocoBridge` writes it to a file
  that a separate sim process watches. The link is one way: telemetry on the pages is
  generated, not read from the sim.

## Modes

| Mode | What it does |
| --- | --- |
| `FORWARD` | plays the original clip unchanged |
| `DELAY` | holds the message for 1.5, 2 or 3 seconds, then plays it |
| `MASK` | plays a pre-rendered clip with static, a noise burst or a dropout |
| `SWAP` | plays a different pre-recorded clip from a fixed lookup table |

Every mode, asset and effect is whitelisted. Anything unknown shows up as an error event
rather than being dropped, even though it should be an impossibility.

## Run it

Needs Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn controller.main:app --host 0.0.0.0 --port 8000
```

Then open, in separate tabs or on separate devices on the same network:

- `http://<host>:8000/commander`
- `http://<host>:8000/receiver`
- `http://<host>:8000/operator`

That runs with `FakeBridge`, so no robot sim is needed.

### With the MuJoCo sim (optional)

The sim runs as a separate process in its own Python environment that has `mujoco`
installed, plus the [unitree_mujoco](https://github.com/unitreerobotics/unitree_mujoco)
models.

```bash
MIMIC_BRIDGE=mujoco MIMIC_GO2_PYTHON=/path/to/python \
  .venv/bin/python -m uvicorn controller.main:app --host 0.0.0.0 --port 8000
```

## Layout

```
controller/    FastAPI app, state, mode resolver, bridge
static/        commander, operator and receiver pages
audio/         source clips, masked variants, swap clips
tools/         render_masks.py, go2_sim.py, ws_client.py (debug CLI)
go2_walk.py    standalone gait experiment placed as a reference
```

`tools/render_masks.py` generates the masked clips offline, so nothing is rendered live
during a demo.
