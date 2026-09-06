"""Workflow 引擎：流水线顺序执行 + 质检返工循环 + 全程留痕。

设计要点（对应 JD"任务调度系统"）：
- v1 用"进程内队列 + Worker 线程"，但对外形态已是"提交立即返回、轮询取结果"；
  队列是注入的边界，v2 换 Redis/MQ 只动调度层，引擎与 Agent 不动。
- 双层状态机：任务级（TaskStatus）与镜头级（SceneStatus）分别驱动、全部留痕。
- 幂等：同一任务只会被执行一次（重复调度直接跳过）。
- 容错：Worker 内的未捕获异常不会杀死调度线程。
"""
from __future__ import annotations

import logging
import queue
import threading

from app.agents.base import AgentContext, BaseAgent
from app.config import Settings
from app.core.exporter import export_task_bundle
from app.core.models import ReviewReport, Storyboard, TaskRecord
from app.core.repair_policy import has_repair
from app.core.state_machine import (
    SCENE_TRANSITIONS,
    TASK_TRANSITIONS,
    IllegalTransitionError,
    SceneStatus,
    StateMachine,
    TaskStatus,
)
from app.core.task_store import TaskStore

log = logging.getLogger("workflow")


class StepFailure(RuntimeError):
    """某个步骤致命失败，任务应转 FAILED。"""


# 步骤名 -> artifacts 里的 key
_ARTIFACT_KEY = {
    "script": "storyboard",
    "prompt": "prompts",
    "generation": "generation",
    "review": "review_report",
    "compose": "compose",
}


