#!/usr/bin/env python3

import mujoco
import mujoco.viewer
import numpy as np
import os
import time
# --- CONFIGURATION ---
# Path to the Unitree Go2 model
model_path = os.path.expanduser("~/unitree_mujoco/unitree_robots/go2/scene.xml")
# Control Parameters (Stiffness of the muscles)
# Per-joint gains: [hip, thigh, calf] repeated for each of the 4 legs (matches ctrl order)
Kp = np.array([60.0, 60.0, 150.0] * 4) # Calf gets much higher gain to overcome ground contact loading
Kd = np.array([5.0, 5.0, 8.0] * 4)
# --- KEYFRAME POSES ---
# The Go2 has 12 motors.
# 1. Standing Pose (Legs straightish)
q_stand = np.array([
 0.0, 0.9, -1.8, # Front Right
 0.0, 0.9, -1.8, # Front Left
 0.0, 0.9, -1.8, # Rear Right
 0.0, 0.9, -1.8 # Rear Left
])
# 2. Sitting Pose (Legs tucked)
q_sit = np.array([
 -0.2, 1.5, -2.6, # Front Right
 0.2, 1.5, -2.6, # Front Left
 -0.2, 1.5, -2.6, # Rear Right
 0.2, 1.5, -2.6 # Rear Left
])
def get_walking_target(t, base_pose):
 """Generates a simple trot gait (sine wave)"""
 target = base_pose.copy()
 
 freq = 1.75 # Speed (Steps per second)
 thigh_amp = 0.8 # Thigh swing amplitude in radians (was 0.3 - doubled to test if leg needs to swing further back to unweight)
 calf_amp = 0.4 # Calf lift amplitude in radians
 
 phase = t * freq * 2 * np.pi
 thigh_swing = np.sin(phase) * thigh_amp
 # Calf lags 90 degrees behind thigh: lifts as the leg swings forward, plants as it swings back
 calf_lift = np.sin(phase - np.pi / 2) * calf_amp
 
 # Move diagonal leg pairs together
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
# --- MAIN LOOP ---
def main():
 print(f"Loading model: {model_path}")
 model = mujoco.MjModel.from_xml_path(model_path)
 data = mujoco.MjData(model)

 # Start from the model's "home" keyframe instead of the raw XML default pose
 home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
 if home_id >= 0:
  mujoco.mj_resetDataKeyframe(model, data, home_id)

 # qpos order (after the 7 free-joint values) is FL, FR, RL, RR.
 # ctrl order (and our q_stand/q_sit arrays) is FR, FL, RR, RL.
 # qpos_to_ctrl[i] tells us: ctrl-ordered slot i comes from this index in qpos[7:]
 qpos_to_ctrl = [3, 4, 5, 0, 1, 2, 9, 10, 11, 6, 7, 8]
 # Launch the visualizer
 with mujoco.viewer.launch_passive(model, data) as viewer:
  start_time = time.time()
 
 # Set camera view
  viewer.cam.distance = 3.0
  viewer.cam.lookat[:] = [0, 0, 0.5]
 
  print("Starting Simulation...")

  # Find the FR foot body/geom so we can check ground-truth lift and contact state
  fr_foot_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "FR_calf")
  fr_geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "FR")
  last_print = 0.0
 
  while viewer.is_running():
   now = time.time() - start_time
 
 # --- STATE MACHINE (The Plan) ---
 # 0-2s: Stand Up
   if now < 2.0:
    target_q = q_stand
    if now < 0.1:
     print("Phase 1: STANDING")
 
 # 2-8s: Walk Forward (Approx 10 steps)
   elif now < 8.0:
    target_q = get_walking_target(now - 2.0, q_stand) # time since walk phase started
    if now > 2.0 and now < 2.1: print("Phase 2: WALKING")
 
 # 8s+: Sit Down
   else:
    target_q = q_sit
    if now > 8.0 and now < 8.1: print("Phase 3: SITTING")
 # --- MOTOR CONTROL (The Muscles) ---
 # Calculate the motor torques using PD control
   current_pos = data.qpos[7:][qpos_to_ctrl] # reordered to match ctrl/FR-FL-RR-RL order
   current_vel = data.qvel[6:][qpos_to_ctrl] # same reorder for velocities

   diff = target_q - current_pos
   data.ctrl[:] = (Kp * diff) - (Kd * current_vel)

   if 2.0 < now < 8.0 and now - last_print > 0.2:
    last_print = now
    foot_z = data.xpos[fr_foot_id][2]
    # Check mujoco's actual contact list to see if the FR foot is touching anything
    fr_in_contact = any(
     (c.geom1 == fr_geom_id or c.geom2 == fr_geom_id) for c in data.contact[:data.ncon]
    )
    print(f"t={now:.2f} target_calf={target_q[2]:.3f} actual_calf={current_pos[2]:.3f} ctrl_torque={data.ctrl[2]:.2f} z={foot_z:.3f} contact={fr_in_contact}")
 # Step Physics
   mujoco.mj_step(model, data)
   viewer.sync()
   time.sleep(model.opt.timestep)
if __name__ == "__main__":
 main()
