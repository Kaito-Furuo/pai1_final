"""MCP を介さずにツール関数を直接呼ぶスクリプトデモ。

ライブビューア（http://localhost:8765/）の確認や、動画素材（B ロール）の生成に使う。
    PYTHONPATH=. .venv/bin/python scripts/scripted_demo.py --layout sorting --record sorting --hold 20
"""
import argparse, json, os, sys, time

parser = argparse.ArgumentParser()
parser.add_argument("--layout", default="sorting")
parser.add_argument("--record", default="")
parser.add_argument("--hold", type=float, default=0.0, help="終了後にビューアを開いたまま待つ秒数")
parser.add_argument("--realtime", default=os.environ.get("ROBOT_REALTIME", "1.0"))
args = parser.parse_args()
os.environ["ROBOT_REALTIME"] = args.realtime

from robot_mcp import server as S


def t(tool, **kw):
    res = getattr(S, tool)(**kw)
    text = res if isinstance(res, str) else next((c for c in res if isinstance(c, str)), "")
    print(f"[{tool}] {text[:200]}", file=sys.stderr)
    return json.loads(text) if text.startswith("{") else None


r = S.robot()
print("viewer:", r.url, file=sys.stderr)
t("reset_scene", layout=args.layout)
if args.record:
    t("start_recording", name=args.record)
if args.layout == "sorting":
    t("say", text="赤いブロックは赤いトレイへ、青いブロックは青いトレイへ仕分けします")
    det = t("detect_objects")
    for o in det["objects"]:
        if o["kind"] == "cube" and o["color"] in ("red", "blue"):
            t("say", text=f"{o['color_ja']}のブロック {o['id']} を {o['color_ja']}いトレイへ運びます")
            p = t("pick", object_id=o["id"])
            if p and p.get("success"):
                t("place", target=f"{o['color']}_tray")
                t("verify", object_id=o["id"], target=f"{o['color']}_tray")
    t("say", text="仕分けが完了しました。緑のブロックは対象外なので残します")
elif args.layout == "stacking":
    t("say", text="大きい順に積み上げます: 青 → 緑 → 赤")
    t("pick", object_id="green_cube"); t("place", target="blue_cube")
    t("pick", object_id="red_cube"); t("place", target="green_cube")
    t("say", text="3 段に積み上がりました")
else:
    t("say", text="赤いブロックを青いトレイに入れます")
    t("pick", object_id="red_cube"); t("place", target="blue_tray"); t("verify", object_id="red_cube", target="blue_tray")
t("home")
if args.record:
    t("stop_recording")
if args.hold > 0:
    print(f"holding viewer for {args.hold}s ...", file=sys.stderr)
    end = time.time() + args.hold
    while time.time() < end:
        r.world.step(17)  # ビューアのフレームを流し続ける
