"""Claude Code から MuJoCo 上の Franka Panda を操作するための MCP サーバー（stdio）。

ツール群:
  reset_scene / look / detect_objects / pick / place / verify / move_to /
  open_gripper / close_gripper / home / get_state / nudge_object / say /
  start_recording / stop_recording

標準出力は MCP の通信路なので、ログは必ず標準エラーへ出す。
"""
from __future__ import annotations

import functools
import json
import math
import os
import subprocess
import sys
import time
import traceback

import cv2
import numpy as np
from mcp.server.fastmcp import FastMCP, Image

from .perception import Perception
from .sim import COLOR_JA, TABLE_TOP_Z, WORKSPACE_X, WORKSPACE_Y, World
from .skills import Arm
from .viewer import LiveViewer

INSTRUCTIONS = """あなたはシミュレータ上の Franka Panda アームを操作するロボットオペレータです。
基本の流れ: say() で方針を一言 → look("top") / detect_objects() で現状把握 → pick / place →
verify() と look() で結果を確認 → 報告。座標系: ロボット基部が原点、+x が正面、+y が左、+z が上、
テーブル面 z=0、作業領域 x 0.30〜0.66 m、y ±0.30 m。物体 ID は detect_objects() が返すものを使う。"""

mcp = FastMCP("robot-operator", instructions=INSTRUCTIONS)


def _eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def _jpeg(rgb: np.ndarray, quality: int = 85) -> Image:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    return Image(data=buf.tobytes(), format="jpeg")


def _js(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Robot:
    def __init__(self):
        realtime = float(os.environ.get("ROBOT_REALTIME", "1.0"))
        w, h = (int(v) for v in os.environ.get("ROBOT_RENDER", "640x480").split("x"))
        layout = os.environ.get("ROBOT_LAYOUT", "basic")
        self.world = World(layout=layout, realtime=realtime, render_size=(w, h))
        self.arm = Arm(self.world)
        self.perception = Perception(self.world)
        self.viewer = LiveViewer(self.world, port=int(os.environ.get("ROBOT_VIEWER_PORT", "8765")))
        self.viewer.status_fn = self.status_text
        self.rec_dir = os.environ.get("ROBOT_RECORD_DIR", "recordings")
        os.makedirs(self.rec_dir, exist_ok=True)
        self.tts = os.environ.get("ROBOT_TTS", "0") == "1"
        self.held: dict | None = None
        self.last_dets = []
        self._log_f = open(os.path.join(self.rec_dir, "tool_log.jsonl"), "a", encoding="utf-8")
        self.url = self.viewer.start()
        self.world.settle(0.3)
        self.detect()
        _eprint(f"[robot] ライブビューア: {self.url}  layout={layout} realtime={realtime}")
        auto = os.environ.get("ROBOT_AUTORECORD", "")
        if auto:  # 起動と同時に録画を始める（CLI ドライバからのデモ用）
            path = os.path.join(self.rec_dir, f"{time.strftime('%Y%m%d_%H%M%S')}_{auto}.mp4")
            self.viewer.recorder.start(path, fps=self.viewer.fps)
            self.viewer.refresh()
            self.log("start_recording", path)

    # ------------------------------------------------------------------ 補助
    def status_text(self) -> str:
        tcp, _ = self.world.tcp_pose()
        held = self.held["id"] if self.held else "なし"
        rec = f"録画中 ({self.viewer.recorder.frames} frames)" if self.viewer.recorder.active else "録画なし"
        return (f"layout: {self.world.layout_name}   sim t = {self.world.data.time:6.1f} s\n"
                f"TCP: ({tcp[0]:.3f}, {tcp[1]:.3f}, {tcp[2]:.3f})   gripper: {self.world.gripper_gap()*1000:.0f} mm\n"
                f"holding: {held}\n{rec}")

    def log(self, tool: str, text: str, **extra):
        self.viewer.add_log(tool, text)
        rec = dict(t=time.time(), sim_t=round(self.world.data.time, 3), tool=tool, text=text, **extra)
        self._log_f.write(_js(rec) + "\n")
        self._log_f.flush()
        _eprint(f"[robot] {tool}: {text}")

    def detect(self):
        dets, img = self.perception.detect()
        self.last_dets = dets
        return dets, img

    def find(self, object_id: str):
        dets, _ = self.detect()
        for d in dets:
            if d.id == object_id:
                return d, dets
        # 色名だけ指定された場合、その色の物体が 1 つなら許容
        matches = [d for d in dets if d.id.startswith(object_id)]
        if len(matches) == 1:
            return matches[0], dets
        lk = self.perception.last_known(object_id)
        if lk is not None:
            return lk, dets
        return None, dets

    def ids(self, dets=None) -> list[str]:
        return [d.id for d in (dets if dets is not None else self.last_dets)]

    def caption(self, text: str, sub: str = ""):
        self.viewer.set_caption(text, sub)


ROBOT: Robot | None = None


def robot() -> Robot:
    global ROBOT
    if ROBOT is None:
        ROBOT = Robot()
    return ROBOT


def _guard(fn):
    """例外をテキストで返す（MCP 側でエラー扱いにしつつ、内容を読めるように）。"""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=3)
            _eprint(tb)
            return _js(dict(error=f"{type(e).__name__}: {e}"))
    return wrapper


