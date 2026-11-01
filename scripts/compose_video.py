"""録画（MP4 + フレーム壁時計）と Claude のイベントログを合成し、
左: シミュレータ映像 / 右: チャット（ユーザー指示・Claude の発話・ツール呼び出し）の動画を作る。

    .venv/bin/python scripts/compose_video.py --run recordings/demo1 [--out recordings/demo1/composed.mp4]
"""
import argparse, glob, json, os, sys, textwrap, time

import imageio
import imageio.v3 as iio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from robot_mcp.viewer import _load_font  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--run", required=True)
parser.add_argument("--out", default="")
parser.add_argument("--width", type=int, default=1920)
parser.add_argument("--height", type=int, default=1080)
parser.add_argument("--max-gap", type=float, default=3.0, help="ツール呼び出し間の静止フレームを最大何秒入れるか")
parser.add_argument("--speed", type=float, default=1.0, help="再生速度（2 なら 2 倍速）")
args = parser.parse_args()

run = args.run
mp4 = sorted(glob.glob(os.path.join(run, "*.mp4")))
mp4 = [m for m in mp4 if not m.endswith("composed.mp4")]
if not mp4:
    sys.exit("録画 MP4 が見つかりません")
mp4 = mp4[0]
frames_json = os.path.splitext(mp4)[0] + ".frames.json"
meta = json.load(open(frames_json))
ts = meta["timestamps"]
fps = meta["fps"]
runinfo = json.load(open(os.path.join(run, "run.json"))) if os.path.exists(os.path.join(run, "run.json")) else {}

# ---------------------------------------------------------------- チャットのタイムライン
entries = []  # (t, kind, text)
if runinfo.get("prompt"):
    entries.append((runinfo.get("started", ts[0]), "user", runinfo["prompt"]))
HIDE_RESULTS = {"say", "start_recording", "stop_recording", "home"}
JA = {"success": "成功", "ok": "OK", "error": "エラー", "message": "結果"}


def summarize_result(txt: str) -> str:
    """ツール結果の JSON を 1 行に要約する。"""
    try:
        j = json.loads(txt)
        if isinstance(j, dict) and isinstance(j.get("result"), str):  # Claude Code のラッパーを剥がす
            j = json.loads(j["result"])
    except Exception:
        return txt[:80]
    if not isinstance(j, dict):
        return str(j)[:80]
    parts = []
    if "error" in j:
        return f"エラー: {j['error']}"[:120]
    if "objects" in j:
        return f"{len(j['objects'])} 物体: " + ", ".join(o["id"] for o in j["objects"])
    for k in ("success", "ok"):
        if k in j:
            parts.append("成功" if j[k] else "失敗")
            break
    if j.get("gap_mm") is not None:
        parts.append(f"指間隔 {j['gap_mm']} mm")
    if j.get("check"):
        parts.append(f"着地誤差 {j['check'].get('xy_error_mm')} mm")
    if "dx_mm" in j:
        parts.append(f"dx {j['dx_mm']} / dy {j['dy_mm']} / dz {j['dz_mm']} mm")
    if "message" in j and not parts:
        parts.append(str(j["message"]))
    return "  ".join(parts) or "OK"


