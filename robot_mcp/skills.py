"""アームの運動スキル（直線移動・把持・設置・ホーム復帰）。

直交座標で補間した経路を 50 Hz で IK に通し、関節位置サーボへ指令する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .ik import IKSolver, rot_down
from .sim import Q_HOME, TABLE_TOP_Z, TCP_HOME, WORKSPACE_X, WORKSPACE_Y, World

CTRL_STEPS = 10  # 0.002 s × 10 = 50 Hz 制御


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def grasp_yaw_for(obj_yaw: float) -> float:
    """立方体の yaw に対し、関節限界に余裕のある把持 yaw（±45° 以内）を選ぶ。"""
    best = None
    for k in range(-2, 3):
        c = _wrap(obj_yaw + k * math.pi / 2)
        if best is None or abs(c) < abs(best):
            best = c
    return float(best)


@dataclass
class MotionResult:
    ok: bool
    message: str
    tcp_error: float = 0.0


class Arm:
    SAFE_Z_MIN = TABLE_TOP_Z + 0.008
    SAFE_Z_MAX = 0.60
    APPROACH_DZ = 0.10

    def __init__(self, world: World):
        self.world = world
        self.rebind()

    def rebind(self):
        """world.reset() 後に呼ぶ。"""
        w = self.world
        self.ik = IKSolver(w.model, w.tcp_site, Q_HOME)
        pos, rot = w.tcp_pose()
        self.cmd_pos = pos.copy()
        self.cmd_yaw = math.atan2(rot[1, 0], rot[0, 0])
        self.holding: str | None = None
        self.gripper_open_frac = 1.0

    # ------------------------------------------------------------------ 低レベル
    @property
    def q(self) -> np.ndarray:
        return self.world.data.qpos[:7].copy()

    def clamp(self, pos):
        p = np.asarray(pos, float).copy()
        p[0] = np.clip(p[0], WORKSPACE_X[0] - 0.08, WORKSPACE_X[1] + 0.06)
        p[1] = np.clip(p[1], WORKSPACE_Y[0] - 0.06, WORKSPACE_Y[1] + 0.06)
        p[2] = np.clip(p[2], self.SAFE_Z_MIN, self.SAFE_Z_MAX)
        return p

    def move_to(self, pos, yaw: float | None = None, speed: float = 0.30, min_time: float = 0.25) -> MotionResult:
        """TCP を直線経路で pos へ動かす（グリッパは真下向き、yaw は指の開閉方向）。"""
        w = self.world
        target = self.clamp(pos)
        yaw = self.cmd_yaw if yaw is None else float(yaw)
        dyaw = _wrap(yaw - self.cmd_yaw)
        dist = float(np.linalg.norm(target - self.cmd_pos))
        duration = max(min_time, dist / speed, abs(dyaw) / 1.2)
        n = max(1, int(math.ceil(duration / (CTRL_STEPS * w.model.opt.timestep))))
        q = self.q
        start_pos, start_yaw = self.cmd_pos.copy(), self.cmd_yaw
        worst = 0.0
        for i in range(1, n + 1):
            s = i / n
            s = s * s * (3 - 2 * s)  # smoothstep
            p = start_pos + (target - start_pos) * s
            y = start_yaw + dyaw * s
            q, ok, ep, er = self.ik.solve(p, rot_down(y), q)
            worst = max(worst, ep)
            w.data.ctrl[:7] = q
            w.step(CTRL_STEPS)
        self.cmd_pos, self.cmd_yaw = target, _wrap(yaw)
        w.settle(0.25)
        actual, _ = w.tcp_pose()
        err = float(np.linalg.norm(actual - target))
        ok = err < 0.01 and worst < 0.01
        return MotionResult(ok, "到達" if ok else f"目標との誤差 {err*1000:.1f} mm（IK 誤差最大 {worst*1000:.1f} mm）", err)

    def set_gripper(self, open_frac: float, wait: float = 0.45):
        open_frac = float(np.clip(open_frac, 0, 1))
        self.world.data.ctrl[7] = 255 * open_frac
        self.gripper_open_frac = open_frac
        self.world.settle(wait)

    def gap(self) -> float:
        return self.world.gripper_gap()

    # ------------------------------------------------------------------ スキル
    def home(self) -> MotionResult:
        w = self.world
        # 低い位置にいるときは一度持ち上げてから戻る
        if self.cmd_pos[2] < 0.15:
            self.move_to([self.cmd_pos[0], self.cmd_pos[1], 0.20], speed=0.25)
        r = self.move_to(TCP_HOME, 0.0, speed=0.35)
        return r

    def pick(self, pos, yaw: float, size: float, name: str | None = None) -> dict:
        """pos（物体中心）を yaw 方向に把持して持ち上げる。"""
        pos = np.asarray(pos, float)
        gyaw = grasp_yaw_for(yaw)
        grasp_z = max(pos[2], TABLE_TOP_Z + size / 2) + 0.002
        above = np.array([pos[0], pos[1], grasp_z + self.APPROACH_DZ])
        log = []
        if self.gripper_open_frac < 0.95:
            self.set_gripper(1.0, wait=0.3)
        r1 = self.move_to(above, gyaw, speed=0.35)
        log.append(f"上空へ移動: {r1.message}")
        r2 = self.move_to([pos[0], pos[1], grasp_z], gyaw, speed=0.15)
        log.append(f"下降: {r2.message}")
        self.set_gripper(0.0, wait=0.5)
        gap_before = self.gap()
        r3 = self.move_to(above, gyaw, speed=0.15)
        self.world.settle(0.2)
        gap = self.gap()
        grasped = (size - 0.015) < gap < (size + 0.006)
        log.append(f"把持後の指間隔 {gap*1000:.1f} mm（物体 {size*1000:.0f} mm）")
        if grasped:
            self.holding = name or "object"
        else:
            self.holding = None
            self.set_gripper(1.0, wait=0.2)
        return dict(success=bool(grasped), gap_mm=round(gap * 1000, 1), log=log,
                    tcp=[round(float(v), 4) for v in self.cmd_pos])

    def place(self, pos, size: float, yaw: float | None = None, release_clearance: float = 0.004) -> dict:
        """pos（置き先の支持面上の目標点、z は支持面の高さ）へ持っている物体を置く。"""
        pos = np.asarray(pos, float)
        tcp_z = pos[2] + size / 2 + release_clearance
        above = np.array([pos[0], pos[1], tcp_z + self.APPROACH_DZ])
        yaw = self.cmd_yaw if yaw is None else grasp_yaw_for(yaw)
        log = []
        r1 = self.move_to(above, yaw, speed=0.30)
        log.append(f"上空へ移動: {r1.message}")
        r2 = self.move_to([pos[0], pos[1], tcp_z], yaw, speed=0.15)
        log.append(f"下降: {r2.message}")
        gap_before = self.gap()
        self.set_gripper(1.0, wait=0.4)
        held = self.holding
        self.holding = None
        r3 = self.move_to(above, yaw, speed=0.25)
        log.append("解放して退避")
        return dict(success=r2.ok, released=held, log=log,
                    tcp=[round(float(v), 4) for v in self.cmd_pos])
