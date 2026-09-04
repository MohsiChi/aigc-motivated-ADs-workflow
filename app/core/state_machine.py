"""通用状态机组件。

设计要点：
- 状态机与业务代码分离，是纯粹的"规则组件"：流转表 + 守卫，谁都能复用。
- 任务级与镜头级复用同一套机制，只是流转表不同。
- 非法流转立刻抛 IllegalTransitionError，让 bug 在第一现场暴露，而不是静默吞掉。
"""
from __future__ import annotations

from enum import Enum


class IllegalTransitionError(Exception):
    """非法状态流转。"""

    def __init__(self, current: Enum, target: Enum):
        self.current = current
        self.target = target
        super().__init__(f"非法状态流转: {current.value} -> {target.value}")


class TaskStatus(str, Enum):
    PENDING = "PENDING"      # 已创建，等待调度
    RUNNING = "RUNNING"      # 流水线执行中
    SUCCEEDED = "SUCCEEDED"  # 全流程完成
    FAILED = "FAILED"        # 致命失败（重试后仍失败 / 步骤致命错误）


class SceneStatus(str, Enum):
    PENDING = "PENDING"                      # 等待生成
    GENERATING = "GENERATING"                # 素材生成中
    IN_REVIEW = "IN_REVIEW"                  # 质检中
    ACCEPTED = "ACCEPTED"                    # 质检通过
    REJECTED = "REJECTED"                    # 质检不通过（可返工）
    ACCEPTED_FALLBACK = "ACCEPTED_FALLBACK"  # 重试耗尽后降级采用（v1 策略：不阻塞整片，只告警）


TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
}

SCENE_TRANSITIONS: dict[SceneStatus, set[SceneStatus]] = {
    SceneStatus.PENDING: {SceneStatus.GENERATING},
    SceneStatus.GENERATING: {SceneStatus.IN_REVIEW},
    SceneStatus.IN_REVIEW: {SceneStatus.ACCEPTED, SceneStatus.REJECTED},
    SceneStatus.REJECTED: {SceneStatus.GENERATING, SceneStatus.ACCEPTED_FALLBACK},
    SceneStatus.ACCEPTED: set(),
    SceneStatus.ACCEPTED_FALLBACK: set(),
}


class StateMachine:
    """通用状态机：传入流转表与初始态，负责合法性检查 + 状态推进。"""

    def __init__(self, transitions: dict, initial: Enum):
        if initial not in transitions:
            raise ValueError(f"初始状态 {initial} 不在流转表中")
        self._transitions = transitions
        self._state = initial

    @property
    def state(self) -> Enum:
        return self._state

    def can_transition(self, target: Enum) -> bool:
        return target in self._transitions.get(self._state, set())

    def transition(self, target: Enum) -> Enum:
        """流转到目标态；非法流转抛 IllegalTransitionError。"""
        if not self.can_transition(target):
            raise IllegalTransitionError(self._state, target)
        self._state = target
        return self._state
