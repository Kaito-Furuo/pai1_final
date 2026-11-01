"""合成動画（compose_video.py の出力）にタイトルカードを付け、速度を変えて提出用 MP4 を作る。

    .venv/bin/python scripts/finalize_video.py --run recordings/final_demo --speed 2
    .venv/bin/python scripts/finalize_video.py --run recordings/final_demo --speed 1 \
        --end "終了カードの文" --outro recordings/ending.mp4 --out recordings/submission/final.mp4

--intro / --outro で講座配布のオープニング・エンディング映像などを前後に付けられる（解像度・fps は自動で揃える）。
--overview "見出し\\n箇条書き1\\n箇条書き2" でタイトル直後に「動画の見方」カードを入れられる。
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
parser.add_argument("--overview", default="", help="タイトル直後の「見方」カード。1 行目が見出し、以降が箇条書き（\\n 区切り）")
parser.add_argument("--overview-seconds", type=float, default=6.0)
parser.add_argument("--end", default="", help="終了カードの本文（改行可）。空なら付けない")
parser.add_argument("--card-seconds", type=float, default=4.0)
parser.add_argument("--intro", default="", help="先頭に付ける外部クリップ（講座配布のオープニング等）。形式は自動で揃える")
parser.add_argument("--outro", default="", help="末尾に付ける外部クリップ（講座配布のエンディング等）。形式は自動で揃える")
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


def card_bullets(path, heading, lines, f_head, f_body):
    """見出し + 左揃えの箇条書きカード。"""
    img = Image.new("RGB", (W, H), (20, 22, 28))
    d = ImageDraw.Draw(img)
    gap = 26
    total = f_head.size + 40 + sum(f_body.size + gap for _ in lines)
    widest = max([f_head.getlength(heading)] + [f_body.getlength("・" + ln) for ln in lines])
    x0 = max(80, (W - widest) // 2)
    y = (H - total) // 2
    d.text((x0, y), heading, font=f_head, fill=(255, 205, 92))
    y += f_head.size + 16
    d.rectangle([x0, y, x0 + 140, y + 4], fill=(255, 205, 92))
    y += 24
    for ln in lines:
        d.text((x0, y), "・" + ln, font=f_body, fill=(240, 240, 240))
        y += f_body.size + gap
    img.save(path)


def still_clip(png, seconds, out, fade_from=""):
    vf = "format=yuv420p" + (f",fade=t=in:st=0:d=0.5:color={fade_from}" if fade_from else "")
    run_ffmpeg(["-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", png,
                "-vf", vf, "-c:v", "libx264", "-crf", "20", out])


def run_ffmpeg(cmd):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


def normalize(src, dst):
    """外部クリップを本編と同じ形式（1920x1080・30 fps・yuv420p・音声なし）に揃える。"""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
          "fps=30,format=yuv420p")
    run_ffmpeg(["-i", src, "-vf", vf, "-an", "-c:v", "libx264", "-crf", "20", dst])


work = os.path.join(run, "_final_work")
os.makedirs(work, exist_ok=True)
f_big, f_mid, f_small = _load_font(72), _load_font(40), _load_font(32)
footer = args.footer or ("Physical AI 応用 1 講座 最終課題 ／ 映像は実時間（等速）" if args.speed == 1
                         else f"Physical AI 応用 1 講座 最終課題 ／ 以降の映像は {args.speed:g} 倍速再生")
card(os.path.join(work, "title.png"), [(args.title, f_big), (args.subtitle, f_mid), (footer, f_small)])
# 配布ジングルは白にフェードして終わるものが多いので、intro があるときはタイトルを白からフェードインさせる
still_clip(os.path.join(work, "title.png"), args.card_seconds, os.path.join(work, "title.mp4"),
           fade_from="white" if args.intro else "")
vf = f"setpts=PTS/{args.speed},format=yuv420p"
run_ffmpeg(["-i", src, "-vf", vf, "-r", "30", "-an", "-c:v", "libx264", "-crf", "20", os.path.join(work, "body.mp4")])
parts = [os.path.join(work, "title.mp4")]
if args.overview:
    head, *lines = [ln for ln in args.overview.split("\\n") if ln.strip()]
    card_bullets(os.path.join(work, "overview.png"), head, lines, _load_font(56), _load_font(36))
    still_clip(os.path.join(work, "overview.png"), args.overview_seconds, os.path.join(work, "overview.mp4"))
    parts.append(os.path.join(work, "overview.mp4"))
parts.append(os.path.join(work, "body.mp4"))
if args.intro:
    normalize(args.intro, os.path.join(work, "intro.mp4"))
    parts.insert(0, os.path.join(work, "intro.mp4"))
if args.end:
    card(os.path.join(work, "end.png"), [(ln, f_mid) for ln in args.end.split("\\n")])
    still_clip(os.path.join(work, "end.png"), args.card_seconds, os.path.join(work, "end.mp4"))
    parts.append(os.path.join(work, "end.mp4"))
if args.outro:
    normalize(args.outro, os.path.join(work, "outro.mp4"))
    parts.append(os.path.join(work, "outro.mp4"))
with open(os.path.join(work, "list.txt"), "w") as f:
    for p in parts:
        f.write(f"file '{os.path.abspath(p)}'\n")
run_ffmpeg(["-f", "concat", "-safe", "0", "-i", os.path.join(work, "list.txt"), "-c", "copy", out])
dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                     capture_output=True, text=True).stdout.strip()
print(f"wrote {out}  duration={float(dur):.1f}s")
