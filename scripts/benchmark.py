"""ランダム配置で pick & place を繰り返し、成功率と設置誤差を集計する（レポート用）。

    PYTHONPATH=. ROBOT_REALTIME=0 .venv/bin/python scripts/benchmark.py --trials 20
"""
import argparse, json, math, os, sys, time

parser = argparse.ArgumentParser()
parser.add_argument("--trials", type=int, default=20)
parser.add_argument("--seed0", type=int, default=100)
parser.add_argument("--out", default="")
args = parser.parse_args()
os.environ.setdefault("ROBOT_REALTIME", "0")
os.environ.setdefault("ROBOT_VIEWER_PORT", "8799")
os.environ.setdefault("ROBOT_RECORD_DIR", "/tmp/robot_bench_logs")

from robot_mcp import server as S

r = S.robot()
rows = []
t0 = time.time()
for i in range(args.trials):
    seed = args.seed0 + i
    det = json.loads(S.reset_scene("random", seed))
    cubes = [o for o in det["objects"] if o["kind"] == "cube"]
    trays = [o for o in det["objects"] if o["kind"] == "tray"]
    for k, c in enumerate(cubes):
        tray = trays[k % len(trays)]
        p = json.loads(next(x for x in S.pick(c["id"]) if isinstance(x, str)))
        row = dict(seed=seed, cube=c["id"], size=c["size"], tray=tray["id"], pick=bool(p.get("success")),
                   place=False, verify=False, err_mm=None)
        if row["pick"]:
            q = json.loads(next(x for x in S.place(tray["id"]) if isinstance(x, str)))
            row["place"] = bool(q.get("success"))
            if q.get("check"):
                row["err_mm"] = q["check"]["xy_error_mm"]
            v = json.loads(S.verify(c["id"], tray["id"]))
            row["verify"] = bool(v.get("ok"))
        rows.append(row)
        print(f"seed {seed} {c['id']:14s} pick={row['pick']} place={row['place']} verify={row['verify']} err={row['err_mm']}", file=sys.stderr)
n = len(rows)
summary = dict(trials=args.trials, attempts=n, pick_success=sum(r_["pick"] for r_ in rows), place_success=sum(r_["place"] for r_ in rows),
               verify_ok=sum(r_["verify"] for r_ in rows),
               err_mm_mean=round(sum(r_["err_mm"] for r_ in rows if r_["err_mm"] is not None) / max(1, sum(1 for r_ in rows if r_["err_mm"] is not None)), 2),
               err_mm_max=max((r_["err_mm"] for r_ in rows if r_["err_mm"] is not None), default=None),
               wall_s=round(time.time() - t0, 1))
print(json.dumps(summary, ensure_ascii=False, indent=2))
if args.out:
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dict(summary=summary, rows=rows), f, ensure_ascii=False, indent=1)
