"""SAP AI Core client: orchestration + direct modes, token caching, 401 re-login,
model fallbacks on the tenant, and the llm-layer provider wiring."""
import httpx
import pytest

from rag import aicore, llm

ENV = {
    "AICORE_AUTH_URL": "https://auth.example",
    "AICORE_CLIENT_ID": "cid",
    "AICORE_CLIENT_SECRET": "csecret",
    "AI_API_URL": "https://api.example",
}
ORCHESTRATION_ID = "d8c092efb8980093"
DIRECT_DEPLOYMENT_ID = "d1a82575de60e02a"


class FakeAICore:
    """httpx.post double for both AI Core modes; never touches the network."""

    def __init__(self, chat_statuses=None):
        self.token_calls = 0
        self.chat_requests = []          # (url, json body, headers)
        self.chat_statuses = list(chat_statuses or [])
        self.reply = " Halo! "
        self.mirror_only = False         # send orchestration_result without module_results

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
        choice = {"message": {"role": "assistant", "content": self.reply}, "finish_reason": "stop"}
        if url.endswith("/completion"):                                  # orchestration
            body = {"orchestration_result": {"choices": [choice]}}
            if not self.mirror_only:
                body["module_results"] = {"llm": {"choices": [choice]}}
            return httpx.Response(200, request=req, json=body)
        return httpx.Response(200, request=req, json={"choices": [choice]})   # direct


