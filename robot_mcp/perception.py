"""古典的な画像処理による物体検出（トップカメラの RGB + 深度）。

HSV 色分割 × 高さマップ（深度の逆投影）で「ブロック」と「トレイ」を分け、
連結成分ごとに世界座標・サイズ・向きを求める。シミュレータの真値は使わない。
呼び出し間では最近傍マッチで ID を引き継ぐ（追跡）。
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import cv2
import numpy as np

from .sim import COLOR_JA, TABLE_TOP_Z, TRAY_BASE_H, World

# OpenCV の H は 0-179
HUE_RANGES = {
    "red": [(0, 8), (170, 179)],
    "orange": [(9, 19)],
    "yellow": [(20, 40)],
    "green": [(40, 88)],
    "blue": [(95, 130)],
    "purple": [(131, 165)],
}
CUBE_MIN_HEIGHT = 0.025  # これより高い色付き画素はブロック（トレイの縁は 0.018）
MIN_AREA = {"cube": 50, "tray": 400}
CUBE_MIN_SIZE = 0.027  # これより小さい「ブロック」は遮蔽による残片とみなして捨てる
TRACK_RADIUS = 0.06


@dataclass
class Detection:
    id: str
    kind: str  # cube | tray
    color: str
    position: list[float]  # 物体中心（cube）/ 底面中心（tray）の世界座標
    top_z: float  # 上面（支持面）の高さ
    size: float  # cube: 一辺 / tray: 内寸
    yaw: float
    pixel: tuple[int, int]
    bbox: tuple[int, int, int, int]
    area_px: int
    visible: bool = True  # False なら「最後に検出した位置」（現在は上カメラから見えない）

    def to_dict(self) -> dict:
        d = dict(id=self.id, kind=self.kind, color=self.color, color_ja=COLOR_JA.get(self.color, self.color),
                 position=[round(v, 4) for v in self.position], top_z=round(self.top_z, 4),
                 size=round(self.size, 4), yaw=round(self.yaw, 3), pixel=list(self.pixel), bbox=list(self.bbox))
        if not self.visible:
            d["visible"] = False
            d["note"] = "上カメラから見えないため最後に検出した位置（別の物体の下に隠れている可能性）"
        return d


class Perception:
    """トップカメラ画像から物体を検出し、呼び出し間で ID を安定化させる。"""

    def __init__(self, world: World, camera: str = "top"):
        self.world = world
        self.camera = camera
        self._tracks: dict[str, dict] = {}  # id -> dict(color, kind, position)

    # ------------------------------------------------------------------ 幾何
    def _height_map(self, depth: np.ndarray):
        """各画素の世界座標 (H, W, 3) を返す。"""
        f, cx, cy = self.world.camera_intrinsics(self.camera)
        cam_pos, cam_rot = self.world.camera_pose(self.camera)
        h, w = depth.shape
        us, vs = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        X = (us - cx) * depth / f
        Y = -(vs - cy) * depth / f
        Z = -depth
        pts = np.stack([X, Y, Z], axis=-1) @ cam_rot.T + cam_pos
        return pts

    def _pixel_size_to_m(self, px: float, depth: float) -> float:
        f, _, _ = self.world.camera_intrinsics(self.camera)
        return px * depth / f

    # ------------------------------------------------------------------ 検出
    def detect(self, annotate: bool = True):
        """(detections, annotated_rgb) を返す。"""
        w = self.world
        rgb = w.render(self.camera, shadows=False)
        depth = w.render(self.camera, depth=True, shadows=False)
        pts = self._height_map(depth)
        height = pts[..., 2] - TABLE_TOP_Z
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        hch, sch, vch = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        colorful = (sch > 100) & (vch > 40)
        kind_masks = {"cube": colorful & (height > CUBE_MIN_HEIGHT), "tray": colorful & (height <= CUBE_MIN_HEIGHT)}
        kernel = np.ones((3, 3), np.uint8)
        raw: list[Detection] = []
        for color, ranges in HUE_RANGES.items():
            hue = np.zeros(hch.shape, bool)
            for lo, hi in ranges:
                hue |= (hch >= lo) & (hch <= hi)
            for kind, kmask in kind_masks.items():
                m8 = cv2.morphologyEx((hue & kmask).astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)
                n, labels, stats, cents = cv2.connectedComponentsWithStats(m8, connectivity=8)
                for i in range(1, n):
                    area = int(stats[i, cv2.CC_STAT_AREA])
                    if area < MIN_AREA[kind]:
                        continue
                    comp = labels == i
                    x0, y0, bw, bh = (int(stats[i, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
                    inner = cv2.erode(comp.astype(np.uint8), kernel, iterations=1).astype(bool)
                    sel = inner if inner.sum() > 10 else comp
                    cu, cv_ = float(cents[i][0]), float(cents[i][1])
                    cnt, _ = cv2.findContours(comp.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cnt = max(cnt, key=cv2.contourArea)
                    (_, _), (rw, rh), ang = cv2.minAreaRect(cnt)
                    d_med = float(np.median(depth[sel]))
                    width_m = self._pixel_size_to_m(min(rw, rh), d_med)
                    if kind == "cube":
                        # 上面の世界座標の中央値（遮蔽・縁の影響を抑える）
                        top = np.median(pts[sel], axis=0)
                        if width_m < CUBE_MIN_SIZE:
                            continue  # 遮蔽による残片
                        size = float(np.clip(width_m, 0.02, 0.08))
                        yaw_world = (-math.radians(ang)) % (math.pi / 2)
                        if yaw_world > math.pi / 4:
                            yaw_world -= math.pi / 2
                        det = Detection("", "cube", color, [float(top[0]), float(top[1]), float(top[2] - size / 2)],
                                        float(top[2]), size, float(yaw_world), (int(cu), int(cv_)), (x0, y0, bw, bh), area)
                    else:
                        # トレイは外形の中心（穴があっても外接矩形の中心で安定）
                        rect_c = cv2.minAreaRect(cnt)[0]
                        base = pts[int(np.clip(rect_c[1], 0, pts.shape[0] - 1)), int(np.clip(rect_c[0], 0, pts.shape[1] - 1))]
                        # 中心画素がブロックで隠れている場合は底面の高さを既知値で置く
                        cx_w, cy_w = float(base[0]), float(base[1])
                        det = Detection("", "tray", color, [cx_w, cy_w, TABLE_TOP_Z], float(TABLE_TOP_Z + TRAY_BASE_H),
                                        max(0.0, width_m - 0.008), 0.0, (int(rect_c[0]), int(rect_c[1])), (x0, y0, bw, bh), area)
                    raw.append(det)
        dets = self._assign_ids(raw)
        shown = w._TONE_LUT[rgb]
        img = self._annotate(shown, dets) if annotate else shown
        return dets, img

    # ------------------------------------------------------------------ 追跡
    def _new_id(self, base: str) -> str:
        existing = [t for t in self._tracks if t == base or t.startswith(base + "_")]
        if not existing:
            return base
        n = len(existing) + 1
        while f"{base}_{n}" in self._tracks:
            n += 1
        return f"{base}_{n}"

    def _assign_ids(self, raw: list[Detection]) -> list[Detection]:
        if not self._tracks:
            # 初回: 同色・同種が複数あれば _1, _2 ...（奥→手前、左→右の順）
            groups: dict[str, list[Detection]] = {}
            for d in raw:
                groups.setdefault(f"{d.color}_{d.kind}", []).append(d)
            for base, ds in groups.items():
                ds.sort(key=lambda d: (-d.position[1], d.position[0]))
                for i, d in enumerate(ds, 1):
                    d.id = base if len(ds) == 1 else f"{base}_{i}"
                    self._tracks[d.id] = dict(color=d.color, kind=d.kind, position=list(d.position), det=d)
            return sorted(raw, key=lambda d: d.id)
        pairs = []
        for i, d in enumerate(raw):
            for tid, t in self._tracks.items():
                if t["color"] != d.color or t["kind"] != d.kind:
                    continue
                dist = math.hypot(t["position"][0] - d.position[0], t["position"][1] - d.position[1])
                if dist < TRACK_RADIUS:
                    pairs.append((dist, i, tid))
        pairs.sort()
        assigned: dict[int, str] = {}
        used: set[str] = set()
        for dist, i, tid in pairs:
            if i in assigned or tid in used:
                continue
            assigned[i] = tid
            used.add(tid)
        for i, d in enumerate(raw):
            if i in assigned:
                d.id = assigned[i]
            else:
                d.id = self._new_id(f"{d.color}_{d.kind}")
                self._tracks[d.id] = dict(color=d.color, kind=d.kind, position=list(d.position))
            self._tracks[d.id]["position"] = list(d.position)
            self._tracks[d.id]["det"] = d
        return sorted(raw, key=lambda d: d.id)

    def update_track(self, object_id: str, xy, z: float | None = None):
        """操作で動かした物体の追跡位置を更新する（次回検出で同じ ID を引き継がせる）。"""
        t = self._tracks.get(object_id)
        if t is None:
            return
        t["position"][0] = float(xy[0])
        t["position"][1] = float(xy[1])
        if z is not None:
            t["position"][2] = float(z)

    def last_known(self, object_id: str):
        """現在見えていない物体の、最後に検出したときの情報（visible=False）を返す。"""
        t = self._tracks.get(object_id)
        if t is None or t.get("det") is None:
            return None
        d = copy.copy(t["det"])
        d.position = list(t["position"])
        d.visible = False
        return d

    def reset_ids(self):
        self._tracks = {}

    @staticmethod
    def _annotate(rgb: np.ndarray, dets: list[Detection]) -> np.ndarray:
        img = np.ascontiguousarray(rgb.copy())
        for d in dets:
            x0, y0, bw, bh = d.bbox
            col = (255, 255, 255) if d.kind == "cube" else (30, 30, 30)
            cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), col, 1)
            cv2.drawMarker(img, d.pixel, col, cv2.MARKER_CROSS, 10, 1)
            label = f"{d.id} ({d.position[0]:.2f},{d.position[1]:.2f})"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
            ty = y0 - 4 if y0 - 4 - th > 0 else y0 + bh + th + 4
            cv2.rectangle(img, (x0, ty - th - 2), (x0 + tw + 2, ty + 2), (0, 0, 0), -1)
            cv2.putText(img, label, (x0 + 1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        return img
