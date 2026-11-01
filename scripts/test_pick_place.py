"""ヘッドレスで pick & place を一周させる動作テスト。"""
import sys, time, math, os
import numpy as np
import imageio.v3 as iio
import imageio

from robot_mcp.sim import World, TCP_HOME
from robot_mcp.skills import Arm
from robot_mcp.ik import rot_down

out = sys.argv[1] if len(sys.argv) > 1 else "scratch"
os.makedirs(out, exist_ok=True)
layout = sys.argv[2] if len(sys.argv) > 2 else "basic"

t0 = time.time()
w = World(layout=layout, realtime=0.0)
arm = Arm(w)
print(f"world built in {time.time()-t0:.2f}s; nq={w.model.nq}; objects={list(w.objects)}")

# 録画
writer = imageio.get_writer(os.path.join(out, "test.mp4"), fps=30, codec="libx264", quality=8, macro_block_size=None)
w.frame_callbacks.append(lambda world, rgb: writer.append_data(rgb))

# 初期画像
w.settle(0.3)
for cam in ["front", "top", "wrist", "side"]:
    iio.imwrite(os.path.join(out, f"{cam}_0.png"), w.render(cam))
pos, rot = w.tcp_pose()
print("TCP at start:", pos.round(3), "z-axis:", rot[:, 2].round(2))

# IK 精度チェック
t0 = time.time()
for p in [TCP_HOME, [0.5, 0.15, 0.03], [0.6, -0.25, 0.03], [0.4, -0.2, 0.2], [0.62, 0.25, 0.12]]:
    q, ok, ep, er = arm.ik.solve(p, rot_down(0.3), arm.q)
    print(f"IK {np.round(p,3)} -> ok={ok} pos_err={ep*1000:.2f}mm rot_err={er:.4f}")
print(f"IK 5 solves: {time.time()-t0:.3f}s")

r = arm.home(); print("home:", r.message, "tcp=", arm.cmd_pos.round(3))

# pick red → blue tray
info = w.object_info("red_cube"); print("red_cube before:", info)
t0 = time.time()
res = arm.pick(info["position"], info["yaw"], info["size"], name="red_cube")
print(f"pick: {res} ({time.time()-t0:.1f}s wall, sim t={w.data.time:.2f})")
iio.imwrite(os.path.join(out, "front_1_holding.png"), w.render("front"))
after = w.object_info("red_cube"); print("red_cube after lift:", after["position"])

tray = w.object_info("blue_tray"); print("blue_tray:", tray)
res2 = arm.place([tray["position"][0], tray["position"][1], tray["top_z"]], info["size"])
print("place:", res2)
w.settle(0.5)
final = w.object_info("red_cube"); print("red_cube final:", final["position"], "yaw", final["yaw"])
dx = math.hypot(final["position"][0]-tray["position"][0], final["position"][1]-tray["position"][1])
print(f"=> in tray: {dx < 0.03 and abs(final['position'][2] - (tray['top_z']+0.02)) < 0.01}  (xy offset {dx*1000:.1f} mm)")
arm.home()
iio.imwrite(os.path.join(out, "front_2_done.png"), w.render("front"))
iio.imwrite(os.path.join(out, "top_2_done.png"), w.render("top"))
writer.close()
print(f"sim time {w.data.time:.1f}s, wall {time.time():.0f}")