@pytest.fixture
def fake(monkeypatch):
    """AI Core configured; orchestration ON (the default)."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    for k in ("AICORE_DEPLOYMENT_ID", "AICORE_RESOURCE_GROUP", "AICORE_ORCHESTRATION_ID",
              "AICORE_MODEL_NAME", "AICORE_MODEL_LABEL", "AICORE_FALLBACK_MODELS"):
        monkeypatch.delenv(k, raising=False)
    aicore.reset_token()
    f = FakeAICore()
    monkeypatch.setattr(aicore.httpx, "post", f.post)
    yield f
    aicore.reset_token()


@pytest.fixture
def fake_direct(fake, monkeypatch):
    """Same fake, direct deployment mode (AICORE_ORCHESTRATION_ID explicitly empty)."""
    monkeypatch.setenv("AICORE_ORCHESTRATION_ID", "")
    yield fake


# ── aicore: orchestration mode (default) ────────────────────────────────────

def test_orchestration_request_shape_and_token_cached(fake):
    assert aicore.ask("hi", system="be brief") == "Halo!"
    assert aicore.ask("again") == "Halo!"
    assert fake.token_calls == 1                      # token reused, not fetched per call
    url, body, headers = fake.chat_requests[0]
    assert url == f"https://api.example/v2/inference/deployments/{ORCHESTRATION_ID}/completion"
    assert headers["Authorization"] == "Bearer tok1"
    assert headers["AI-Resource-Group"] == "default"
    cfg = body["orchestration_config"]["module_configurations"]
    assert cfg["templating_module_config"]["template"] == [
        {"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    llm_cfg = cfg["llm_module_config"]
    assert set(llm_cfg) == {"model_name", "model_params"}
    assert llm_cfg["model_name"] == "gpt-5.4"         # a plain string, never {"name": ...}
    assert isinstance(llm_cfg["model_name"], str)
    assert llm_cfg["model_params"] == {}


def test_orchestration_id_defaults_to_tenant_deployment(fake):
    assert aicore.orchestration_id() == ORCHESTRATION_ID


def test_per_call_model_override(fake):
    assert aicore.ask("q", model="anthropic--claude-4.5-sonnet") == "Halo!"
    _, body, _ = fake.chat_requests[-1]
    llm_cfg = body["orchestration_config"]["module_configurations"]["llm_module_config"]
    assert llm_cfg["model_name"] == "anthropic--claude-4.5-sonnet"


def test_orchestration_reply_tolerates_mirror_only_body(fake):
    fake.mirror_only = True               # orchestration_result mirrors module_results
    assert aicore.ask("hi") == "Halo!"


def test_orchestration_empty_reply_raises(fake):
    fake.reply = ""
    with pytest.raises(aicore.AICoreError) as e:
        aicore.ask("hi")
    assert "empty reply" in str(e.value)


def test_model_label_reflects_orchestration_model(fake, monkeypatch):
    assert aicore.model_label() == "gpt-5.4"
    monkeypatch.setenv("AICORE_MODEL_NAME", "anthropic--claude-4.5-sonnet")
    assert aicore.model_label() == "anthropic--claude-4.5-sonnet"
    monkeypatch.setenv("AICORE_MODEL_LABEL", "label override")
    assert aicore.model_label() == "label override"


# ── aicore: direct mode (orchestration explicitly disabled) ─────────────────

def test_direct_mode_uses_deployment_url(fake_direct):
    assert aicore.orchestration_id() == ""            # explicitly disabled
    assert aicore.ask("hi", system="be brief") == "Halo!"
    url, body, headers = fake_direct.chat_requests[0]
    assert url == (f"https://api.example/v2/inference/deployments/{DIRECT_DEPLOYMENT_ID}"
                   "/chat/completions?api-version=2024-12-01-preview")
    assert headers["Authorization"] == "Bearer tok1"
    assert headers["AI-Resource-Group"] == "default"
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert "max_completion_tokens" in body and "model" not in body
    assert "orchestration_config" not in body


def test_model_label_direct_mode(fake_direct):
    assert aicore.model_label() == "gpt-5.4"          # same label as before orchestration


def test_direct_mode_has_no_fallback_models(fake_direct, monkeypatch):
    """Orchestration off: only the direct gpt-5.4 deployment exists, so no model fallbacks."""
    monkeypatch.setenv("AICORE_FALLBACK_MODELS", "anthropic--claude-4.5-sonnet")
    assert aicore.fallback_models() == []
    assert aicore.models_in_order() == ["gpt-5.4"]


# ── aicore: token handling (both modes share the machinery) ─────────────────

@pytest.mark.parametrize("mode", ["orchestration", "direct"])
def test_relogin_once_on_401(request, mode):
    fake = request.getfixturevalue("fake_direct" if mode == "direct" else "fake")
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


# ── aicore: model fallbacks on the same tenant ──────────────────────────────

def _model_of(request_body):
    return request_body["orchestration_config"]["module_configurations"]["llm_module_config"]["model_name"]


def test_default_fallback_is_claude_sonnet(fake):
    assert aicore.models_in_order() == ["gpt-5.4", "anthropic--claude-4.5-sonnet"]


def test_fallback_models_env(fake, monkeypatch):
    monkeypatch.setenv("AICORE_FALLBACK_MODELS", " amazon--nova-pro , gpt-5.4, ")
    assert aicore.models_in_order() == ["gpt-5.4", "amazon--nova-pro"]     # no duplicates
    assert aicore.models_in_order("anthropic--claude-4.7-opus")[0] == "anthropic--claude-4.7-opus"
    monkeypatch.setenv("AICORE_FALLBACK_MODELS", "")
    assert aicore.models_in_order() == ["gpt-5.4"]


def test_ask_any_falls_back_to_next_model(fake):
    fake.chat_statuses = [400]                       # gpt-5.4: "model not found"
    text, used = aicore.ask_any("hi")
    assert (text, used) == ("Halo!", "anthropic--claude-4.5-sonnet")
    assert [_model_of(b) for _, b, _ in fake.chat_requests] == ["gpt-5.4", "anthropic--claude-4.5-sonnet"]


def test_ask_any_raises_when_every_model_fails(fake):
    fake.chat_statuses = [500, 500]
    with pytest.raises(aicore.AICoreError) as e:
        aicore.ask_any("hi")
    assert "gpt-5.4" in str(e.value) and "anthropic--claude-4.5-sonnet" in str(e.value)


def test_ask_any_stops_after_login_failure(fake, monkeypatch):
    def post(url, **kw):
        raise httpx.ConnectError("Name or service not known")
    monkeypatch.setattr(aicore.httpx, "post", post)
    with pytest.raises(aicore.AICoreError) as e:
        aicore.ask_any("hi")
    assert "login" in str(e.value) and "anthropic--claude-4.5-sonnet" not in str(e.value)


# ── llm layer: provider wiring ───────────────────────────────────────────────

def test_llm_auto_prefers_aicore(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert llm.available_providers() == ["aicore", "claude"]
    res, errors = llm.complete("sys", "user", json_mode=False, provider="auto")
    assert res.ok and res.provider == "aicore" and res.model == "gpt-5.4"
    assert res.text == "Halo!" and not errors
    url, _, _ = fake.chat_requests[-1]
    assert url == f"https://api.example/v2/inference/deployments/{ORCHESTRATION_ID}/completion"


def test_llm_reports_the_fallback_model_that_answered(fake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake.chat_statuses = [503]
    res, errors = llm.complete("sys", "user", json_mode=False)
    assert res.ok and res.provider == "aicore" and res.model == "anthropic--claude-4.5-sonnet"
    assert not errors


def test_no_gemini_provider_left(fake, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")        # ignored now
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.available_providers() == ["aicore"]
    assert not hasattr(llm, "GEMINI_URL")


def test_orchestration_json_mode_is_prompt_steered_and_parses(fake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake.reply = '```json\n{"claims": [{"text": "x", "sources": ["S1"]}]}\n```'
    res, errors = llm.complete("sys", "user", json_mode=True)
    assert res.ok and res.data == {"claims": [{"text": "x", "sources": ["S1"]}]} and not errors
    _, body, _ = fake.chat_requests[-1]
    template = body["orchestration_config"]["module_configurations"]["templating_module_config"]["template"]
    assert template[-1]["role"] == "user" and llm.JSON_STEER.strip() in template[-1]["content"]


class FakeAnthropicHTTP:
    """httpx.post double for the public Anthropic REST API."""

    def __init__(self):
        self.requests = []                # (url, json body, headers)

    def post(self, url, *, json=None, headers=None, timeout=None):
        self.requests.append((url, json, headers))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "content": [{"type": "text", "text": "Hello from Claude"}]})


def test_claude_api_used_when_aicore_not_configured(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    aicore.reset_token()
    f = FakeAnthropicHTTP()
    monkeypatch.setattr(llm.httpx, "post", f.post)
    assert llm.available_providers() == ["claude"]
    res, errors = llm.complete("sys", "user", json_mode=False)
    assert res.ok and res.provider == "claude" and res.text == "Hello from Claude" and not errors
    url, payload, headers = f.requests[-1]
    assert url == llm.ANTHROPIC_URL
    assert payload["model"] == "claude-haiku-4-5"
    assert headers["x-api-key"] == "a"
