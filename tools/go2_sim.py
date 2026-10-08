#!/usr/bin/env python3
"""Live-controllable variant of go2_walk.py.

Instead of go2_walk.py's fixed elapsed-time sequence (stand -> walk -> sit),
this polls a small command file for the current gait ("stand"/"walk"/"sit"),
written by MujocoBridge.trigger(). Launched as a subprocess from the
unitree_robot conda env, since mujoco isn't installed in the controller's venv.
"""
import sys
import time
import os

import mujoco
import mujoco.viewer
import numpy as np

# --- CONFIGURATION ---
model_path = os.path.expanduser("~/unitree_mujoco/unitree_robots/go2/scene.xml")
# Per-joint gains: [hip, thigh, calf] repeated for each of the 4 legs (matches ctrl order)
Kp = np.array([60.0, 60.0, 150.0] * 4)  # Calf gets much higher gain to overcome ground contact loading
Kd = np.array([5.0, 5.0, 8.0] * 4)

# --- KEYFRAME POSES ---
# The Go2 has 12 motors.
q_stand = np.array([
    0.0, 0.9, -1.8,  # Front Right
    0.0, 0.9, -1.8,  # Front Left
    0.0, 0.9, -1.8,  # Rear Right
    0.0, 0.9, -1.8,  # Rear Left
])
q_sit = np.array([
    -0.2, 1.5, -2.6,  # Front Right
    0.2, 1.5, -2.6,  # Front Left
    -0.2, 1.5, -2.6,  # Rear Right
    0.2, 1.5, -2.6,  # Rear Left
])


def get_walking_target(t, base_pose):
    """Generates a simple trot gait (sine wave)"""
    target = base_pose.copy()

    freq = 1.75  # Speed (Steps per second)
    thigh_amp = 0.8  # Thigh swing amplitude in radians
    calf_amp = 0.4  # Calf lift amplitude in radians

    phase = t * freq * 2 * np.pi
    thigh_swing = np.sin(phase) * thigh_amp
    # Calf lags 90 degrees behind thigh: lifts as the leg swings forward, plants as it swings back
    calf_lift = np.sin(phase - np.pi / 2) * calf_amp

    # Pair 1: Front Right (thigh=1, calf=2) & Rear Left (thigh=10, calf=11)
    target[1] -= thigh_swing
    target[2] += calf_lift
    target[10] -= thigh_swing
    target[11] += calf_lift

    # Pair 2: Front Left (thigh=4, calf=5) & Rear Right (thigh=7, calf=8) - opposite phase
    target[4] += thigh_swing
    target[5] -= calf_lift
    target[7] += thigh_swing
    target[8] -= calf_lift

    return target


def read_command(command_path, last_command):
    try:
        with open(command_path) as f:
            command = f.read().strip()
    except OSError:
        return last_command
    return command if command in ("stand", "walk", "sit") else last_command


def main():
    if len(sys.argv) != 2:
        print("usage: go2_sim.py <command_file>")
        sys.exit(1)
    command_path = sys.argv[1]

    print(f"Loading model: {model_path}")
    model = mujoco.MjModel.from_xml_path(model_path)
    data = mujoco.MjData(model)

    # Start from the model's "home" keyframe instead of the raw XML default pose
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if home_id >= 0:
        mujoco.mj_resetDataKeyframe(model, data, home_id)

    # qpos order (after the 7 free-joint values) is FL, FR, RL, RR.
    # ctrl order (and our q_stand/q_sit arrays) is FR, FL, RR, RL.
    qpos_to_ctrl = [3, 4, 5, 0, 1, 2, 9, 10, 11, 6, 7, 8]

    with mujoco.viewer.launch_passive(model, data) as viewer:
        viewer.cam.distance = 3.0
        viewer.cam.lookat[:] = [0, 0, 0.5]

        print("Starting Simulation...")

        current_command = "stand"
        walk_phase_start = time.time()
        last_poll = 0.0

        while viewer.is_running():
            now = time.time()

            if now - last_poll > 0.2:
                last_poll = now
                new_command = read_command(command_path, current_command)
                if new_command != current_command:
                    print(f"Command changed: {current_command} -> {new_command}")
                    if new_command == "walk":
                        walk_phase_start = now
                    current_command = new_command

            if current_command == "walk":
                target_q = get_walking_target(now - walk_phase_start, q_stand)
            elif current_command == "sit":
                target_q = q_sit
            else:
                target_q = q_stand

            # --- MOTOR CONTROL (The Muscles) ---
            current_pos = data.qpos[7:][qpos_to_ctrl]  # reordered to match ctrl/FR-FL-RR-RL order
            current_vel = data.qvel[6:][qpos_to_ctrl]  # same reorder for velocities

            diff = target_q - current_pos
            data.ctrl[:] = (Kp * diff) - (Kd * current_vel)

            # Step Physics
            mujoco.mj_step(model, data)
            viewer.sync()
            time.sleep(model.opt.timestep)


if __name__ == "__main__":
    main()
