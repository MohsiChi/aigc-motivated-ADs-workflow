"""状态机组件单测：合法流转放行，非法流转拒绝。"""
import pytest

from app.core.state_machine import (
    SCENE_TRANSITIONS,
    TASK_TRANSITIONS,
    IllegalTransitionError,
    SceneStatus,
    StateMachine,
    TaskStatus,
)


def test_task_happy_path():
    sm = StateMachine(TASK_TRANSITIONS, TaskStatus.PENDING)
    sm.transition(TaskStatus.RUNNING)
    sm.transition(TaskStatus.SUCCEEDED)
    assert sm.state is TaskStatus.SUCCEEDED


def test_task_failure_path():
    sm = StateMachine(TASK_TRANSITIONS, TaskStatus.PENDING)
    sm.transition(TaskStatus.RUNNING)
    sm.transition(TaskStatus.FAILED)
    assert sm.state is TaskStatus.FAILED


def test_task_terminal_states_reject_everything():
    sm = StateMachine(TASK_TRANSITIONS, TaskStatus.PENDING)
    sm.transition(TaskStatus.RUNNING)
    sm.transition(TaskStatus.SUCCEEDED)
    with pytest.raises(IllegalTransitionError):
        sm.transition(TaskStatus.RUNNING)


def test_task_cannot_skip_running():
    sm = StateMachine(TASK_TRANSITIONS, TaskStatus.PENDING)
    with pytest.raises(IllegalTransitionError):
        sm.transition(TaskStatus.SUCCEEDED)


def test_scene_retry_path():
    """PENDING -> GENERATING -> IN_REVIEW -> REJECTED -> 返工 -> ACCEPTED"""
    sm = StateMachine(SCENE_TRANSITIONS, SceneStatus.PENDING)
    sm.transition(SceneStatus.GENERATING)
    sm.transition(SceneStatus.IN_REVIEW)
    sm.transition(SceneStatus.REJECTED)
    sm.transition(SceneStatus.GENERATING)   # 重试
    sm.transition(SceneStatus.IN_REVIEW)
    sm.transition(SceneStatus.ACCEPTED)
    assert sm.state is SceneStatus.ACCEPTED


def test_scene_fallback_after_retries():
    sm = StateMachine(SCENE_TRANSITIONS, SceneStatus.PENDING)
    sm.transition(SceneStatus.GENERATING)
    sm.transition(SceneStatus.IN_REVIEW)
    sm.transition(SceneStatus.REJECTED)
    sm.transition(SceneStatus.ACCEPTED_FALLBACK)
    assert sm.state is SceneStatus.ACCEPTED_FALLBACK


def test_scene_cannot_skip_review():
    sm = StateMachine(SCENE_TRANSITIONS, SceneStatus.PENDING)
    sm.transition(SceneStatus.GENERATING)
    with pytest.raises(IllegalTransitionError):
        sm.transition(SceneStatus.ACCEPTED)


def test_scene_blocked_is_terminal():
    """BLOCKED 是终态：进得去，出不来。"""
    sm = StateMachine(SCENE_TRANSITIONS, SceneStatus.PENDING)
    sm.transition(SceneStatus.GENERATING)
    sm.transition(SceneStatus.IN_REVIEW)
    sm.transition(SceneStatus.REJECTED)
    sm.transition(SceneStatus.BLOCKED)
    assert sm.state is SceneStatus.BLOCKED
    assert not sm.can_transition(SceneStatus.GENERATING)
    with pytest.raises(IllegalTransitionError):
        sm.transition(SceneStatus.ACCEPTED)


def test_accepted_scene_cannot_be_blocked():
    """已验收的镜头不得被事后拦截（ACCEPTED 是终态）。"""
    sm = StateMachine(SCENE_TRANSITIONS, SceneStatus.PENDING)
    sm.transition(SceneStatus.GENERATING)
    sm.transition(SceneStatus.IN_REVIEW)
    sm.transition(SceneStatus.ACCEPTED)
    assert not sm.can_transition(SceneStatus.BLOCKED)


def test_blocked_only_reachable_from_rejected():
    """只有经质检判定不通过，才允许进入 BLOCKED——不许绕过质检直接拦。"""
    assert SceneStatus.BLOCKED not in SCENE_TRANSITIONS[SceneStatus.PENDING]
    assert SceneStatus.BLOCKED not in SCENE_TRANSITIONS[SceneStatus.GENERATING]
    assert SceneStatus.BLOCKED not in SCENE_TRANSITIONS[SceneStatus.IN_REVIEW]
    assert SceneStatus.BLOCKED in SCENE_TRANSITIONS[SceneStatus.REJECTED]