ev_path = os.path.join(run, "events.jsonl")
tool_names = {}  # tool_use id -> name
if os.path.exists(ev_path):
    for line in open(ev_path, encoding="utf-8"):
        rec = json.loads(line)
        t, ev = rec["t"], rec["ev"]
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "text" and c["text"].strip():
                    entries.append((t, "claude", c["text"].strip()))
                elif c.get("type") == "tool_use":
                    name = c.get("name", "").replace("mcp__robot__", "")
                    tool_names[c.get("id")] = name
                    if name == "ToolSearch":
                        continue
                    arg = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in (c.get("input") or {}).items())
                    entries.append((t, "tool", f"{name}({arg})"))
        elif ev.get("type") == "user":
            for c in ev.get("message", {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    name = tool_names.get(c.get("tool_use_id"), "")
                    if name in HIDE_RESULTS or name == "ToolSearch":
                        continue
                    cc = c.get("content")
                    if isinstance(cc, str):
                        texts = [cc]
                    else:
                        # 画像付き結果には "[Image: source: <ローカルパス>]" のテキストが付くことがあるので、
                        # JSON として読めるブロックだけを使う（パス等を動画に出さない）
                        texts = [x.get("text", "") for x in (cc or []) if x.get("type") == "text"]
                    txt = next((x for x in texts if x.strip().startswith("{")), texts[0] if texts else "")
                    if not txt.strip().startswith("{"):
                        txt = "{}"
                    has_img = (not isinstance(cc, str)) and any(x.get("type") == "image" for x in (cc or []))
                    entries.append((t, "result", ("[画像] " if has_img else "") + summarize_result(txt)))
entries.sort(key=lambda e: e[0])

# ---------------------------------------------------------------- フレーム列（静止フレームの挿入）
seq = []  # (frame_index, wall_time)
for i, t in enumerate(ts):
    if i > 0:
        gap = t - ts[i - 1]
        if gap > 0.5:
            hold = min(gap, args.max_gap)
            n = int(hold * fps)
            for k in range(n):
                seq.append((i - 1, ts[i - 1] + (k + 1) * gap / n))
    seq.append((i, t))
if args.speed != 1.0:
    seq = seq[:: max(1, int(round(args.speed)))]

W, H = args.width, args.height
sim_w = int(H * 4 / 3)
panel_x = sim_w
panel_w = W - sim_w
font = _load_font(26)
font_small = _load_font(21)
font_tag = _load_font(18)
COL = {"user": (255, 236, 179), "claude": (230, 238, 255), "tool": (200, 230, 210), "result": (215, 215, 215)}
TAG = {"user": "ユーザー", "claude": "Claude", "tool": "ツール", "result": "結果"}


def wrap(text, f, width):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if f.getlength(cur + ch) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def render_panel(t_now):
    img = Image.new("RGB", (panel_w, H), (24, 26, 32))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, panel_w, 54], fill=(36, 40, 50))
    d.text((18, 14), "Claude Code  ×  MCP robot", font=font, fill=(240, 240, 240))
    visible = [e for e in entries if e[0] <= t_now]
    # 下から積む
    blocks = []
    for t, kind, text in visible[-40:]:
        f = font_small if kind in ("tool", "result") else font
        lines = wrap(text, f, panel_w - 40)
        if kind in ("tool", "result"):
            lines = lines[:3]
        else:
            lines = lines[:8]
        h = 26 + len(lines) * (f.size + 6) + 10
        blocks.append((kind, lines, f, h))
    y = H - 16
    for kind, lines, f, h in reversed(blocks):
        y -= h
        if y < 64:
            break
        bg = (32, 36, 46) if kind != "user" else (54, 48, 30)
        d.rounded_rectangle([12, y, panel_w - 12, y + h - 8], radius=8, fill=bg)
        d.text((22, y + 4), TAG[kind], font=font_tag, fill=COL[kind])
        yy = y + 26
        for ln in lines:
            d.text((22, yy), ln, font=f, fill=(235, 235, 235) if kind in ("user", "claude") else (190, 190, 190))
            yy += f.size + 6
    return np.asarray(img)


out = args.out or os.path.join(run, "composed.mp4")
writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=8, macro_block_size=None)
reader = iio.imiter(mp4)
cache = {}
cur = -1
frames_in = list(iio.imiter(mp4)) if len(ts) < 4000 else None
t0 = time.time()
last_panel_t = None
panel = None
for n, (fi, t) in enumerate(seq):
    if frames_in is not None:
        src = frames_in[min(fi, len(frames_in) - 1)]
    else:
        src = next(reader) if fi > cur else src  # noqa: F821
        cur = fi
    sim = np.asarray(Image.fromarray(src).resize((sim_w, H), Image.BILINEAR))
    if panel is None or last_panel_t is None or any(last_panel_t < e[0] <= t for e in entries):
        panel = render_panel(t)
        last_panel_t = t
    canvas = np.concatenate([sim, panel], axis=1)
    writer.append_data(canvas)
writer.close()
print(f"wrote {out}: {len(seq)} frames ({len(seq)/fps:.1f}s) in {time.time()-t0:.0f}s; {len(entries)} chat entries")
