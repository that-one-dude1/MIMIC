"""FakeBridge and MujocoBridge: isolate the controller from the MuJoCo/Unitree
SDK specifics. Both expose trigger(command) and telemetry_loop(callback).
Selected via MIMIC_BRIDGE=fake|mujoco (default: fake).

MuJoCo is a visual aid only: MujocoBridge doesn't poll real sim state, so its
telemetry is made-up too, same as FakeBridge's.
"""
import asyncio
import os
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GO2_SIM_SCRIPT = BASE_DIR / "tools" / "go2_sim.py"
GO2_CONDA_PYTHON = os.environ.get("MIMIC_GO2_PYTHON",
    os.path.expanduser("~/anaconda3/envs/unitree_robot/bin/python3"),
)
GO2_COMMAND_FILE = BASE_DIR / ".go2_command"


class FakeBridge:
    def __init__(self):
        self.status = "IDLE"
        self._t = 0.0

    def trigger(self, command: str):
        print(f"[FakeBridge] trigger: {command}")
        self.status = command.upper()

    async def telemetry_loop(self, callback):
        while True:
            await asyncio.sleep(1)
            self._t += 1
            await callback(
                {
                    "connected": True,
                    "status": self.status,
                    "position": {"x": round(0.1 * self._t, 2), "y": 0.0, "z": 0.0},
                }
            )

    def close(self):
        pass


class MujocoBridge:
    def __init__(self):
        if not os.path.exists(GO2_CONDA_PYTHON):
            raise FileNotFoundError(f"MuJoCo python not found at {GO2_CONDA_PYTHON}. Set MIMIC_GO2_PYTHON to the right interpreter.")
        self.status = "IDLE"
        self._t = 0.0
        GO2_COMMAND_FILE.write_text("stand")
        self._process = subprocess.Popen([GO2_CONDA_PYTHON, str(GO2_SIM_SCRIPT), str(GO2_COMMAND_FILE)])

    def trigger(self, command: str):
        print(f"[MujocoBridge] trigger: {command}")
        self.status = command.upper()
        GO2_COMMAND_FILE.write_text(command)

    async def telemetry_loop(self, callback):
        while True:
            await asyncio.sleep(1)
            self._t += 1
            await callback(
                {
                    "connected": self._process.poll() is None,
                    "status": self.status,
                    "position": {"x": round(0.1 * self._t, 2), "y": 0.0, "z": 0.0},
                }
            )

    def close(self):
        self._process.terminate()


def create_bridge():
    kind = os.environ.get("MIMIC_BRIDGE", "fake")
    if kind == "mujoco":
        return MujocoBridge()
    return FakeBridge()
