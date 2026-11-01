"""減衰最小二乗（DLS）法による 7 自由度アームの逆運動学。

TCP サイトの位置と姿勢（6 次元）を目標に、ヌル空間でホーム姿勢へ寄せる。
"""
from __future__ import annotations

import math

import mujoco
import numpy as np


def rot_down(yaw: float) -> np.ndarray:
    """グリッパが真下を向き、指の開閉軸（サイト y 軸）が yaw 方向を向く回転行列。"""
    x = np.array([math.cos(yaw), math.sin(yaw), 0.0])
    z = np.array([0.0, 0.0, -1.0])
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def rot_error(r_cur: np.ndarray, r_des: np.ndarray) -> np.ndarray:
    """回転誤差（世界座標系の小角度ベクトル）。"""
    e = np.zeros(3)
    for i in range(3):
        e += np.cross(r_cur[:, i], r_des[:, i])
    return 0.5 * e


class IKSolver:
    def __init__(self, model: mujoco.MjModel, site_id: int, q_home: np.ndarray, n_arm: int = 7,
                 damping: float = 5e-3, max_iter: int = 80, tol_pos: float = 2e-4, tol_rot: float = 2e-3,
                 max_step: float = 0.25, null_gain: float = 0.15):
        self.model = model
        self.data = mujoco.MjData(model)  # 作業用（シミュレーション状態を汚さない）
        self.site_id = site_id
        self.q_home = np.asarray(q_home, float)
        self.n = n_arm
        self.damping = damping
        self.max_iter = max_iter
        self.tol_pos = tol_pos
        self.tol_rot = tol_rot
        self.max_step = max_step
        self.null_gain = null_gain
        self.lo = model.jnt_range[:n_arm, 0] + 0.02
        self.hi = model.jnt_range[:n_arm, 1] - 0.02
        self._jacp = np.zeros((3, model.nv))
        self._jacr = np.zeros((3, model.nv))

    def fk(self, q: np.ndarray):
        self.data.qpos[: self.n] = q
        mujoco.mj_kinematics(self.model, self.data)
        mujoco.mj_comPos(self.model, self.data)
        return self.data.site_xpos[self.site_id].copy(), self.data.site_xmat[self.site_id].reshape(3, 3).copy()

    def solve(self, target_pos, target_rot: np.ndarray, q_init: np.ndarray):
        """目標位置・姿勢を満たす関節角を返す。(q, converged, pos_err, rot_err)"""
        target_pos = np.asarray(target_pos, float)
        q = np.clip(np.asarray(q_init, float).copy(), self.lo, self.hi)
        best = (q.copy(), np.inf)
        n = self.n
        for _ in range(self.max_iter):
            pos, rot = self.fk(q)
            e_pos = target_pos - pos
            e_rot = rot_error(rot, target_rot)
            err = np.linalg.norm(e_pos) + 0.1 * np.linalg.norm(e_rot)
            if err < best[1]:
                best = (q.copy(), err)
            if np.linalg.norm(e_pos) < self.tol_pos and np.linalg.norm(e_rot) < self.tol_rot:
                return q, True, float(np.linalg.norm(e_pos)), float(np.linalg.norm(e_rot))
            mujoco.mj_jacSite(self.model, self.data, self._jacp, self._jacr, self.site_id)
            J = np.vstack([self._jacp[:, :n], self._jacr[:, :n]])
            e = np.concatenate([e_pos, e_rot])
            JJt = J @ J.T + self.damping * np.eye(6)
            dq = J.T @ np.linalg.solve(JJt, e)
            # ヌル空間でホーム姿勢へ
            N = np.eye(n) - np.linalg.pinv(J) @ J
            dq += N @ (self.null_gain * (self.q_home - q))
            scale = np.max(np.abs(dq))
            if scale > self.max_step:
                dq *= self.max_step / scale
            q = np.clip(q + dq, self.lo, self.hi)
        q = best[0]
        pos, rot = self.fk(q)
        return q, False, float(np.linalg.norm(target_pos - pos)), float(np.linalg.norm(rot_error(rot, target_rot)))