# ====================================================================== ツール
@mcp.tool()
@_guard
def reset_scene(layout: str = "basic", seed: int | None = None) -> str:
    """シーンを初期化してアームをホームに戻す。

    layout: "basic"（赤・緑・青ブロック + 青・黄トレイ）/ "sorting"（赤2・青2・緑1 + 赤・青トレイ）/
    "stacking"（大中小のブロック + 黄トレイ）/ "random"（seed でランダム配置）。
    戻り値: 検出した物体一覧（JSON）。
    """
    r = robot()
    r.world.reset(layout, seed)
    r.arm.rebind()
    r.perception.reset_ids()
    r.held = None
    r.world.settle(0.3)
    dets, _ = r.detect()
    r.caption("")
    r.log("reset_scene", f"layout={layout} seed={seed} objects={r.ids(dets)}")
    return _js(dict(layout=layout, objects=[d.to_dict() for d in dets], viewer=r.url))


@mcp.tool()
@_guard
def look(camera: str = "front") -> list:
    """カメラ画像を取得する。

    camera: "front"（斜め前方、全体の確認用）/ "top"（真上。物体検出の結果を ID 付きで注記した画像と一覧）/
    "wrist"（手首カメラ。把持できているかの確認用）/ "side"（横から）。
    """
    r = robot()
    if camera == "top":
        dets, img = r.detect()
        r.log("look", f"camera=top ({len(dets)} objects)")
        return [_jpeg(img), _js(dict(camera="top", objects=[d.to_dict() for d in dets]))]
    if camera not in ("front", "wrist", "side"):
        return [_js(dict(error=f"未知のカメラ: {camera}（front / top / wrist / side）"))]
    img = r.world.render_display(camera)
    r.log("look", f"camera={camera}")
    return [_jpeg(img), _js(dict(camera=camera, holding=r.held["id"] if r.held else None))]


@mcp.tool()
@_guard
def detect_objects() -> str:
    """トップカメラの画像処理（色分割 + 深度の逆投影）で物体を検出し、ID・色・種類・位置・サイズ・向きを返す。"""
    r = robot()
    dets, _ = r.detect()
    r.log("detect_objects", f"{len(dets)} objects: {r.ids(dets)}")
    return _js(dict(objects=[d.to_dict() for d in dets],
                    note="position は cube なら中心、tray なら底面中心。top_z は上に物を置くときの支持面の高さ。"))


