"""Deterministic lifecycle regressions; no rendering or external providers."""
import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents.base import AgentResult
from app.core import exporter, workflow
from app.core.task_store import TaskStore
from app.core.workflow import WorkflowEngine
from app.main import build_app, build_components
from tests.conftest import create_task


def fast_engine(settings):
    outputs = {
        "script": {"topic": "test", "scenes": [{"scene_id": 1, "title": "t", "narration": "n", "visual": "v", "duration_s": 2}]},
        "prompt": {"prompts": []},
        "generation": {"clips": []},
        "review": {"passed_all": True, "verdicts": [{"scene_id": 1, "passed": True}]},
        "compose": {"final_video_path": "controlled.mp4"},
    }
    agents = {name: SimpleNamespace(run=lambda ctx, name=name: AgentResult(agent=name, output=outputs[name])) for name in outputs}
    store = TaskStore(settings.db_path)
    return store, WorkflowEngine(store, settings, agents)


def meta(store, task_id):
    return json.loads((Path(store.get_task(task_id).artifacts["export_dir"]) / "00_task.json").read_text(encoding="utf-8"))


def test_success_published_only_after_export_and_events(tmp_settings, monkeypatch):
    store, engine = fast_engine(tmp_settings)
    tid = create_task(store)
    observations = []
    real_export = workflow.export_task_bundle
    def inspect(rec, directory):
        observations.append((store.get_task(tid).status, rec.status))
        return real_export(rec, directory)
    monkeypatch.setattr(workflow, "export_task_bundle", inspect)
    real_update = store.update_task
    def update(task_id, **fields):
        if fields.get("status") == "SUCCEEDED":
            rec = store.get_task(task_id)
            assert Path(rec.artifacts["export_dir"]).is_dir()
            assert store.list_events(task_id)[-1]["message"] == "任务完成"
        real_update(task_id, **fields)
    monkeypatch.setattr(store, "update_task", update)
    engine.run_task(tid)
    assert observations == [("RUNNING", "SUCCEEDED")]
    assert store.get_task(tid).status == "SUCCEEDED"
    assert meta(store, tid)["status"] == "SUCCEEDED"
    steps = store.list_steps(tid)
    engine.run_task(tid)
    assert store.list_steps(tid) == steps
    store.close()


def test_export_failure_marks_failed_and_worker_continues(tmp_settings, monkeypatch):
    store, engine = fast_engine(tmp_settings)
    first = create_task(store, "first")
    second = create_task(store, "second")
    real_export = workflow.export_task_bundle
    done = threading.Event()
    attempts = []
    def export(rec, directory):
        if rec.id == first:
            attempts.append(rec.status)
            raise OSError("disk unavailable")
        return real_export(rec, directory)
    monkeypatch.setattr(workflow, "export_task_bundle", export)
    real_run = engine.run_task
    def run(tid):
        real_run(tid)
        if tid == second:
            done.set()
    monkeypatch.setattr(engine, "run_task", run)
    engine.submit(first)
    engine.submit(second)
    engine.start()
    try:
        assert done.wait(3), "Worker died after export error"
    finally:
        engine.stop()
    rec = store.get_task(first)
    assert rec.status == "FAILED"
    assert "disk unavailable" in rec.error
    assert rec.artifacts["scene_states"] == {"1": "ACCEPTED"}
    assert any("disk unavailable" in warning for warning in rec.warnings)
    assert attempts == ["SUCCEEDED", "FAILED"]
    assert store.get_task(second).status == "SUCCEEDED"
    store.close()


@pytest.mark.parametrize("failure", ["agent", "persistence", "validation"])
def test_unexpected_errors_close_steps_and_export_audit(tmp_settings, monkeypatch, failure):
    store, engine = fast_engine(tmp_settings)
    tid = create_task(store)
    if failure == "agent":
        def boom(ctx):
            raise RuntimeError("unexpected agent failure")
        engine._agents["generation"].run = boom
    elif failure == "persistence":
        original = store.merge_artifact
        def merge(task_id, key, value):
            if key == "generation":
                raise RuntimeError("artifact persistence failure")
            original(task_id, key, value)
        monkeypatch.setattr(store, "merge_artifact", merge)
    else:
        engine._agents["review"].run = lambda ctx: AgentResult(agent="review", output={"invalid": True})
    engine.run_task(tid)
    rec = store.get_task(tid)
    assert rec.status == "FAILED"
    assert rec.error
    assert all(step["status"] != "RUNNING" for step in store.list_steps(tid))
    audit = meta(store, tid)
    assert audit["status"] == "FAILED"
    assert audit["error"] == rec.error
    assert audit["scene_states"] == {"1": "GENERATING" if failure != "validation" else "IN_REVIEW"}
    before = rec.model_dump()
    engine.run_task(tid)
    assert store.get_task(tid).model_dump() == before
    store.close()


