"""知覚モジュールの精度をシミュレータ真値と比較する。"""
import os, sys, math
import imageio.v3 as iio
from robot_mcp.sim import World
from robot_mcp.perception import Perception

out = sys.argv[1]; os.makedirs(out, exist_ok=True)
w = World(layout="basic", seed=3, realtime=0.0)
per = Perception(w)
for layout in ["basic", "sorting", "stacking", "random"]:
    if layout != "basic":
        w.reset(layout, seed=3); per.reset_ids()
    w.settle(0.3)
    dets, img = per.detect()
    iio.imwrite(os.path.join(out, f"det_{layout}.png"), img)
    truth = {o["name"]: o for o in w.all_object_info()}
    print(f"== {layout}: {len(dets)} detections / {len(truth)} objects")
    worst = 0
    for d in dets:
        # 同色・同種の真値のうち最近傍と比較
        cands = [t for t in truth.values() if t["color"] == d.color and t["kind"] == d.kind]
        t = min(cands, key=lambda t: math.hypot(d.position[0]-t["position"][0], d.position[1]-t["position"][1])) if cands else None
        if t is None:
            print(f"  {d.id}: NO TRUTH of same color/kind  pos={[round(v,3) for v in d.position]}")
            continue
        err = math.hypot(d.position[0]-t["position"][0], d.position[1]-t["position"][1]); worst = max(worst, err)
        dz = d.position[2]-t["position"][2]
        print(f"  {d.id:14s} kind={d.kind:4s} xy_err={err*1000:5.1f}mm dz={dz*1000:5.1f}mm size={d.size*1000:4.1f}/{t['size']*1000:.0f}mm yaw={math.degrees(d.yaw):6.1f}/{math.degrees(t['yaw']):6.1f}deg area={d.area_px}")
    print(f"  worst xy err: {worst*1000:.1f} mm")
