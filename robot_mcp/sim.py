"""MuJoCo 上の卓上 Franka Panda シーン。

シーンは MjSpec で動的に組み立てる（mujoco_menagerie の panda.xml に
テーブル・ブロック・トレイ・カメラ・TCP サイトを追加する）。
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import mujoco
import numpy as np

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
PANDA_XML = os.path.join(ASSETS_DIR, "menagerie", "franka_emika_panda", "panda.xml")

TABLE_TOP_Z = 0.0
# ブロックを置ける作業領域（ロボット基部が原点、+x が正面）
WORKSPACE_X = (0.30, 0.66)
WORKSPACE_Y = (-0.30, 0.30)

# 物体色（RGB 0-1）。知覚モジュール（HSV 分割）と対応させる。
COLORS = {
    "red": (0.85, 0.10, 0.10),
    "green": (0.10, 0.65, 0.18),
    "blue": (0.10, 0.30, 0.90),
    "yellow": (0.92, 0.82, 0.10),
    "orange": (0.95, 0.45, 0.05),
    "purple": (0.60, 0.20, 0.85),
}
COLOR_JA = {"red": "赤", "green": "緑", "blue": "青", "yellow": "黄", "orange": "オレンジ", "purple": "紫"}

# menagerie の home 姿勢（IK の初期値・ヌル空間の目標姿勢）
Q_HOME = np.array([0.0, -0.3, 0.0, -2.2, 0.0, 1.9, 0.785])
# ホーム時の TCP（手先）位置。カメラ視野を塞がないよう、やや後方・上方。
TCP_HOME = np.array([0.20, 0.0, 0.40])

TRAY_RIM_H = 0.012  # トレイの縁の高さ
TRAY_BASE_H = 0.006  # トレイ底板の厚み


@dataclass
class ObjectSpec:
    name: str
    kind: str  # "cube" | "tray"
    color: str
    pos: tuple[float, float]  # (x, y) テーブル上
    size: float  # cube: 一辺の長さ / tray: 内寸の一辺
    yaw: float = 0.0  # rad

    @property
    def rgba(self):
        c = COLORS[self.color]
        if self.kind == "tray":  # トレイは同色のブロックと見分けやすいよう暗めにする
            return (c[0] * 0.62, c[1] * 0.62, c[2] * 0.62, 1.0)
        return (*c, 1.0)

    @property
    def support_z(self) -> float:
        """この物体の上に物を置くときの支持面の高さ。"""
        if self.kind == "tray":
            return TABLE_TOP_Z + TRAY_BASE_H
        return TABLE_TOP_Z + self.size  # 立方体の天面（初期配置時）


def _unique_names(objs: list[ObjectSpec]) -> list[ObjectSpec]:
    seen: dict[str, int] = {}
    for o in objs:
        base = f"{o.color}_{o.kind}"
        seen[base] = seen.get(base, 0) + 1
        o.name = base if seen[base] == 1 else f"{base}_{seen[base]}"
    # 2 個目以降がある色は 1 個目にも _1 を付けて揃える
    counts: dict[str, int] = {}
    for o in objs:
        counts[f"{o.color}_{o.kind}"] = counts.get(f"{o.color}_{o.kind}", 0) + 1
    for o in objs:
        base = f"{o.color}_{o.kind}"
        if counts[base] > 1 and o.name == base:
            o.name = f"{base}_1"
    return objs


def make_layout(name: str, rng: np.random.Generator) -> list[ObjectSpec]:
    """名前付きレイアウトを返す。"""
    objs: list[ObjectSpec] = []
    if name == "basic":
        objs += [
            ObjectSpec("", "cube", "red", (0.48, 0.14), 0.04, 0.0),
            ObjectSpec("", "cube", "green", (0.56, -0.02), 0.04, 0.3),
            ObjectSpec("", "cube", "blue", (0.44, -0.18), 0.04, -0.4),
            ObjectSpec("", "tray", "blue", (0.60, 0.25), 0.14),
            ObjectSpec("", "tray", "yellow", (0.60, -0.25), 0.14),
        ]
    elif name == "sorting":
        objs += [
            ObjectSpec("", "cube", "red", (0.42, 0.06), 0.04, 0.2),
            ObjectSpec("", "cube", "blue", (0.50, -0.10), 0.04, -0.5),
            ObjectSpec("", "cube", "red", (0.56, 0.08), 0.04, 0.9),
            ObjectSpec("", "cube", "blue", (0.40, -0.06), 0.04, 0.1),
            ObjectSpec("", "cube", "green", (0.50, 0.20), 0.04, 0.0),
            ObjectSpec("", "tray", "red", (0.60, 0.25), 0.14),
            ObjectSpec("", "tray", "blue", (0.60, -0.25), 0.14),
        ]
    elif name == "stacking":
        objs += [
            ObjectSpec("", "cube", "blue", (0.50, 0.16), 0.055, 0.0),
            ObjectSpec("", "cube", "green", (0.44, -0.08), 0.045, 0.4),
            ObjectSpec("", "cube", "red", (0.58, -0.14), 0.035, -0.3),
            ObjectSpec("", "tray", "yellow", (0.60, 0.25), 0.14),
        ]
    elif name == "random":
        colors = list(COLORS)
        n = int(rng.integers(3, 6))
        placed: list[tuple[float, float]] = [(0.60, 0.25), (0.60, -0.25)]
        objs += [
            ObjectSpec("", "tray", "red", (0.60, 0.25), 0.14),
            ObjectSpec("", "tray", "blue", (0.60, -0.25), 0.14),
        ]
        for _ in range(n):
            for _try in range(100):
                x = rng.uniform(WORKSPACE_X[0] + 0.03, WORKSPACE_X[1] - 0.10)
                y = rng.uniform(WORKSPACE_Y[0] + 0.03, WORKSPACE_Y[1] - 0.03)
                if all(math.hypot(x - px, y - py) > 0.13 for px, py in placed):
                    break
            placed.append((x, y))
            objs.append(ObjectSpec("", "cube", colors[int(rng.integers(len(colors)))], (x, y), float(rng.choice([0.035, 0.04, 0.045])), float(rng.uniform(-0.8, 0.8))))
    else:
        raise ValueError(f"未知のレイアウト: {name}（basic / sorting / stacking / random）")
    return _unique_names(objs)


def camera_xyaxes(pos, target, up=(0, 0, 1)):
    """pos から target を見るカメラの xyaxes を返す。"""
    pos = np.asarray(pos, float)
    f = np.asarray(target, float) - pos
    f /= np.linalg.norm(f)
    x = np.cross(f, np.asarray(up, float))
    x /= np.linalg.norm(x)
    y = np.cross(x, f)
    return np.concatenate([x, y]).tolist()


def yaw_to_quat(yaw: float):
    return [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


def quat_to_yaw(q) -> float:
    w, x, y, z = q
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


class World:
    """シミュレーション世界。物理ステップ・描画・物体情報を扱う。"""

    CAMERAS = {
        # name: (pos, target, fovy)
        "front": ((1.15, -0.85, 0.62), (0.40, 0.0, 0.10), 48),
        "side": ((0.45, 1.10, 0.50), (0.45, 0.0, 0.08), 45),
    }
    TOP_CAM_POS = (0.48, 0.0, 1.25)
    TOP_CAM_FOVY = 26

    def __init__(self, layout: str = "basic", seed: int | None = None, realtime: float = 1.0,
                 render_size: tuple[int, int] = (640, 480)):
        self.realtime = realtime
        self.render_w, self.render_h = render_size
        self.frame_callbacks: list = []  # fn(world, rgb_front) — 約 30fps（シミュレーション時間）で呼ぶ
        self.render_every = 17  # 0.002 s × 17 ≈ 30 fps
        self._step_count = 0
        self._wall_start = time.time()
        self.renderer: mujoco.Renderer | None = None
        self._old_renderers: list = []
        self.reset(layout, seed)

    # ------------------------------------------------------------------ 構築
    def _build_spec(self, objs: list[ObjectSpec]) -> mujoco.MjSpec:
        spec = mujoco.MjSpec.from_file(PANDA_XML)
        spec.modelname = "panda_tabletop"
        spec.option.timestep = 0.002
        spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
        spec.option.impratio = 10
        spec.option.noslip_iterations = 2
        spec.visual.map.znear = 0.01
        spec.visual.map.zfar = 6.0
        spec.visual.global_.offwidth = max(1280, self.render_w)
        spec.visual.global_.offheight = max(960, self.render_h)
        # 照明は合計が 1 を超えて色が飽和しない強さにする（知覚の色相が崩れないように）
        spec.visual.headlight.ambient[:] = [0.25, 0.25, 0.25]
        spec.visual.headlight.diffuse[:] = [0.30, 0.30, 0.30]
        spec.visual.headlight.specular[:] = [0.03, 0.03, 0.03]
        # panda.xml 付属の真上ライト（diffuse 0.7）は天面を飽和させるので外す
        for light in list(spec.lights):
            if light.name == "top":
                light.diffuse[:] = [0, 0, 0]
                light.specular[:] = [0, 0, 0]
                light.castshadow = False

        # グリッパのサーボを強める（kp 100 → 300、目標開度の対応は維持）
        act8 = spec.actuator("actuator8")
        act8.gainprm[0] = 0.01568627451 * 3
        act8.biasprm[1] = -300
        act8.biasprm[2] = -30

        wb = spec.worldbody
        # 床（遠く下）とテーブル
        spec.add_texture(name="sky", type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
                         builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT, width=512, height=3072,
                         rgb1=[0.55, 0.65, 0.78], rgb2=[0.12, 0.14, 0.20])
        tex = spec.add_texture(name="floor_tex", type=mujoco.mjtTexture.mjTEXTURE_2D,
                               builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER, width=512, height=512,
                               rgb1=[0.82, 0.82, 0.84], rgb2=[0.72, 0.72, 0.75], mark=mujoco.mjtMark.mjMARK_EDGE,
                               markrgb=[0.9, 0.9, 0.9])
        mat = spec.add_material(name="floor_mat", texrepeat=[6, 6], texuniform=True, reflectance=0.05)
        mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = "floor_tex"
        wb.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[3, 3, 0.1], pos=[0, 0, -0.75],
                    material="floor_mat")
        table = wb.add_body(name="table", pos=[0.35, 0.0, TABLE_TOP_Z - 0.02])
        table.add_geom(name="table_top", type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.55, 0.60, 0.02],
                       rgba=[0.74, 0.74, 0.77, 1], friction=[1.0, 0.005, 0.0001])
        for sx in (-1, 1):
            for sy in (-1, 1):
                table.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.025, 0.025, 0.355],
                               pos=[sx * 0.50, sy * 0.55, -0.375], rgba=[0.35, 0.35, 0.37, 1],
                               contype=0, conaffinity=0)
        # ライト
        wb.add_light(name="key", pos=[0.8, -0.6, 1.6], dir=[-0.4, 0.4, -1.0], diffuse=[0.40, 0.40, 0.40],
                     specular=[0.05, 0.05, 0.05], castshadow=True)
        wb.add_light(name="fill", pos=[0.3, 0.9, 1.2], dir=[0.1, -0.7, -1.0], diffuse=[0.10, 0.10, 0.10],
                     castshadow=False)

        # 物体（マット材質: 鏡面反射があると真上カメラで天面が白く飽和し、色相が崩れる）
        mats: dict[str, str] = {}
        for o in objs:
            key = f"mat_{o.color}_{o.kind}"
            if key not in mats:
                spec.add_material(name=key, rgba=list(o.rgba), specular=0.0, shininess=0.0, reflectance=0.0)
                mats[key] = key
            if o.kind == "cube":
                h = o.size / 2
                b = wb.add_body(name=o.name, pos=[o.pos[0], o.pos[1], TABLE_TOP_Z + h + 0.001],
                                quat=yaw_to_quat(o.yaw))
                b.add_freejoint()
                b.add_geom(name=o.name, type=mujoco.mjtGeom.mjGEOM_BOX, size=[h, h, h], material=key,
                           mass=0.06, friction=[1.2, 0.01, 0.0005], condim=4, solref=[0.01, 1],
                           solimp=[0.95, 0.99, 0.001, 0.5, 2])
            elif o.kind == "tray":
                w = o.size / 2
                t = 0.004  # 縁の厚み
                b = wb.add_body(name=o.name, pos=[o.pos[0], o.pos[1], TABLE_TOP_Z])
                b.add_geom(name=o.name, type=mujoco.mjtGeom.mjGEOM_BOX, size=[w + t, w + t, TRAY_BASE_H / 2],
                           pos=[0, 0, TRAY_BASE_H / 2], material=key)
                rim_z = TRAY_BASE_H + TRAY_RIM_H / 2
                for i, (px, py, sx, sy) in enumerate([(w + t / 2, 0, t / 2, w + t), (-(w + t / 2), 0, t / 2, w + t),
                                                      (0, w + t / 2, w + t, t / 2), (0, -(w + t / 2), w + t, t / 2)]):
                    b.add_geom(name=f"{o.name}_rim{i}", type=mujoco.mjtGeom.mjGEOM_BOX, size=[sx, sy, TRAY_RIM_H / 2],
                               pos=[px, py, rim_z], material=key)

        # カメラ
        for cname, (cpos, ctarget, fovy) in self.CAMERAS.items():
            wb.add_camera(name=cname, pos=list(cpos), xyaxes=camera_xyaxes(cpos, ctarget), fovy=fovy)
        wb.add_camera(name="top", pos=list(self.TOP_CAM_POS), xyaxes=[0, -1, 0, 1, 0, 0], fovy=self.TOP_CAM_FOVY)
        hand = spec.body("hand")
        hand.add_site(name="tcp", pos=[0, 0, 0.1034], size=[0.005, 0.005, 0.005], rgba=[0, 1, 0, 0])
        hand.add_camera(name="wrist", pos=[0.07, 0.0, 0.03], xyaxes=[0, -1, 0, -0.8, 0, -0.6], fovy=80)
        return spec

    def reset(self, layout: str = "basic", seed: int | None = None):
        rng = np.random.default_rng(seed)
        self.layout_name = layout
        self.objects: dict[str, ObjectSpec] = {o.name: o for o in make_layout(layout, rng)}
        spec = self._build_spec(list(self.objects.values()))
        self.model = spec.compile()
        self.xml = spec.to_xml()
        self.data = mujoco.MjData(self.model)
        self.tcp_site = self.model.site("tcp").id
        self.arm_qpos = np.arange(7)  # panda を先に読み込むため関節は qpos[0:7]
        self.finger_qpos = np.array([7, 8])
        # 初期姿勢: TCP_HOME（トップカメラの視野外）を IK で求める
        from .ik import IKSolver, rot_down
        q_init, _, _, _ = IKSolver(self.model, self.tcp_site, Q_HOME).solve(TCP_HOME, rot_down(0.0), Q_HOME)
        self.q_init = q_init
        self.data.qpos[:7] = q_init
        self.data.qpos[7:9] = 0.04
        self.data.ctrl[:7] = q_init
        self.data.ctrl[7] = 255
        mujoco.mj_forward(self.model, self.data)
        # 注意: Renderer を close / GC すると、生きている別の Renderer の深度出力が壊れる
        # （macOS で確認）。古いものは閉じずに保持する。
        if self.renderer is not None:
            self._old_renderers.append(self.renderer)
        self.renderer = mujoco.Renderer(self.model, self.render_h, self.render_w)
        if getattr(self, "renderer_small", None) is not None:
            self._old_renderers.append(self.renderer_small)
        self.renderer_small = mujoco.Renderer(self.model, 240, 320)  # 手首カメラの小窓用
        self._step_count = 0
        self._wall_start = time.time()

    # ------------------------------------------------------------------ 時間発展
    def step(self, n: int = 1):
        for _ in range(n):
            # 重力・コリオリ項の補償（位置サーボのたわみを除く）
            self.data.qfrc_applied[:7] = self.data.qfrc_bias[:7]
            mujoco.mj_step(self.model, self.data)
            self._step_count += 1
            if self.frame_callbacks and self._step_count % self.render_every == 0:
                rgb = self.render_display("front")
                for cb in list(self.frame_callbacks):
                    cb(self, rgb)
        if self.realtime > 0:
            target_wall = self._wall_start + self.data.time / self.realtime
            now = time.time()
            if now < target_wall:
                time.sleep(target_wall - now)
            elif now - target_wall > 0.2:
                # 待ち時間（ツール呼び出しの合間）で遅れたぶんは追いつかずに再同期
                self._wall_start = now - self.data.time / self.realtime

    def settle(self, seconds: float):
        self.step(int(round(seconds / self.model.opt.timestep)))

    # ------------------------------------------------------------------ 描画
    def render(self, camera: str = "front", depth: bool = False, shadows: bool = True) -> np.ndarray:
        r = self.renderer
        if depth:
            r.enable_depth_rendering()
        else:
            r.disable_depth_rendering()
        r.update_scene(self.data, camera=camera)
        r.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 1 if shadows else 0
        return r.render().copy()

    # 表示・録画用のトーン補正（知覚は補正前の画像を使う）
    _TONE_LUT = (255.0 * (np.arange(256) / 255.0) ** 0.72).astype(np.uint8)

    def render_display(self, camera: str = "front") -> np.ndarray:
        """人が見る用に少し明るくした画像（ライブビューア・録画・look の画像）。"""
        return self._TONE_LUT[self.render(camera)]

    def render_small(self, camera: str = "wrist") -> np.ndarray:
        """小窓用の低解像度（320x240）描画。"""
        r = self.renderer_small
        r.disable_depth_rendering()
        r.update_scene(self.data, camera=camera)
        return self._TONE_LUT[r.render()]

    def camera_intrinsics(self, camera: str):
        cid = self.model.camera(camera).id
        fovy = math.radians(self.model.cam_fovy[cid])
        f = (self.render_h / 2) / math.tan(fovy / 2)
        return f, self.render_w / 2, self.render_h / 2

    def camera_pose(self, camera: str):
        cid = self.model.camera(camera).id
        return self.data.cam_xpos[cid].copy(), self.data.cam_xmat[cid].reshape(3, 3).copy()

    # ------------------------------------------------------------------ 状態
    def tcp_pose(self):
        return self.data.site_xpos[self.tcp_site].copy(), self.data.site_xmat[self.tcp_site].reshape(3, 3).copy()

    def gripper_gap(self) -> float:
        return float(self.data.qpos[7] + self.data.qpos[8])

    def object_pose(self, name: str):
        b = self.data.body(name)
        return b.xpos.copy(), b.xquat.copy()

    def object_info(self, name: str) -> dict:
        o = self.objects[name]
        pos, quat = self.object_pose(name)
        info = dict(name=name, kind=o.kind, color=o.color, color_ja=COLOR_JA[o.color], size=o.size,
                    position=[round(float(v), 4) for v in pos], yaw=round(quat_to_yaw(quat), 3))
        if o.kind == "cube":
            info["top_z"] = round(float(pos[2] + o.size / 2), 4)
        else:
            info["top_z"] = round(TABLE_TOP_Z + TRAY_BASE_H, 4)
        return info

    def all_object_info(self) -> list[dict]:
        return [self.object_info(n) for n in self.objects]

    def set_object_pose(self, name: str, xy, yaw: float | None = None, z: float | None = None):
        """物体をテレポートさせる（外乱・シーン編集用）。"""
        o = self.objects[name]
        jid = self.model.body(name).jntadr[0]
        adr = self.model.jnt_qposadr[jid]
        dadr = self.model.jnt_dofadr[jid]
        if z is None:
            z = TABLE_TOP_Z + o.size / 2 + 0.001
        if yaw is None:
            yaw = quat_to_yaw(self.data.qpos[adr + 3:adr + 7])
        self.data.qpos[adr:adr + 3] = [xy[0], xy[1], z]
        self.data.qpos[adr + 3:adr + 7] = yaw_to_quat(yaw)
        self.data.qvel[dadr:dadr + 6] = 0
        mujoco.mj_forward(self.model, self.data)
