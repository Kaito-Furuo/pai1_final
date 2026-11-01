"""合成動画（compose_video.py の出力）にタイトルカードを付け、2 倍速にして提出用 MP4 を作る。

    .venv/bin/python scripts/finalize_video.py --run recordings/final_demo --speed 2
"""
import argparse, os, subprocess, sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from robot_mcp.viewer import _load_font  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--run", required=True)
parser.add_argument("--src", default="", help="合成済み動画（既定: <run>/composed.mp4）")
parser.add_argument("--out", default="")
parser.add_argument("--speed", type=float, default=2.0)
parser.add_argument("--title", default="Claude Robot Operator")
parser.add_argument("--subtitle", default="Claude Code が MCP 経由で MuJoCo 上のロボットアームを自然言語で操作する")
parser.add_argument("--footer", default="", help="既定: 速度に応じて「等速」または「N 倍速再生」を記す")
parser.add_argument("--end", default="", help="終了カードの本文（改行可）。空なら付けない")
parser.add_argument("--card-seconds", type=float, default=4.0)
args = parser.parse_args()

run = args.run
src = args.src or os.path.join(run, "composed.mp4")
out = args.out or os.path.join(run, f"final_{args.speed:g}x.mp4")
W, H = 1920, 1080


def card(path, lines):
    img = Image.new("RGB", (W, H), (20, 22, 28))
    d = ImageDraw.Draw(img)
    y = H // 2 - sum(f.size + 28 for _, f in lines) // 2
    for text, f in lines:
        w = f.getlength(text)
        d.text(((W - w) / 2, y), text, font=f, fill=(240, 240, 240))
        y += f.size + 28
    img.save(path)


def run_ffmpeg(cmd):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


work = os.path.join(run, "_final_work")
os.makedirs(work, exist_ok=True)
f_big, f_mid, f_small = _load_font(72), _load_font(40), _load_font(32)
footer = args.footer or ("Physical AI 応用 1 講座 最終課題 ／ 映像は実時間（等速）" if args.speed == 1
                         else f"Physical AI 応用 1 講座 最終課題 ／ 以降の映像は {args.speed:g} 倍速再生")
card(os.path.join(work, "title.png"), [(args.title, f_big), (args.subtitle, f_mid), (footer, f_small)])
run_ffmpeg(["-loop", "1", "-framerate", "30", "-t", str(args.card_seconds), "-i", os.path.join(work, "title.png"),
            "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "20", os.path.join(work, "title.mp4")])
vf = f"setpts=PTS/{args.speed},format=yuv420p"
run_ffmpeg(["-i", src, "-vf", vf, "-r", "30", "-an", "-c:v", "libx264", "-crf", "20", os.path.join(work, "body.mp4")])
parts = [os.path.join(work, "title.mp4"), os.path.join(work, "body.mp4")]
if args.end:
    card(os.path.join(work, "end.png"), [(ln, f_mid) for ln in args.end.split("\\n")])
    run_ffmpeg(["-loop", "1", "-framerate", "30", "-t", str(args.card_seconds), "-i", os.path.join(work, "end.png"),
                "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "20", os.path.join(work, "end.mp4")])
    parts.append(os.path.join(work, "end.mp4"))
with open(os.path.join(work, "list.txt"), "w") as f:
    for p in parts:
        f.write(f"file '{os.path.abspath(p)}'\n")
run_ffmpeg(["-f", "concat", "-safe", "0", "-i", os.path.join(work, "list.txt"), "-c", "copy", out])
dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                     capture_output=True, text=True).stdout.strip()
print(f"wrote {out}  duration={float(dur):.1f}s")
