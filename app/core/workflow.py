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
from app.core.repair_policy import has_repair, repair_action
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
        # 一个数据库只由一个进程、一个 Worker 使用；不是跨进程任务抢占协议。
        self._lifecycle_lock = threading.Lock()
        self._run_lock = threading.RLock()
        self._stop_requested = threading.Event()

    # ---------------- 生命周期（FastAPI lifespan 调用） ----------------

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
        with self._run_lock, self._lifecycle_lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_requested.clear()
            # 数据库是待执行任务的来源，重建队列同时丢弃旧哨兵/重复提交。
            self._queue = queue.Queue()
            for rec in self._store.list_unfinished_tasks():
                if rec.status == "RUNNING":
                    self._fail_task_safe(rec.id, "进程中断，未完成任务不自动重跑外部生成")
                else:
                    self._queue.put(rec.id)
            self._worker_thread = threading.Thread(
                target=self._worker_loop, name="workflow-worker", daemon=True
            )
            self._worker_thread.start()
        log.info("Workflow Worker 已启动")

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_requested.set()
            worker = self._worker_thread
            self._queue.put(None)  # 唤醒空闲 Worker；不排空积压任务。
        if worker:
            worker.join()  # 当前任务完整收尾后，调用方才能关闭 Store。
        log.info("Workflow Worker 已停止")

    def submit(self, task_id: str) -> None:
        """API 层提交任务入队（提交即返回，不阻塞请求）。"""
        with self._lifecycle_lock:
            self._queue.put(task_id)

    def _worker_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if task_id is None:
                break
            with self._run_lock:
                with self._lifecycle_lock:
                    if self._stop_requested.is_set():
                        break
                try:
                    self.run_task(task_id)
                except Exception as e:  # noqa: BLE001 最后防线
                    log.exception("Worker 未预期异常 task=%s", task_id)
                    self._fail_task_safe(task_id, f"Worker 异常: {e}")

    def _fail_task_safe(self, task_id: str, error: str, ctx: AgentContext | None = None) -> None:
        """失败收尾和审计均尽力执行；磁盘/数据库故障不得再次炸穿 Worker。"""
        try:
            rec = self._store.get_task(task_id)
            if rec is None or rec.status in ("SUCCEEDED", "FAILED"):
                return
            sm = StateMachine(TASK_TRANSITIONS, TaskStatus(rec.status))
            sm.transition(TaskStatus.FAILED)
        except Exception:
            log.exception("无法读取失败任务 task=%s", task_id)
            return
        actions = [lambda: self._store.fail_running_steps(task_id, error)]
        if ctx is not None:
            actions.append(lambda: self._persist_scene_states(ctx))
        actions.extend([
            lambda: self._store.update_task(task_id, status="FAILED", error=error),
            lambda: self._store.append_event(task_id, f"任务失败: {error}"),
        ])
        for action in actions:
            try:
                action()
            except Exception:
                log.exception("失败任务收尾写入失败 task=%s", task_id)
        try:
            self._export_bundle(ctx, task_id, "失败审计包", status="FAILED", error=error)
        except Exception as export_error:
            message = f"失败审计包导出失败: {export_error}"
            log.exception("[%s] %s", task_id, message)
            try:
                self._store.add_warning(task_id, message)
            except Exception:
                log.exception("无法记录导出告警 task=%s", task_id)

    def _export_bundle(self, ctx: AgentContext | None, task_id: str, note: str,
                       *, status: str, error: str | None = None) -> None:
        """导出规范化产物目录。成功与失败路径都必须调用。

        失败任务同样需要审计留痕——尤其合规门禁拦截的场景，
        00_task.json 与 05_review/review_report.json 正是事后追责要看的东西。

        导出终态副本；成功交付时数据库仍为 RUNNING，全部导出后才发布成功。
        """
        rec = self._store.get_task(task_id)
        if rec is None:
            return
        rec = rec.model_copy(update={"status": status, "error": error})
        bundle = export_task_bundle(rec, self._settings.output_dir / task_id)
        self._store.merge_artifact(task_id, "export_dir", str(bundle))
        if ctx is not None:
            ctx.artifacts["export_dir"] = str(bundle)
        self._store.append_event(task_id, f"规范化产物已导出: {bundle}（{note}）")

    # ---------------- 核心：跑一个任务 ----------------

    def run_task(self, task_id: str) -> None:
        # 直接调用与 Worker 共用串行边界，避免镜头状态串任务或重复执行。
        with self._run_lock:
            self._run_task(task_id)

    def _run_task(self, task_id: str) -> None:
        rec = self._store.get_task(task_id)
        if rec is None:
            raise ValueError(f"任务不存在: {task_id}")
        # 每任务重置镜头状态机，防止上一个任务的残留状态串进来
        self._scene_machines = {}

        sm = StateMachine(TASK_TRANSITIONS, TaskStatus(rec.status))
        if sm.state is not TaskStatus.PENDING:
            log.info("[%s] 重复调度，跳过（幂等保护）", task_id)
            return
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
            sm.transition(TaskStatus.RUNNING)
            self._store.update_task(task_id, status="RUNNING", error="")
            self._store.append_event(task_id, "任务开始")
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
            sm.transition(TaskStatus.SUCCEEDED)
            self._persist_scene_states(ctx)
            self._export_bundle(ctx, task_id, "交付包", status="SUCCEEDED")
            self._store.append_event(task_id, "任务完成")
            # 最后发布成功：调用方读到 SUCCEEDED 时导出、索引和事件均已持久化。
            self._store.update_task(task_id, status="SUCCEEDED")
        except Exception as e:
            log.exception("[%s] 任务失败", task_id)
            self._fail_task_safe(task_id, f"{type(e).__name__}: {e}", ctx)
            return
        log.info("[%s] SUCCEEDED", task_id)

    # ---------------- 步骤执行 ----------------

    def _run_step(self, ctx: AgentContext, name: str, attempt: int = 1) -> None:
        agent = self._agents[name]
        key = _ARTIFACT_KEY[name]
        self._store.update_task(ctx.task_id, current_step=name)
        step_id = self._store.start_step(ctx.task_id, name, attempt=attempt)
        result = agent.run(ctx)
        if result.ok:
            if name == "prompt" and ctx.extra.get("scene_ids") is not None:
                # 编排边界也保护未指定的镜头，包含自定义 Agent 返回全片的情况。
                selected = set(ctx.extra["scene_ids"])
                previous = ctx.artifacts.get(key, {}).get("prompts", [])
                merged = [p for p in previous if p["scene_id"] not in selected]
                merged.extend(p for p in result.output["prompts"] if p["scene_id"] in selected)
                result.output = {**result.output, "prompts": sorted(merged, key=lambda p: p["scene_id"])}
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
                # 终态不回退；记录矛盾后由每轮交付门禁阻止继续合成。
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

    def _enforce_review_gate(self, ctx: AgentContext, report: ReviewReport) -> None:
        """每轮检查最新报告；镜头终态不会让新阻断失效。"""
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

        inconsistent = [v.scene_id for v in report.verdicts
                        if not v.passed and v.scene_id in self._scene_machines
                        and self._scene_machines[v.scene_id].state in (
                            SceneStatus.ACCEPTED, SceneStatus.ACCEPTED_FALLBACK)]
        if inconsistent:
            raise StepFailure(f"质检与已接受镜头状态不一致，任务不予交付：{inconsistent}")

    # ---------------- 质检返工循环 ----------------

    def _review_retry_loop(self, ctx: AgentContext, sm_task: StateMachine) -> None:
        report = ReviewReport.model_validate(ctx.artifacts["review_report"])
        self._enforce_review_gate(ctx, report)

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
            prompt_ids = sorted({
                v.scene_id for v in report.verdicts if v.scene_id in rejected
                for finding in v.findings
                if finding.owner == "prompt" and finding.severity == "MAJOR"
                and repair_action(finding.rule_id, finding.field) == "regenerate_prompt"
            })
            if prompt_ids:
                ctx.extra = {"scene_ids": prompt_ids, "attempt": attempt,
                             "review_feedback": {str(sid): reasons[str(sid)] for sid in prompt_ids}}
                self._run_step(ctx, "prompt", attempt=attempt)
            ctx.extra = {"scene_ids": rejected, "attempt": attempt, "review_feedback": reasons}
            self._move_scenes(rejected, SceneStatus.GENERATING)  # REJECTED -> GENERATING
            self._run_step(ctx, "generation", attempt=attempt)
            self._move_scenes(rejected, SceneStatus.IN_REVIEW)
            self._run_step(ctx, "review", attempt=attempt)
            self._apply_verdicts(ctx)
            report = ReviewReport.model_validate(ctx.artifacts["review_report"])
            self._enforce_review_gate(ctx, report)
            rejected = self._rejected_scenes()

        if rejected:
            missing = [v.scene_id for v in report.verdicts if v.scene_id in rejected
                       and any(f.rule_id == "R3" for f in v.findings)]
            if missing:
                raise StepFailure(f"素材缺失且返工额度耗尽，任务不予交付：{missing}")
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
