"""礼貌客户端重试测试：429 与读阶段断连（审查发现的逃逸路径）。"""
import io
import urllib.error

import pytest

from app.tools.providers.pollinations_provider import PollinationsProvider


def test_polite_get_retries_on_read_level_oserror(tmp_path, monkeypatch):
    provider = PollinationsProvider(tmp_path)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("connection reset")  # 读阶段断连（OSError）
        return io.BytesIO(b"x" * 2048)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.tools.providers.pollinations_provider.time.sleep", lambda s: None)
    data = provider._polite_get("https://example.invalid/x")
    assert len(data) == 2048
    assert calls["n"] == 2


def test_polite_get_gives_up_after_max_attempts(tmp_path, monkeypatch):
    provider = PollinationsProvider(tmp_path)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("u", 429, "rate limited", None, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.tools.providers.pollinations_provider.time.sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        provider._polite_get("https://example.invalid/x")