class WorkflowEngine:
    PIPELINE = ("script", "prompt", "generation", "review", "compose")

    def __init__(self, store: TaskStore, settings: Settings, agents: dict[str, BaseAgent]):
        self._store = store
        self._settings = settings
        self._agents = agents
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._scene_machines: dict[int, StateMachine] = {}

    # ---------------- 生命周期（FastAPI lifespan 调用） ----------------

    def start(self) -> None:
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name="workflow-worker", daemon=True
        )
        self._worker_thread.start()
        log.info("Workflow Worker 已启动")

    def stop(self) -> None:
        self._queue.put(None)  # 哨兵
        if self._worker_thread:
            self._worker_thread.join(timeout=10)
        log.info("Workflow Worker 已停止")

    def submit(self, task_id: str) -> None:
        """API 层提交任务入队（提交即返回，不阻塞请求）。"""
        self._queue.put(task_id)

    def _worker_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if task_id is None:
                break
            try:
                self.run_task(task_id)
            except Exception as e:  # noqa: BLE001 最后防线：不允许炸死调度线程
                log.exception("Worker 未预期异常 task=%s", task_id)
                self._fail_task_safe(task_id, f"Worker 异常: {e}")

    def _fail_task_safe(self, task_id: str, error: str) -> None:
        """兜底失败路径：仍然走状态机守卫（D2），并关闭停留在 RUNNING 的步骤。"""
        rec = self._store.get_task(task_id)
        if rec is None:
            return
        sm = StateMachine(TASK_TRANSITIONS, TaskStatus(rec.status))
        if sm.can_transition(TaskStatus.FAILED):
            sm.transition(TaskStatus.FAILED)
            self._store.update_task(task_id, status="FAILED", error=error)
        self._store.fail_running_steps(task_id, "worker 异常中断")

    def _export_bundle(self, ctx: AgentContext, task_id: str, note: str) -> None:
        """导出规范化产物目录。成功与失败路径都必须调用。

        失败任务同样需要审计留痕——尤其合规门禁拦截的场景，
        00_task.json 与 05_review/review_report.json 正是事后追责要看的东西。

        前置条件：任务状态已落终态、scene_states 已写入 artifacts，
        这样 00_task.json 里的 status / scene_states 一定是终态快照
        （延续 Session 1 定下的"状态最后写"对外可见性契约）。
        """
        rec = self._store.get_task(task_id)
        if rec is None:
            return
        bundle = export_task_bundle(rec, self._settings.output_dir / task_id)
        self._store.merge_artifact(task_id, "export_dir", str(bundle))
        ctx.artifacts["export_dir"] = str(bundle)
        self._store.append_event(task_id, f"规范化产物已导出: {bundle}（{note}）")

    # ---------------- 核心：跑一个任务 ----------------

    def run_task(self, task_id: str) -> None:
        rec = self._store.get_task(task_id)
        if rec is None:
            raise ValueError(f"任务不存在: {task_id}")
        # 每任务重置镜头状态机，防止上一个任务的残留状态串进来
        self._scene_machines = {}

        sm = StateMachine(TASK_TRANSITIONS, TaskStatus(rec.status))
        if sm.state is not TaskStatus.PENDING:
            log.info("[%s] 重复调度，跳过（幂等保护）", task_id)
            return
        sm.transition(TaskStatus.RUNNING)
        self._store.update_task(task_id, status="RUNNING", error="")
        self._store.append_event(task_id, "任务开始")

        ctx = AgentContext(
            task_id=task_id,
            topic=rec.topic,
            product_info=rec.product_info,
            scene_count=rec.scene_count,
            settings=self._settings,
            artifacts={},
            extra={},
        )

        try:
            self._run_step(ctx, "script")
            self._init_scene_machines(ctx)
            self._run_step(ctx, "prompt")
            self._move_scenes(all_ids(ctx), SceneStatus.GENERATING)
            self._run_step(ctx, "generation")
            self._move_scenes(all_ids(ctx), SceneStatus.IN_REVIEW)
            self._run_step(ctx, "review")
            self._apply_verdicts(ctx)
            self._review_retry_loop(ctx, sm_task=sm)
            self._run_step(ctx, "compose")
        except StepFailure as e:
            sm.transition(TaskStatus.FAILED)
            self._persist_scene_states(ctx)
            self._store.update_task(task_id, status="FAILED", error=str(e))
            self._store.append_event(task_id, f"任务失败: {e}")
            self._export_bundle(ctx, task_id, "失败审计包")
            return

        sm.transition(TaskStatus.SUCCEEDED)
        self._persist_scene_states(ctx)
        # 状态最后写：读到 SUCCEEDED 的调用方，产物一定已经齐了（对外可见性契约）
        self._store.update_task(task_id, status="SUCCEEDED")
        # 状态落定之后再导出：00_task.json 里的 status / scene_states 必须是终态
        self._export_bundle(ctx, task_id, "交付包")
        self._store.append_event(task_id, "任务完成")
        log.info("[%s] SUCCEEDED", task_id)

    # ---------------- 步骤执行 ----------------

    def _run_step(self, ctx: AgentContext, name: str, attempt: int = 1) -> None:
        agent = self._agents[name]
        key = _ARTIFACT_KEY[name]
        self._store.update_task(ctx.task_id, current_step=name)
        step_id = self._store.start_step(ctx.task_id, name, attempt=attempt)
        result = agent.run(ctx)
        if result.ok:
            ctx.artifacts[key] = result.output
            self._store.merge_artifact(ctx.task_id, key, result.output)
            self._store.finish_step(step_id, "SUCCEEDED", output=result.output)
        else:
            self._store.finish_step(step_id, "FAILED", error=result.error)
            raise StepFailure(f"{name} 步骤失败: {result.error}")

    # ---------------- 镜头级状态机 ----------------

    def _init_scene_machines(self, ctx: AgentContext) -> None:
        storyboard = Storyboard.model_validate(ctx.artifacts["storyboard"])
        self._scene_machines = {
            s.scene_id: StateMachine(SCENE_TRANSITIONS, SceneStatus.PENDING)
            for s in storyboard.scenes
        }

    def _move_scenes(self, scene_ids: list[int], target: SceneStatus) -> None:
        for sid in scene_ids:
            machine = self._scene_machines.get(sid)
            if machine is None:
                continue
            if not machine.can_transition(target):
                # 不再静默吞掉非法流转：调用点已保证按构造合法（见 _rejected_scenes），
                # 走到这里说明编排逻辑本身有 bug，必须在第一现场暴露（对齐架构文档 §2）。
                log.error("镜头 %s 非法流转 %s -> %s（编排逻辑 bug）",
                          sid, machine.state.value, target.value)
                raise IllegalTransitionError(machine.state, target)
            machine.transition(target)

    def _apply_verdicts(self, ctx: AgentContext) -> None:
        report = ReviewReport.model_validate(ctx.artifacts["review_report"])
        for verdict in report.verdicts:
            machine = self._scene_machines.get(verdict.scene_id)
            if machine is None:
                continue
            target = SceneStatus.ACCEPTED if verdict.passed else SceneStatus.REJECTED
            if machine.state == target:
                continue  # 真幂等：返工轮里已定态的镜头被复检报告再次覆盖
            if not machine.can_transition(target):
                # 终态与本轮裁决矛盾。唯一可达路径是 R3：它检查磁盘文件是否存在，
                # 已 ACCEPTED 镜头的关键帧/片段在两轮之间消失即会翻转。
                # 终态是设计意图，不回退；但矛盾必须留痕，不能静默吞掉（F15）。
                msg = (f"镜头 {verdict.scene_id} 已处于终态 {machine.state.value}，"
                       f"但本轮质检判为{'通过' if verdict.passed else '不通过'}"
                       f"（{verdict.reasons}）；状态保持不变，记为不一致")
                self._store.add_warning(ctx.task_id, msg)
                self._store.append_event(ctx.task_id, msg, step="review")
                log.warning("[%s] %s", ctx.task_id, msg)
                continue
            machine.transition(target)

    def _rejected_scenes(self) -> list[int]:
        """从状态机推导待返工镜头，而不是从质检报告推导。

        这是让 _move_scenes 每个调用点"按构造合法"的关键：只有 REJECTED 会进入返工，
        已 ACCEPTED / ACCEPTED_FALLBACK / BLOCKED 的终态镜头不会被再次流转，
        也不会被无谓地重新生成一遍（F15：报告与状态机不一致时的静默错乱）。
        """
        return sorted(sid for sid, m in self._scene_machines.items()
                      if m.state is SceneStatus.REJECTED)

    def _persist_scene_states(self, ctx: AgentContext) -> None:
        states = {str(sid): m.state.value for sid, m in sorted(self._scene_machines.items())}
        ctx.artifacts["scene_states"] = states
        self._store.merge_artifact(ctx.task_id, "scene_states", states)

    # ---------------- 交付门禁（Verdict -> Delivery Gate） ----------------

    @staticmethod
    def _collect_blocked(report: ReviewReport) -> dict[int, list[str]]:
        """挑出"含 BLOCKER 级发现且当前无修复通道"的镜头 -> {scene_id: [rule_id, ...]}"""
        blocked: dict[int, list[str]] = {}
        for v in report.verdicts:
            if v.passed:
                continue
            rules = sorted({
                f.rule_id for f in v.findings
                if f.severity == "BLOCKER" and not has_repair(f.rule_id, f.field)
            })
            if rules:
                blocked[v.scene_id] = rules
        return blocked

    def _apply_blocked(self, ctx: AgentContext, blocked: dict[int, list[str]]) -> None:
        """置 BLOCKED 终态并留痕。

        必须先落状态、再让上层抛 StepFailure——这样 _persist_scene_states 与失败审计包
        （00_task.json / 05_review/review_report.json）里都能看到"哪个镜头被拦、因为哪条规则"。
        合规拦截恰恰是最需要留痕的场景，不能只留一条 DB 事件。
        """
        for sid, rules in blocked.items():
            machine = self._scene_machines.get(sid)
            if machine is not None and machine.can_transition(SceneStatus.BLOCKED):
                machine.transition(SceneStatus.BLOCKED)
            msg = f"镜头 {sid} 触发 BLOCKER 级门禁 {rules}，当前无修复通道，不予交付"
            self._store.add_warning(ctx.task_id, msg)
            self._store.append_event(ctx.task_id, msg, step="review")
            log.warning("[%s] %s", ctx.task_id, msg)
        self._persist_scene_states(ctx)

    # ---------------- 质检返工循环 ----------------

    def _review_retry_loop(self, ctx: AgentContext, sm_task: StateMachine) -> None:
        report = ReviewReport.model_validate(ctx.artifacts["review_report"])

        # 交付门禁：BLOCKER 级发现且当前无修复通道时，
        # ① 不进入返工循环——重跑注定得到逐字相同的裁决，白烧重试额度与生成成本；
        # ② 不允许降级采用——"降级"对美学失败是合理策略，对合规失败是错误策略。
        # 语义：Verdict 必须成为 Delivery Gate，而不是只产生一条意见。
        blocked = self._collect_blocked(report)
        if blocked:
            self._apply_blocked(ctx, blocked)
            detail = "；".join(
                f"镜头 {sid} 触发 {rules}" for sid, rules in sorted(blocked.items())
            )
            raise StepFailure(f"合规门禁未通过，任务不予交付 —— {detail}")

        rejected = self._rejected_scenes()
        attempt = 1
        while rejected and attempt <= self._settings.max_scene_retries:
            attempt += 1
            reasons = {
                str(v.scene_id): v.reasons
                for v in report.verdicts
                if not v.passed and v.scene_id in rejected
            }
            self._store.append_event(
                ctx.task_id,
                f"质检不通过，返工第 {attempt - 1} 次：镜头={rejected} 原因={reasons}",
                step="review",
            )
            ctx.extra = {"scene_ids": rejected, "attempt": attempt, "review_feedback": reasons}
            self._move_scenes(rejected, SceneStatus.GENERATING)  # REJECTED -> GENERATING
            self._run_step(ctx, "generation", attempt=attempt)
            self._move_scenes(rejected, SceneStatus.IN_REVIEW)
            self._run_step(ctx, "review", attempt=attempt)
            self._apply_verdicts(ctx)
            report = ReviewReport.model_validate(ctx.artifacts["review_report"])
            rejected = self._rejected_scenes()

        if rejected:
            # 重试耗尽：降级采用 + 告警（v1 策略：不阻塞整片；生产中可改人工复核）
            self._move_scenes(rejected, SceneStatus.ACCEPTED_FALLBACK)
            for sid in rejected:
                msg = f"镜头 {sid} 重试 {self._settings.max_scene_retries} 次后仍未通过质检，降级采用"
                self._store.add_warning(ctx.task_id, msg)
                self._store.append_event(ctx.task_id, msg, step="review")
            log.warning("[%s] 降级采用镜头: %s", ctx.task_id, rejected)


def all_ids(ctx: AgentContext) -> list[int]:
    storyboard = Storyboard.model_validate(ctx.artifacts["storyboard"])
    return [s.scene_id for s in storyboard.scenes]