@mcp.tool()
@_guard
def pick(object_id: str) -> list:
    """指定 ID の物体を把持して持ち上げる。成否と手首カメラ画像を返す。

    object_id は detect_objects() / look("top") が返す ID（例: "red_cube", "blue_cube_2"）。
    """
    r = robot()
    if r.held is not None:
        return [_js(dict(success=False, error=f"すでに {r.held['id']} を持っています。先に place してください。"))]
    d, dets = r.find(object_id)
    if d is None:
        return [_js(dict(success=False, error=f"'{object_id}' が見つかりません。検出中の ID: {r.ids(dets)}"))]
    if d.kind != "cube":
        return [_js(dict(success=False, error=f"{d.id} は {d.kind} なので持ち上げられません。"))]
    if not d.visible:
        return [_js(dict(success=False, error=f"{d.id} は上カメラから見えません（別の物体の下に隠れている可能性）。"
                                              "上に載っている物体を先にどかしてください。"))]
    r.caption(f"{COLOR_JA.get(d.color, d.color)}のブロック（{d.id}）を把持します")
    res = r.arm.pick(d.position, d.yaw, d.size, name=d.id)
    if res["success"]:
        r.held = dict(id=d.id, size=d.size, color=d.color)
    r.log("pick", f"{d.id} -> {'成功' if res['success'] else '失敗'} (gap {res['gap_mm']} mm)", success=res["success"])
    img = r.world.render_display("wrist")
    return [_js(dict(object=d.id, **res)), _jpeg(img)]


def _free_spot_in_tray(tray, held_size: float, dets):
    """トレイ内で既存のブロックと干渉しない置き位置（トレイ中心からのオフセット）を返す。"""
    half = tray.size / 2
    inside = [d for d in dets if d.kind == "cube"
              and abs(d.position[0] - tray.position[0]) < half and abs(d.position[1] - tray.position[1]) < half]
    o = half - held_size / 2 - 0.008
    cands = [(0, 0), (-o, -o), (o, o), (o, -o), (-o, o), (o, 0), (-o, 0), (0, o), (0, -o)]
    for cx, cy in cands:
        ok = all(math.hypot(cx - (d.position[0] - tray.position[0]), cy - (d.position[1] - tray.position[1]))
                 >= (held_size + d.size) / 2 + 0.006 for d in inside)
        if ok:
            return cx, cy, len(inside)
    return None


def _parse_target(r: Robot, target: str, dx: float, dy: float, held_size: float):
    """置き先を (x, y, support_z, description) に解決する。"""
    t = target.strip()
    if "," in t and not any(c.isalpha() for c in t.replace("table", "")):
        xs, ys = t.replace("table:", "").split(",")[:2]
        x, y = float(xs), float(ys)
        return x + dx, y + dy, TABLE_TOP_Z, f"テーブル上 ({x:.2f}, {y:.2f})"
    d, dets = r.find(t)
    if d is None:
        raise ValueError(f"'{target}' が見つかりません。検出中の ID: {r.ids(dets)}（座標指定は \"x,y\"）")
    if d.kind == "tray":
        if dx == 0 and dy == 0:
            spot = _free_spot_in_tray(d, held_size, dets)
            if spot is None:
                raise ValueError(f"{d.id} に空きがありません（dx, dy で位置を指定するか別の置き場所を選んでください）")
            dx, dy, n_inside = spot
            desc = f"{d.id} の中" + (f"（既に {n_inside} 個あるので空き位置 dx={dx:+.3f}, dy={dy:+.3f}）" if n_inside else "")
            return d.position[0] + dx, d.position[1] + dy, d.top_z, desc
        # 手動オフセット: トレイ内の既存ブロックとの干渉を確認する
        half = d.size / 2
        if abs(dx) > half - held_size / 2 or abs(dy) > half - held_size / 2:
            raise ValueError(f"オフセット ({dx:+.3f}, {dy:+.3f}) は {d.id} の縁に当たります（内寸 {d.size:.3f} m）。"
                             "dx, dy を省略すれば空き位置を自動で選びます。")
        for o in dets:
            if o.kind == "cube" and abs(o.position[0] - d.position[0]) < half and abs(o.position[1] - d.position[1]) < half:
                if math.hypot(dx - (o.position[0] - d.position[0]), dy - (o.position[1] - d.position[1])) < (held_size + o.size) / 2 + 0.004:
                    spot = _free_spot_in_tray(d, held_size, dets)
                    hint = f" 空き位置の候補: dx={spot[0]:+.3f}, dy={spot[1]:+.3f}" if spot else ""
                    raise ValueError(f"オフセット ({dx:+.3f}, {dy:+.3f}) は {o.id} と干渉します。"
                                     f"dx, dy を省略すれば空き位置を自動で選びます。{hint}")
        return d.position[0] + dx, d.position[1] + dy, d.top_z, f"{d.id} の中（dx={dx:+.3f}, dy={dy:+.3f}）"
    note = "" if d.visible else "（上カメラから見えないため最後に検出した位置）"
    return d.position[0] + dx, d.position[1] + dy, d.top_z, f"{d.id} の{'中' if d.kind == 'tray' else '上'}{note}"