def test_exporter_writes_terminal_metadata_last(tmp_settings, monkeypatch):
    store, _ = fast_engine(tmp_settings)
    tid = create_task(store)
    store.merge_artifact(tid, "storyboard", {"scenes": []})
    store.update_task(tid, status="FAILED", error="audit reason")
    writes = []
    real_write = exporter._write_json
    def write(path, obj):
        writes.append(path.name)
        real_write(path, obj)
    monkeypatch.setattr(exporter, "_write_json", write)
    directory = exporter.export_task_bundle(store.get_task(tid), tmp_settings.output_dir / tid)
    assert writes[-1] == "00_task.json"
    assert json.loads((directory / "00_task.json").read_text(encoding="utf-8"))["error"] == "audit reason"
    store.close()


def test_start_recovers_pending_and_audits_interrupted(tmp_settings, monkeypatch):
    store, engine = fast_engine(tmp_settings)
    interrupted = create_task(store, "interrupted")
    store.update_task(interrupted, status="RUNNING")
    store.merge_artifact(interrupted, "scene_states", {"1": "GENERATING"})
    store.start_step(interrupted, "generation")
    pending = create_task(store, "pending")
    done = threading.Event()
    called = []
    real_run = engine.run_task
    def run(tid):
        called.append(tid)
        real_run(tid)
        if tid == pending:
            done.set()
    monkeypatch.setattr(engine, "run_task", run)
    engine.start()
    try:
        assert done.wait(3), "Persisted pending task was never queued"
    finally:
        engine.stop()
    assert called == [pending]
    assert store.get_task(interrupted).status == "FAILED"
    assert store.list_steps(interrupted)[0]["status"] == "FAILED"
    audit = meta(store, interrupted)
    assert audit["error"]
    assert audit["scene_states"] == {"1": "GENERATING"}
    store.close()


def test_stop_waits_current_task_leaves_backlog_and_start_is_idempotent(tmp_settings, monkeypatch):
    store, engine = fast_engine(tmp_settings)
    first = create_task(store, "active")
    second = create_task(store, "backlog")
    entered, release, stopping = threading.Event(), threading.Event(), threading.Event()
    original = engine._agents["compose"].run
    def compose(ctx):
        if ctx.task_id == first:
            entered.set()
            assert release.wait(3)
        return original(ctx)
    engine._agents["compose"].run = compose
    engine.submit(first)
    engine.submit(second)
    engine.start()
    stopper = None
    try:
        assert entered.wait(3)
        worker = engine._worker_thread
        engine.start()
        assert engine._worker_thread is worker
        original_join = worker.join
        def join(timeout=None):
            stopping.set()
            assert timeout is None, "Shutdown must await current task without a deadline"
            original_join(timeout)
        monkeypatch.setattr(worker, "join", join)
        stopper = threading.Thread(target=engine.stop)
        stopper.start()
        assert stopping.wait(3)
        assert stopper.is_alive()
        assert store.get_task(second).status == "PENDING"
    finally:
        release.set()
        if stopper:
            stopper.join(3)
        engine.stop()
    assert store.get_task(first).status == "SUCCEEDED"
    assert store.get_task(second).status == "PENDING"
    assert store.list_steps(second) == []
    resumed = threading.Event()
    def resumed_compose(ctx):
        resumed.set()
        return original(ctx)
    engine._agents["compose"].run = resumed_compose
    engine.start()
    try:
        assert resumed.wait(3), "Restart did not recover the stopped backlog"
    finally:
        engine.stop()
    assert store.get_task(second).status == "SUCCEEDED"
    assert len(store.list_steps(second)) == 5
    assert len(store.list_steps(first)) == 5
    store.close()


@pytest.mark.parametrize("provider", ["invalid-provider", "jimeng"])
def test_provider_fallback_updates_actual_settings_and_health(tmp_settings, provider):
    settings = tmp_settings.with_overrides(gen_provider=provider)
    actual, store, engine = build_components(settings)
    try:
        assert actual.gen_provider == "mock"
        assert engine._settings.gen_provider == "mock"
    finally:
        store.close()
    with TestClient(build_app(settings)) as client:
        assert client.get("/healthz").json()["gen_provider"] == "mock"


def test_lifespan_exception_stops_worker_before_closing_store(tmp_settings, monkeypatch):
    import app.main as main
    calls = []
    engine = SimpleNamespace(start=lambda: calls.append("start"), stop=lambda: calls.append("stop"))
    store = SimpleNamespace(close=lambda: calls.append("close"))
    monkeypatch.setattr(main, "build_components", lambda settings: (settings, store, engine))
    app = main.build_app(tmp_settings)
    async def exercise():
        with pytest.raises(RuntimeError, match="handler failed"):
            async with app.router.lifespan_context(app):
                raise RuntimeError("handler failed")
    asyncio.run(exercise())
    assert calls == ["start", "stop", "close"]
