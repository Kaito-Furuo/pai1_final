"""ライブビューア（MJPEG over HTTP）と MP4 録画。

MCP サーバーの裏で HTTP サーバーを 1 本立て、ブラウザで http://localhost:8765/ を
開くとシミュレーションの様子（前方カメラ＋手首カメラ＋字幕＋ツールログ）が見える。
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _load_font(size: int):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


class FrameComposer:
    """前方カメラ画像に手首カメラの小窓と字幕を合成する。"""

    def __init__(self, world):
        self.world = world
        self.caption = ""
        self.sub = ""
        self._font = _load_font(22)
        self._font_small = _load_font(15)

    def compose(self, front: np.ndarray) -> np.ndarray:
        img = np.ascontiguousarray(front.copy())
        h, w = img.shape[:2]
        # 手首カメラの小窓（右下）
        try:
            wrist = self.world.render_small("wrist")
            iw, ih = w // 3, h // 3
            wrist = cv2.resize(wrist, (iw, ih), interpolation=cv2.INTER_AREA)
            x0, y0 = w - iw - 8, h - ih - 8
            img[y0 - 2:y0 + ih + 2, x0 - 2:x0 + iw + 2] = (255, 255, 255)
            img[y0:y0 + ih, x0:x0 + iw] = wrist
        except Exception:
            pass
        if self.caption or self.sub:
            pil = Image.fromarray(img)
            draw = ImageDraw.Draw(pil, "RGBA")
            pad = 10
            lines = []
            if self.caption:
                lines.append((self.caption, self._font))
            if self.sub:
                lines.append((self.sub, self._font_small))
            total_h = sum(f.size + 6 for _, f in lines) + pad
            draw.rectangle([0, 0, w, total_h], fill=(0, 0, 0, 150))
            y = pad // 2
            for text, f in lines:
                draw.text((pad, y), text, font=f, fill=(255, 255, 255, 255))
                y += f.size + 6
            img = np.asarray(pil)
        return img


class Recorder:
    def __init__(self):
        self.writer = None
        self.path = None
        self.frames = 0

    def start(self, path: str, fps: float):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.writer = imageio.get_writer(path, fps=fps, codec="libx264", quality=8, macro_block_size=None)
        self.path, self.frames, self.fps = path, 0, fps
        self.timestamps: list[float] = []  # 各フレームの壁時計（動画合成でチャットと同期させる）

    def append(self, frame: np.ndarray):
        if self.writer is not None:
            self.writer.append_data(frame)
            self.timestamps.append(time.time())
            self.frames += 1

    def stop(self) -> dict:
        info = dict(path=self.path, frames=self.frames)
        if self.writer is not None:
            self.writer.close()
            with open(os.path.splitext(self.path)[0] + ".frames.json", "w", encoding="utf-8") as f:
                json.dump(dict(fps=self.fps, timestamps=self.timestamps), f)
        self.writer = None
        return info

    @property
    def active(self) -> bool:
        return self.writer is not None


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>Robot Operator Live</title>
<style>
 :root{--bg:#111318;--panel:#1b1f27;--fg:#e8eaf0;--mut:#9aa3b2;--acc:#5ec2ff}
 body{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans",sans-serif}
 header{padding:10px 16px;background:var(--panel);display:flex;align-items:center;gap:12px}
 header h1{font-size:15px;margin:0;font-weight:600}
 header .dot{width:10px;height:10px;border-radius:50%;background:#555}
 header .dot.rec{background:#ff4d4d;box-shadow:0 0 8px #ff4d4d}
 main{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:12px;padding:12px}
 img#view{width:100%;border-radius:8px;background:#000;display:block}
 .side{display:flex;flex-direction:column;gap:10px;min-width:0}
 .card{background:var(--panel);border-radius:8px;padding:10px 12px}
 .card h2{font-size:12px;color:var(--mut);margin:0 0 6px;text-transform:uppercase;letter-spacing:.06em}
 #caption{font-size:18px;line-height:1.4;min-height:1.4em}
 #status{font-size:13px;color:var(--mut);white-space:pre-wrap}
 #log{font-size:12px;list-style:none;margin:0;padding:0;max-height:46vh;overflow:auto}
 #log li{padding:4px 0;border-bottom:1px solid #2a2f3a;color:var(--mut)} #log li b{color:var(--acc)}
 @media (max-width:800px){main{grid-template-columns:1fr}}
</style></head><body>
<header><span class="dot" id="dot"></span><h1>Claude Robot Operator — MuJoCo / Franka Panda</h1></header>
<main>
 <div><img id="view" src="/stream"></div>
 <div class="side">
  <div class="card"><h2>Claude</h2><div id="caption">…</div></div>
  <div class="card"><h2>Status</h2><div id="status"></div></div>
  <div class="card"><h2>Tool log</h2><ul id="log"></ul></div>
 </div>
</main>
<script>
async function tick(){try{const r=await fetch('/state');const s=await r.json();
 document.getElementById('caption').textContent=s.caption||'…';
 document.getElementById('status').textContent=s.status;
 document.getElementById('dot').className='dot'+(s.recording?' rec':'');
 const ul=document.getElementById('log');ul.innerHTML=s.log.slice().reverse().map(e=>`<li><b>${e.tool}</b> ${e.text}</li>`).join('');
}catch(e){}}
setInterval(tick,500);tick();
</script></body></html>"""