@mcp.tool()
@_guard
def place(target: str, dx: float = 0.0, dy: float = 0.0) -> list:
    """持っている物体を置く。

    target: 物体 ID（トレイならその中央に入れる、ブロックならその上に積む）、または "x,y"（テーブル上の座標 [m]）。
    dx, dy: 目標からのオフセット [m]（例: トレイの端に寄せる）。置いた後に自動で検証し、前方カメラ画像を返す。
    """
    r = robot()
    if r.held is None:
        return [_js(dict(success=False, error="何も持っていません。先に pick してください。"))]
    x, y, zs, desc = _parse_target(r, target, dx, dy, r.held["size"])
    if not (WORKSPACE_X[0] - 0.05 <= x <= WORKSPACE_X[1] + 0.05 and WORKSPACE_Y[0] - 0.05 <= y <= WORKSPACE_Y[1] + 0.05):
        return [_js(dict(success=False, error=f"目標 ({x:.2f}, {y:.2f}) は作業領域外です。x {WORKSPACE_X}, y {WORKSPACE_Y}"))]
    held = r.held
    short = desc.split("（")[0]
    r.caption(f"{COLOR_JA.get(held['color'], held['color'])}のブロックを{short}に置きます")
    res = r.arm.place([x, y, zs], held["size"])
    r.held = None
    # ホームへ退避してから（トップカメラを遮らない位置で）置いた結果を検証
    r.arm.home()
    r.world.settle(0.2)
    r.perception.update_track(held["id"], (x, y), zs + held["size"] / 2)
    d, _ = r.find(held["id"])
    check = None
    if d is not None:
        err = math.hypot(d.position[0] - x, d.position[1] - y)
        check = dict(object=d.id, position=[round(v, 4) for v in d.position], xy_error_mm=round(err * 1000, 1),
                     on_target=bool(err < 0.03 and abs(d.top_z - (zs + held["size"])) < 0.012))
    r.log("place", f"{held['id']} -> {desc}: {'成功' if res['success'] else '失敗'}"
          + (f", 誤差 {check['xy_error_mm']} mm" if check else ""), success=res["success"])
    img = r.world.render_display("front")
    return [_js(dict(target=desc, **res, check=check)), _jpeg(img)]


