"""API 端到端测试：提交 -> 轮询 -> 完成；404；健康检查。"""
import time


def _wait_until_done(client, task_id: str, timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        view = client.get(f"/api/v1/tasks/{task_id}").json()
        if view["status"] in ("SUCCEEDED", "FAILED"):
            return view
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 在 {timeout_s}s 内未完成")


def test_submit_and_poll_until_success(client):
    resp = client.post("/api/v1/tasks", json={
        "topic": "给这双鞋子做个广告",
        "product_info": "轻便跑鞋，透气网面，缓震中底",
        "scene_count": 3,
    })
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    assert resp.json()["status"] == "PENDING"

    view = _wait_until_done(client, task_id)
    assert view["status"] == "SUCCEEDED"
    assert view["artifacts"]["storyboard"]["scenes"], "应有分镜脚本"
    assert view["artifacts"]["review_report"]["passed_all"] is True
    assert view["scene_states"], "应有镜头级状态"
    assert view["steps"], "应有步骤历史"
    assert view["events"], "应有事件留痕"


def test_unknown_task_404(client):
    resp = client.get("/api/v1/tasks/nonexistent123")
    assert resp.status_code == 404


def test_list_tasks(client):
    client.post("/api/v1/tasks", json={"topic": "给这个水杯做个广告"})
    resp = client.get("/api/v1/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_validation_rejects_empty_topic(client):
    resp = client.post("/api/v1/tasks", json={"topic": ""})
    assert resp.status_code == 422


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mock_llm"] is True
