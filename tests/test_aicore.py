"""SAP AI Core client: token caching, request shape, re-login on 401, provider wiring."""
import httpx
import pytest

from rag import aicore, llm

ENV = {
    "AICORE_AUTH_URL": "https://auth.example",
    "AICORE_CLIENT_ID": "cid",
    "AICORE_CLIENT_SECRET": "csecret",
    "AI_API_URL": "https://api.example",
}


class FakeAICore:
    def __init__(self, chat_statuses=None):
        self.token_calls = 0
        self.chat_requests = []
        self.chat_statuses = list(chat_statuses or [])

    def post(self, url, *, data=None, json=None, headers=None, auth=None, timeout=None):
        req = httpx.Request("POST", url)
        if url.endswith("/oauth/token"):
            assert auth == ("cid", "csecret") and data == {"grant_type": "client_credentials"}
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": f"tok{self.token_calls}",
                                             "expires_in": 43199}, request=req)
        self.chat_requests.append((url, json, headers))
        code = self.chat_statuses.pop(0) if self.chat_statuses else 200
        if code != 200:
            return httpx.Response(code, text="nope", request=req)
        return httpx.Response(200, request=req, json={
            "choices": [{"message": {"role": "assistant", "content": " Halo! "},
                         "finish_reason": "stop"}]})


@pytest.fixture
def fake(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AICORE_DEPLOYMENT_ID", raising=False)
    monkeypatch.delenv("AICORE_RESOURCE_GROUP", raising=False)
    aicore.reset_token()
    f = FakeAICore()
    monkeypatch.setattr(aicore.httpx, "post", f.post)
    yield f
    aicore.reset_token()


def test_chat_request_shape_and_token_cached(fake):
    assert aicore.ask("hi", system="be brief") == "Halo!"
    assert aicore.ask("again") == "Halo!"
    assert fake.token_calls == 1                      # token reused, not fetched per call
    url, body, headers = fake.chat_requests[0]
    assert url == ("https://api.example/v2/inference/deployments/d1a82575de60e02a"
                   "/chat/completions?api-version=2024-12-01-preview")
    assert headers["Authorization"] == "Bearer tok1"
    assert headers["AI-Resource-Group"] == "default"
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert "max_completion_tokens" in body and "model" not in body


def test_relogin_once_on_401(fake):
    fake.chat_statuses = [401]
    assert aicore.ask("hi") == "Halo!"
    assert fake.token_calls == 2


def test_errors_never_contain_secret(fake):
    fake.chat_statuses = [500, 500]
    with pytest.raises(aicore.AICoreError) as e:
        aicore.ask("hi")
    assert "csecret" not in str(e.value) and "tok1" not in str(e.value)


def test_not_configured(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    aicore.reset_token()
    assert not aicore.configured()
    with pytest.raises(aicore.AICoreError):
        aicore.get_token()


def test_llm_auto_prefers_aicore(fake, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert llm.available_providers()[0] == "aicore"
    res, errors = llm.complete("sys", "user", json_mode=False, provider="auto")
    assert res.ok and res.provider == "aicore" and res.text == "Halo!" and not errors