@mcp.tool()
@_guard
def verify(object_id: str, target: str) -> str:
    """物体が目標（トレイの中 / 別のブロックの上 / "x,y" 座標）にあるかを知覚結果から検証する。"""
    r = robot()
    d, dets = r.find(object_id)
    if d is None:
        return _js(dict(ok=False, error=f"'{object_id}' が見つかりません。検出中の ID: {r.ids(dets)}"))
    t = target.strip()
    if "," in t and not any(c.isalpha() for c in t.replace("table", "")):
        xs, ys = t.replace("table:", "").split(",")[:2]
        tx, ty, half = float(xs), float(ys), 0.03
        tz = TABLE_TOP_Z + d.size
        desc = f"({tx:.2f},{ty:.2f})"
        td = None
    else:
        td = next((o for o in dets if o.id == t), None) or r.perception.last_known(t)
        if td is None:
            return _js(dict(ok=False, error=f"'{target}' が見つかりません。検出中の ID: {r.ids(dets)}"))
        tx, ty = td.position[0], td.position[1]
        half = (td.size / 2 - d.size / 2 + 0.01) if td.kind == "tray" else (td.size / 2 + 0.008)
        tz = td.top_z + d.size
        desc = td.id
    ex, ey, ez = d.position[0] - tx, d.position[1] - ty, d.top_z - tz
    ok = abs(ex) <= half and abs(ey) <= half and abs(ez) <= 0.012
    notes = []
    if not d.visible:
        notes.append(f"{d.id} は現在上カメラから見えないため最後に検出した位置で判定")
    if hasattr(td, "visible") and not td.visible:
        notes.append(f"{td.id} は上に物が載って隠れているため最後に検出した位置で判定")
    r.log("verify", f"{d.id} @ {desc}: {'OK' if ok else 'NG'} (dx {ex*1000:.0f}, dy {ey*1000:.0f}, dz {ez*1000:.0f} mm)")
    return _js(dict(ok=bool(ok), object=d.id, target=desc, dx_mm=round(ex * 1000, 1), dy_mm=round(ey * 1000, 1),
                    dz_mm=round(ez * 1000, 1), tolerance_xy_mm=round(half * 1000, 1), notes=notes))


@mcp.tool()
@_guard
def move_to(x: float, y: float, z: float, yaw_deg: float | None = None) -> str:
    """手先（TCP）を直線的に (x, y, z) [m] へ動かす。グリッパは常に真下向き。yaw_deg は指の開閉方向 [deg]。"""
    r = robot()
    yaw = None if yaw_deg is None else math.radians(yaw_deg)
    res = r.arm.move_to([x, y, z], yaw, speed=0.3)
    r.log("move_to", f"({x:.3f}, {y:.3f}, {z:.3f}) yaw={yaw_deg}: {res.message}")
    tcp, _ = r.world.tcp_pose()
    return _js(dict(ok=res.ok, message=res.message, tcp=[round(float(v), 4) for v in tcp]))


@mcp.tool()
@_guard
def open_gripper() -> str:
    """グリッパを開く。持っていた物体は解放される。"""
    r = robot()
    r.arm.set_gripper(1.0)
    released = r.held["id"] if r.held else None
    r.held = None
    r.arm.holding = None
    r.log("open_gripper", f"gap {r.world.gripper_gap()*1000:.0f} mm" + (f" (released {released})" if released else ""))
    return _js(dict(gap_mm=round(r.world.gripper_gap() * 1000, 1), released=released))


@mcp.tool()
@_guard
def close_gripper() -> str:
    """グリッパを閉じる。指間隔から何かを掴んだかを推定して返す。"""
    r = robot()
    r.arm.set_gripper(0.0)
    gap = r.world.gripper_gap()
    r.log("close_gripper", f"gap {gap*1000:.0f} mm")
    return _js(dict(gap_mm=round(gap * 1000, 1), something_grasped=bool(gap > 0.005)))


@mcp.tool()
@_guard
def home() -> str:
    """アームをホーム姿勢（カメラの視野外）に戻す。"""
    r = robot()
    res = r.arm.home()
    r.log("home", res.message)
    return _js(dict(ok=res.ok, message=res.message))