class LiveViewer:
    def __init__(self, world, port: int = 8765, jpeg_quality: int = 82):
        self.world = world
        self.port = port
        self.composer = FrameComposer(world)
        self.recorder = Recorder()
        self.jpeg_quality = jpeg_quality
        self._jpeg: bytes | None = None
        self._seq = 0
        self._cond = threading.Condition()
        self.log: list[dict] = []
        self.status_fn = lambda: ""
        self.fps = 1.0 / (world.render_every * world.model.opt.timestep)
        world.frame_callbacks.append(self._on_frame)
        self._server = None
        self._thread = None

    # ------------------------------------------------------------------ フレーム
    def _on_frame(self, world, front_rgb):
        frame = self.composer.compose(front_rgb)
        self.recorder.append(frame)
        self._publish(frame)

    def _publish(self, frame: np.ndarray):
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if ok:
            with self._cond:
                self._jpeg = buf.tobytes()
                self._seq += 1
                self._cond.notify_all()

    def refresh(self):
        """ステップしていないときに手動で 1 フレーム描画する（字幕更新など）。"""
        frame = self.composer.compose(self.world.render_display("front"))
        self.recorder.append(frame)
        self._publish(frame)

    def set_caption(self, text: str, sub: str = ""):
        self.composer.caption = text
        self.composer.sub = sub
        self.refresh()

    def add_log(self, tool: str, text: str):
        self.log.append(dict(t=round(time.time(), 2), tool=tool, text=text))
        self.log = self.log[-60:]

    # ------------------------------------------------------------------ HTTP
    def start(self):
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 標準エラーへの雑音を抑える
                pass

            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    last = -1
                    try:
                        while True:
                            with viewer._cond:
                                viewer._cond.wait_for(lambda: viewer._seq != last, timeout=1.0)
                                jpeg, last = viewer._jpeg, viewer._seq
                            if jpeg is None:
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg))
                            self.wfile.write(jpeg)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                elif self.path.startswith("/frame.jpg"):
                    jpeg = viewer._jpeg or b""
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg)))
                    self.end_headers()
                    self.wfile.write(jpeg)
                elif self.path.startswith("/state"):
                    body = json.dumps(dict(caption=viewer.composer.caption, status=viewer.status_fn(),
                                           recording=viewer.recorder.active, log=viewer.log[-14:]),
                                      ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    body = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        last_err = None
        for port in range(self.port, self.port + 10):  # 使用中なら次のポートを試す
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                self.port = port
                break
            except OSError as e:
                last_err = e
        else:
            raise RuntimeError(f"ビューアのポートを確保できません: {last_err}")
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.refresh()
        return f"http://localhost:{self.port}/"

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