@mcp.tool()
@_guard
def get_state() -> str:
    """ロボットの状態（TCP 位置、グリッパ開度、保持中の物体、最新の物体一覧、ビューア URL）を返す。"""
    r = robot()
    tcp, _ = r.world.tcp_pose()
    return _js(dict(tcp=[round(float(v), 4) for v in tcp], gripper_gap_mm=round(r.world.gripper_gap() * 1000, 1),
                    holding=r.held, layout=r.world.layout_name, sim_time=round(r.world.data.time, 2),
                    objects=[d.to_dict() for d in r.last_dets], viewer=r.url, recording=r.viewer.recorder.active))


@mcp.tool()
@_guard
def nudge_object(object_id: str, dx: float = 0.0, dy: float = 0.0, yaw_deg: float | None = None) -> str:
    """【外乱・実験用】物体を (dx, dy) [m] だけ瞬間移動させる（人が動かした状況を再現）。"""
    r = robot()
    d, dets = r.find(object_id)
    if d is None:
        return _js(dict(ok=False, error=f"'{object_id}' が見つかりません。検出中の ID: {r.ids(dets)}"))
    yaw = None if yaw_deg is None else math.radians(yaw_deg)
    # 知覚 ID とシミュレータ内部名の対応は最近傍で解決する
    truth = min(r.world.all_object_info(), key=lambda o: math.hypot(o["position"][0] - d.position[0], o["position"][1] - d.position[1])
                if o["kind"] == d.kind and o["color"] == d.color else 9)
    r.world.set_object_pose(truth["name"], (d.position[0] + dx, d.position[1] + dy), yaw)
    r.perception.update_track(d.id, (d.position[0] + dx, d.position[1] + dy))
    r.world.settle(0.3)
    r.viewer.refresh()
    r.log("nudge_object", f"{d.id} moved by ({dx:+.3f}, {dy:+.3f})")
    return _js(dict(ok=True, object=d.id, new_position=[round(d.position[0] + dx, 3), round(d.position[1] + dy, 3)]))


@mcp.tool()
@_guard
def say(text: str) -> str:
    """ひとこと発話する。ライブビューアと録画の字幕に表示される（方針・判断・結果を短く伝えるのに使う）。"""
    r = robot()
    r.caption(text)
    r.log("say", text)
    if r.tts:
        try:
            subprocess.Popen(["say", "-v", "Kyoko", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    return _js(dict(ok=True))


@mcp.tool()
@_guard
def start_recording(name: str = "demo") -> str:
    """前方カメラ（手首カメラの小窓と字幕つき）の MP4 録画を開始する。"""
    r = robot()
    if r.viewer.recorder.active:
        return _js(dict(ok=False, error="すでに録画中です。"))
    path = os.path.join(r.rec_dir, f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.mp4")
    r.viewer.recorder.start(path, fps=r.viewer.fps)
    r.viewer.refresh()
    r.log("start_recording", path)
    return _js(dict(ok=True, path=os.path.abspath(path), fps=round(r.viewer.fps, 2)))


@mcp.tool()
@_guard
def stop_recording() -> str:
    """録画を停止してファイルを閉じる。"""
    r = robot()
    if not r.viewer.recorder.active:
        return _js(dict(ok=False, error="録画していません。"))
    info = r.viewer.recorder.stop()
    r.log("stop_recording", f"{info['path']} ({info['frames']} frames)")
    return _js(dict(ok=True, **info))


def _shutdown(*_args):
    """終了時に録画を閉じる（stdin 切断・SIGTERM・通常終了のいずれでも）。"""
    global ROBOT
    r = ROBOT
    if r is not None and r.viewer.recorder.active:
        info = r.viewer.recorder.stop()
        _eprint(f"[robot] 録画を終了: {info['path']} ({info['frames']} frames)")


def main():
    import atexit
    import signal

    atexit.register(_shutdown)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_a: (_shutdown(), sys.exit(0)))
    robot()  # 起動時に初期化しておく（ビューアを先に開けるように）
    try:
        mcp.run(transport="stdio")
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
