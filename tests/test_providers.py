"""Models and keys (product plan 4.1): the owner's connections, the review on them, and the first lab
left exactly as it was. No real service is called: Anthropic's client is replaced and the
OpenAI-compatible service is a server on this machine."""

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from octo import claude_code as CC
from octo import labconfig as LC
from octo import pipeline as P
from octo import planning as PL
from octo import providers as PV
from octo import review_any as RA
from octo import settings as ST
from octo import stages
from octo.db import connect
from octo.pipeline import input_hash
from octo.stages import STAGES, params_for

from tests.test_review_any import ANSWER, DZ, NAMES, PARAMS, RUBRIC


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "_hf_cached", lambda repo: True)
    monkeypatch.setattr(ST, "_ollama_models", lambda: {"qwen3:8b", "gemma3:12b"})
    return connect(tmp_path / "t.db")


@pytest.fixture
def new_lab(tmp_path, monkeypatch):
    """A buyer's lab: no reviews before the product, so nothing chosen for it, Claude Code included (P5)."""
    monkeypatch.delitem(LC.TEST_OVERRIDES, "claude_code")
    monkeypatch.setattr(ST, "_hf_cached", lambda repo: True)
    monkeypatch.setattr(ST, "_ollama_models", lambda: set())
    return connect(tmp_path / "new.db")


# ---- the connections file ----------------------------------------------------------------------

def test_keys_stay_in_a_private_file_and_never_reach_the_page():
    added = PV.add("anthropic", api_key="sk-ant-api03-secret-1234")
    assert added["key"] == "…1234" and "api_key" not in added
    assert all("api_key" not in c and "secret" not in json.dumps(c) for c in PV.listing())
    assert stat.S_IMODE(os.stat(PV.CONFIG).st_mode) == 0o600
    assert PV.load()["connections"][0]["api_key"] == "sk-ant-api03-secret-1234"


def test_what_each_kind_needs():
    with pytest.raises(PV.ProviderError, match="API key"):
        PV.add("anthropic", api_key=" ")
    with pytest.raises(PV.ProviderError, match="model"):
        PV.add("openai", base_url="https://openrouter.ai/api/v1", api_key="k")
    with pytest.raises(PV.ProviderError, match="https"):
        PV.add("openai", base_url="http://example.com/v1", models=["m"])  # a transcript in clear over the internet
    local = PV.add("openai", label="Ollama", base_url="http://127.0.0.1:11434/v1/", models=["qwen3:8b"])
    assert local["base_url"] == "http://127.0.0.1:11434/v1" and local["key"] is None
    with pytest.raises(PV.ProviderError):
        PV.add("gemini-cloud", api_key="k")


def test_two_services_with_one_name_get_their_own_ids():
    a = PV.add("openai", label="OpenRouter", base_url="https://openrouter.ai/api/v1", api_key="k1", models=["a"])
    b = PV.add("openai", label="OpenRouter", base_url="https://openrouter.ai/api/v1", api_key="k2", models=["b"])
    assert a["id"] == "openrouter" and b["id"] == "openrouter-2"
    PV.update("openrouter", models=["a", "c"])
    assert PV.connection("openrouter")["models"] == ["a", "c"]
    PV.remove("openrouter")
    assert [c["id"] for c in PV.listing()] == ["openrouter-2"]


def test_strict_schema_closes_every_object_without_changing_the_original():
    s = PV.strict(RA.SCHEMA)
    assert s["additionalProperties"] is False
    quote = s["properties"]["reading"]["items"]["properties"]["quotes"]["items"]
    assert quote["additionalProperties"] is False and quote["required"] == ["line", "text"]
    assert "additionalProperties" not in RA.SCHEMA


# ---- the first lab: nothing changes ----------------------------------------------------------------

def test_jays_lab_keeps_claude_code_and_its_exact_parameters(conn):
    """The review's parameters are what they were, so no review of his re-runs."""
    stage = STAGES["insight"]
    assert params_for(stage, ST.overrides(conn, "insight")) == params_for(stage)
    assert ST.reader(conn) == {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}
    assert ST.review_blocked(conn) is None
    PV.add("anthropic", api_key="sk-ant-x-9999")  # connecting a key changes nothing until he chooses it
    assert params_for(stage, ST.overrides(conn, "insight")) == params_for(stage)


def test_opening_claude_code_to_every_lab_changes_no_input_hash_in_jays(conn, monkeypatch):
    """31 Oct: any lab may choose Claude Code. In his lab, chosen or not, the review's and commitments' params
    and input hashes are what they were before, so nothing of his re-runs; the owner's profile stays out of them."""
    monkeypatch.setattr(CC, "claude_bin", lambda: "/usr/local/bin/claude")
    for name in ("insight", "commitments"):
        stage = STAGES[name]
        before = input_hash(stage, params_for(stage), "s1", ["t1"])
        assert input_hash(stage, params_for(stage, ST.overrides(conn, name)), "s1", ["t1"]) == before
        ST.choose(conn, name, "claude-opus-5-5")  # as if he'd picked it on the Pipeline page
        assert "owner" not in ST.overrides(conn, name)
        assert input_hash(stage, params_for(stage, ST.overrides(conn, name)), "s1", ["t1"]) == before
    assert ST.reader(conn) == {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}


def test_a_lab_with_reviews_before_the_product_is_the_first_labs(tmp_path, monkeypatch):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "claude_code")
    c = connect(tmp_path / "old.db")
    c.execute("DELETE FROM app_config")  # as it was before 7 Oct
    c.execute("PRAGMA user_version = 0")  # a database from before SCHEMA_VERSION (8 Oct)
    c.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) "
              "VALUES ('r1', 'insight', 's1', 'h', 'done', 0)")
    c.close()
    assert PV.claude_code_default(connect(tmp_path / "old.db"))


def test_a_new_lab_never_turns_into_jays_once_it_has_reviews(tmp_path, monkeypatch):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "claude_code")
    c = connect(tmp_path / "new.db")
    assert not PV.claude_code_default(c)
    c.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) "
              "VALUES ('r1', 'insight', 's1', 'h', 'done', 0)")
    c.close()
    assert not PV.claude_code_default(connect(tmp_path / "new.db"))  # decided once, when the lab was new


# ---- a new lab ---------------------------------------------------------------------------------

def test_a_new_lab_offers_claude_code_but_waits_for_its_owner_to_choose(new_lab, monkeypatch):
    monkeypatch.setattr(PL, "settled_speakers", lambda conn, sid: [{"speaker": "A", "name": "Asha", "self": True}])
    step = ST.BY_STAGE["insight"]
    assert any(c.claude for c in ST.choices(new_lab, step))  # offered (31 Oct), never chosen for them
    assert ST.selected(new_lab, step) is None and ST.reader(new_lab) is None
    assert ST.selected(new_lab, ST.BY_STAGE["commitments"]) is None
    assert "No model chosen" in ST.review_blocked(new_lab)
    assert not PL.insight_ready(new_lab, "s1")
    with pytest.raises(ValueError, match="isn't installed"):  # no `claude` on this Mac (conftest)
        ST.choose(new_lab, "insight", "claude-opus-5-5")
    page = next(s for s in ST.describe(new_lab) if s["stage"] == "insight")
    cc = next(c for c in page["choices"] if c["id"] == "claude-opus-5-5")
    assert page["blocked"] and "Claude Code on this Mac" in cc["label"] and "isn't installed" in cc["unavailable"]


def test_a_new_lab_that_chooses_claude_code_gets_what_jays_has(new_lab, monkeypatch):
    """The 60 a day and the usage-limit wait follow the run's model (pipeline.which), so they come with it; the
    review carries the owner's own profile, and the Hindi merge, commitments and the coach read through it too."""
    monkeypatch.setattr(CC, "claude_bin", lambda: "/usr/local/bin/claude")
    new_lab.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('owner', ?, 0)",
                    (json.dumps({"name": "Asha", "pronoun": "she"}),))
    ST.choose(new_lab, "insight", "claude-opus-5-5")
    assert ST.reader(new_lab) == {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}
    assert ST.review_blocked(new_lab) is None
    params = params_for(STAGES["insight"], ST.overrides(new_lab, "insight"))
    assert params["owner"]["name"] == "Asha" and "reader" not in params
    assert P.uses_claude({"stage": "insight", "params": json.dumps(params)})
    assert ST.selected(new_lab, ST.BY_STAGE["commitments"]).claude  # follows the review's model
    assert ST.privacy(new_lab)["review"] == "Claude Opus 5.5 through Claude Code"


def test_the_privacy_line_names_what_actually_reads_the_text_in_jays_lab(conn):
    assert ST.privacy(conn) == {"review": "Claude Opus 5.5 through Claude Code", "local": False,
                                "hindi": "Claude Opus 5.5 through Claude Code"}  # as it is
    ST.choose(conn, "insight", "qwen3-8b")
    assert ST.privacy(conn) == {"review": "qwen3:8b (local)", "local": True, "hindi": "Claude Opus 5.5 through Claude Code"}


def test_the_privacy_line_in_a_new_lab(new_lab):
    assert ST.privacy(new_lab) == {"review": None, "local": False, "hindi": None}  # a new lab: nothing leaves yet
    PV.add("anthropic", api_key="sk-ant-x-9999")
    ST.choose(new_lab, "insight", "anthropic:claude-opus-5-5")
    p = ST.privacy(new_lab)
    assert p["review"].startswith("Claude Opus") and "Claude Code" not in p["review"] and p["hindi"] == p["review"]


def test_choosing_a_connected_model_runs_the_review_on_it(new_lab):
    PV.add("anthropic", api_key="sk-ant-x-9999")
    PV.add("openai", label="OpenRouter", base_url="https://openrouter.ai/api/v1", api_key="k", models=["vendor/model-x"])
    ids = [c.id for c in ST.choices(new_lab, ST.BY_STAGE["insight"])]
    assert "anthropic:claude-opus-5-5" in ids and "openrouter:vendor/model-x" in ids
    new_lab.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('owner', ?, 0)",
                    (json.dumps({"name": "Asha", "pronoun": "she"}),))
    ST.choose(new_lab, "insight", "anthropic:claude-opus-5-5")
    params = params_for(STAGES["insight"], ST.overrides(new_lab, "insight"))
    assert params["reader"] == {"kind": "anthropic", "connection": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
    assert params["owner"]["name"] == "Asha" and "api_key" not in json.dumps(params)
    assert ST.reader(new_lab)["kind"] == "anthropic" and ST.review_blocked(new_lab) is None
    before = input_hash(STAGES["insight"], params, "s1", ["t1"])
    new_lab.execute("UPDATE app_config SET value=? WHERE key='owner'", (json.dumps({"name": "Asha", "pronoun": "they"}),))
    after = input_hash(STAGES["insight"], params_for(STAGES["insight"], ST.overrides(new_lab, "insight")), "s1", ["t1"])
    assert after != before  # editing the profile makes the review again
    PV.remove("anthropic")
    assert ST.selected(new_lab, ST.BY_STAGE["insight"]) is None  # its service gone: back to choosing


def test_the_hindi_merge_uses_the_owners_model_in_a_new_lab_and_claude_code_in_jays(conn, new_lab, monkeypatch):
    out = {"language": "hi", "paused": True}
    monkeypatch.setattr(PL, "other_language", lambda o: "hi")
    monkeypatch.setattr(PL, "language_paused", lambda o: True)
    monkeypatch.setattr(PL, "jay_talk_s", lambda c, s: 60.0)
    for c in (conn, new_lab):
        c.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at, is_current, output) "
                  "VALUES ('t1', 'transcribe', 's1', 'h', 'done', 0, 1, ?)", (json.dumps(out),))
    from octo.hindi import HINDI
    conn.execute("""INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES ('lab_origin', '"first"', 0)""")  # the first lab, as migrated (octo/labconfig.py)
    assert PL.hindi_input(conn, "s1") == {"hindi": HINDI}  # The owner's: unchanged, nothing re-transcribes
    assert PL.hindi_input(new_lab, "s1") == {}  # no model yet: stays paused
    PV.add("anthropic", api_key="sk-ant-x-9999")
    ST.choose(new_lab, "insight", "anthropic:claude-sonnet-5-5")
    h = PL.hindi_input(new_lab, "s1")["hindi"]
    assert h["reader"]["model"] == "claude-sonnet-5-5" and h["merge_model"] == "claude-sonnet-5-5"


# ---- the calls ---------------------------------------------------------------------------------

class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def fake_anthropic(monkeypatch, msg, sent):
    import anthropic

    class Client:
        def __init__(self, **kw):
            sent["client"] = kw
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

        def stream(self, **kw):
            sent["request"] = kw
            return FakeStream(msg)

    monkeypatch.setattr(anthropic, "Anthropic", Client)


def message(content, stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(content=content, stop_reason=stop, model=model, stop_details=None,
                           usage=SimpleNamespace(input_tokens=10_000, output_tokens=8_000,
                                                 cache_creation_input_tokens=0, cache_read_input_tokens=0))


def block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


def test_claude_on_the_owners_key(monkeypatch):
    PV.add("anthropic", api_key="sk-ant-x-9999")
    sent = {}
    fake_anthropic(monkeypatch, message([block("thinking", thinking=""), block("text", text='{"ok": true}')]), sent)
    reader = {"kind": "anthropic", "connection": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
    answer, usage = PV.ask(reader, "sys", "prompt", {"type": "object", "properties": {"ok": {"type": "boolean"}}})
    assert answer == {"ok": True}
    assert sent["client"]["api_key"] == "sk-ant-x-9999"
    req = sent["request"]
    assert req["model"] == "claude-opus-5-5" and req["system"] == "sys" and req["fallbacks"] == "default"
    assert req["output_config"]["effort"] == "high"  # Opus 5.5 defaults to medium: set as the reviews were made
    assert req["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert usage["cost_usd"] == pytest.approx(0.2) and usage["output_tokens"] == 8_000  # 10k × $4 + 8k × $20 per M


def test_haiku_takes_no_effort_and_a_fallback_restarts_the_answer(monkeypatch):
    PV.add("anthropic", api_key="sk-ant-x-9999")
    sent = {}
    content = [block("text", text='{"ok": fa'), block("fallback"), block("text", text='{"ok": true}')]
    fake_anthropic(monkeypatch, message(content, model="claude-haiku-4-5"), sent)
    reader = {"kind": "anthropic", "connection": "anthropic", "model": "claude-haiku-4-5", "effort": "high"}
    answer, usage = PV.ask(reader, "s", "p", {"type": "object", "properties": {"ok": {"type": "boolean"}}})
    assert answer == {"ok": True} and "effort" not in sent["request"]["output_config"]
    assert "fallbacks" not in sent["request"] and usage["model"] == "claude-haiku-4-5"


def test_a_refusal_or_a_cut_off_answer_is_a_failure_with_a_reason(monkeypatch):
    PV.add("anthropic", api_key="sk-ant-x-9999")
    reader = {"kind": "anthropic", "connection": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
    fake_anthropic(monkeypatch, message([], stop="refusal"), {})
    with pytest.raises(PV.ProviderError, match="declined"):
        PV.ask(reader, "s", "p", {})
    fake_anthropic(monkeypatch, message([block("text", text='{"ok"')], stop="max_tokens"), {})
    with pytest.raises(PV.ProviderError, match="cut off"):
        PV.ask(reader, "s", "p", {})
    with pytest.raises(PV.ProviderError, match="isn't connected"):
        PV.ask({**reader, "connection": "gone"}, "s", "p", {})


@pytest.fixture
def service():
    """An OpenAI-compatible service on this machine: records each request, answers what's queued."""
    seen, answers = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            code, payload = answers.pop(0)
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1", seen, answers
    srv.shutdown()


def test_an_openai_compatible_service(service):
    base, seen, answers = service
    PV.add("openai", label="Local", base_url=base, api_key="k-123", models=["m1"])
    answers.append((200, {"model": "m1-2026", "choices": [{"finish_reason": "stop", "message": {
        "content": '```json\n{"ok": true}\n```'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}))
    reader = {"kind": "openai", "connection": "local", "model": "m1", "base_url": base}
    answer, usage = PV.ask(reader, "sys", "prompt", {"type": "object", "properties": {"ok": {"type": "boolean"}}})
    assert answer == {"ok": True} and usage["model"] == "m1-2026" and usage["cost_usd"] is None
    req = seen[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer k-123"
    assert req["body"]["messages"][0] == {"role": "system", "content": "sys"}
    fmt = req["body"]["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"]["additionalProperties"] is False
    answers.append((401, {"error": "bad key"}))
    with pytest.raises(PV.ProviderError, match="didn't accept the key"):
        PV.ask(reader, "s", "p", {})
    answers.append((200, {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}))
    with pytest.raises(PV.ProviderError, match="cut off"):
        PV.ask(reader, "s", "p", {})


# ---- the review on any model -------------------------------------------------------------------

def test_the_review_on_any_model_is_checked_like_jays(monkeypatch):
    sent = {}

    def fake_ask(reader, system, prompt, schema):
        sent.update(reader=reader, system=system, prompt=prompt, schema=schema)
        return ANSWER, {"model": "claude-opus-5-5", "cost_usd": 0.2}

    reader = {"kind": "anthropic", "connection": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
    owner = {"name": "Sam", "pronoun": "she", "about": "a teacher in Pune", "principles_from": "the Gita"}
    params = {**PARAMS, "reader": reader, "owner": owner}
    out = RA.run(DZ, NAMES, {"B"}, params, RUBRIC, ask=fake_ask)
    assert out["engine"] == "anthropic" and out["usage"]["cost_usd"] == 0.2
    assert "for Sam, a teacher in Pune. She uses these reviews" in sent["system"]
    assert "principles she wrote herself from the Gita" in sent["system"]
    assert "Read the whole conversation first." in sent["system"]  # the owner's rules, as written
    assert "Sam's principles, in her words" in sent["prompt"]
    assert "product manager" not in sent["system"]
    # The same checks as the owner's review: an invented quote and a one-line "pattern" are dropped.
    assert [r["headline"] for r in out["reading"]] == ["Sets standards from examples"]
    assert [b["headline"] for b in out["between"]] == ["The disagreement stayed open"]
    again = RA.run(DZ, NAMES, {"B"}, {**params}, RUBRIC, ask=lambda *a: pytest.fail("asked again"),
                   memo=out["memo"])
    assert again["reused"] == 1


def test_they_reads_as_they():
    s = RA.system_prompt({"name": "Sam"}, "rules")
    assert "for Sam. They use these reviews to get better at how they show up" in s
    assert "what they were thinking" in s and "the next time they talk to that person" in s


def test_the_insight_stage_sends_a_connected_model_to_review_any(monkeypatch):
    called = {}
    monkeypatch.setattr(RA, "run", lambda *a, **kw: called.setdefault("review_any", True) and {"kind": "review"})
    reader = {"kind": "anthropic", "connection": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
    params = {**STAGES["insight"].params, "reader": reader, "speakers": [{"speaker": "B", "name": "Sam", "self": True}]}
    stages.insight({"upstream": [DZ]}, params)
    assert called == {"review_any": True}


def test_a_redirect_is_not_followed_and_outages_say_they_will_be_tried_again(tmp_path):
    """Review 8 Oct, finding 9: urllib followed a 3xx with the Authorization header, to any URL. And finding 5:
    what the pipeline waits on instead of counting ends with providers.TRANSIENT."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers["Content-Length"]))
            code, headers = {"/a/chat/completions": (302, {"Location": "http://127.0.0.1:9/elsewhere"}),
                             "/b/chat/completions": (503, {}),
                             "/c/chat/completions": (429, {"Retry-After": "120"})}[self.path]
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_port
        for path in ("a", "b", "c"):
            PV.add("openai", label=f"S{path}", base_url=f"http://127.0.0.1:{port}/{path}", api_key="k-secret", models=["m"])
        ask = lambda path: PV.ask({"kind": "openai", "connection": f"s{path}", "model": "m",
                                   "base_url": f"http://127.0.0.1:{port}/{path}"}, "s", "p", {})
        with pytest.raises(PV.ProviderError, match="not followed") as e:
            ask("a")
        assert PV.TRANSIENT not in str(e.value) and [p for p, _ in seen] == ["/a/chat/completions"]  # nothing else asked
        with pytest.raises(PV.ProviderError, match=PV.TRANSIENT):
            ask("b")
        with pytest.raises(PV.ProviderError, match=r"\(it asked for 120 s\)"):
            ask("c")
    finally:
        srv.shutdown()
    with pytest.raises(PV.ProviderError, match=PV.TRANSIENT):  # nothing listening: the network, not the request
        PV._openai({"base_url": "http://127.0.0.1:9/v1", "id": "x"}, "m", "s", "p", {}, 5)


def test_a_damaged_keys_file_is_never_saved_over_and_two_writers_keep_both(tmp_path):
    """Review 8 Oct, finding 11: a damaged providers.json read as empty, and the next change saved over every key;
    two changes at once (a double click on Connect) could lose one."""
    PV.CONFIG.write_text('{"connections": [{"id": "anthropic", "kind": "anthropic", "api_key": "sk-ant-x"')  # cut off
    with pytest.raises(PV.ProviderError, match="can't be read, so nothing was changed"):
        PV.add("openai", label="Local", base_url="http://127.0.0.1:1234/v1", models=["m"])
    assert PV.CONFIG.read_text().endswith('"sk-ant-x"')  # left as it was, for someone to look at
    assert PV.load() == {"connections": []}  # reading it just finds nothing to use
    PV.CONFIG.unlink()
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: PV.add("openai", label=f"S{i}", base_url="http://127.0.0.1:1234/v1", models=["m"]), range(8)))
    assert sorted(c["label"] for c in PV.load()["connections"]) == [f"S{i}" for i in range(8)]
    assert oct(PV.CONFIG.stat().st_mode & 0o777) == "0o600" and not list(PV.CONFIG.parent.glob("*.tmp"))


def _lab_before_8_oct(path, claude_code: bool | None, reviews: bool):
    """A lab as the 7 Oct code left it: its three settings decided (or none), no lab_origin, schema 1."""
    c = connect(path)
    c.execute("DELETE FROM app_config")
    if claude_code is not None:
        for key, jays in (("claude_code", True), ("voiceprints_need_consent", False)):
            c.execute("INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, 0)",
                      (key, "true" if jays == claude_code else "false"))
    if reviews:
        c.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) VALUES ('r', 'insight', 's', 'h', 'done', 0)")
    c.execute("PRAGMA user_version = 1")
    c.close()
    return connect(path)


def test_which_lab_is_decided_once_and_every_setting_follows_it(tmp_path, monkeypatch):
    """Review 8 Oct, finding 8 (octo/labconfig.py)."""
    monkeypatch.setattr(LC, "TEST_OVERRIDES", {})
    jays = _lab_before_8_oct(tmp_path / "first.db", claude_code=True, reviews=True)
    assert LC.origin(jays) == "first" and LC.flag(jays, "claude_code")
    assert not LC.flag(jays, "voiceprints_need_consent")
    # A buyer's lab from 7 Oct that has had reviews since is still a buyer's: its own rows said so.
    buyer = _lab_before_8_oct(tmp_path / "buyer.db", claude_code=False, reviews=True)
    assert LC.origin(buyer) == "product" and not LC.flag(buyer, "claude_code")
    # A setting added later follows the lab, not "has it had reviews": the buyer's lab isn't the owner's for it.
    monkeypatch.setitem(LC.FLAGS, "ticktick_auto", {LC.FIRST: True, LC.PRODUCT: False})
    assert LC.flag(jays, "ticktick_auto") and not LC.flag(buyer, "ticktick_auto")
    # Never an environment variable: a shell left with the old switches set changes nothing.
    monkeypatch.setenv("OCTO_CLAUDE_CODE", "1")
    monkeypatch.setenv("OCTO_GATE", "1")
    assert not PV.claude_code_default(buyer)
    new = connect(tmp_path / "new.db")
    assert LC.origin(new) == "product" and not LC.flag(new, "claude_code")


def test_a_merged_hindi_transcript_stands_when_the_review_model_changes(new_lab, monkeypatch):
    """Review 8 Oct, finding 6: in a new lab the merge's model followed the review's choice, so switching it redid
    every merged Hindi transcript (and paid again), and a local review model undid them back to the paused draft."""
    from octo.hindi import HINDI
    monkeypatch.setattr(PL, "other_language", lambda o: "hi")
    PV.add("anthropic", api_key="sk-ant-x-9999")
    ST.choose(new_lab, "insight", "anthropic:claude-sonnet-5-5")
    merged_with = {**HINDI, "merge_model": "claude-sonnet-5-5",
                   "reader": {"kind": "anthropic", "connection": "anthropic", "model": "claude-sonnet-5-5", "effort": "high"}}
    new_lab.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at, is_current, params, output) "
                    "VALUES ('t1', 'transcribe', 's1', 'h', 'done', 0, 1, ?, ?)",
                    (json.dumps({"hindi": merged_with}), json.dumps({"language": "hi", "merged": {"calls": 3}})))
    ST.choose(new_lab, "insight", "anthropic:claude-opus-5-5")  # another model for reviews
    assert PL.hindi_input(new_lab, "s1") == {"hindi": merged_with}  # the merge stands
    PV.remove("anthropic")  # its connection gone
    assert PL.hindi_input(new_lab, "s1") == {"hindi": merged_with}
    monkeypatch.setattr("octo.hindi.HINDI", {**HINDI, "chunk_s": 33.0})  # the merge itself changes: made again
    assert PL.hindi_input(new_lab, "s1") == {}  # (no model now, so it waits for one)


def test_a_key_is_tested_before_its_kept(tmp_path, monkeypatch):
    # Onboarding review, 28 Oct: a refused key or an empty account is said on Connect, not at the first review.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    monkeypatch.setattr(PV, "CONFIG", tmp_path / "providers.json")
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    add = lambda: c.post("/api/octo/settings/providers", json={"kind": "anthropic", "api_key": "sk-ant-0000000000"})
    answers = iter([PV.ProviderError("The Anthropic account is out of credit: add some at console.anthropic.com (Billing)"),
                    PV.ProviderError(f"Couldn't reach Anthropic (is the Mac online?): {PV.TRANSIENT}"),
                    ({"ok": True}, {"model": "claude-haiku-4-5"})])

    def ask(*a, **k):
        got = next(answers)
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(PV, "ask", ask)
    r = add()
    assert r.status_code == 400 and "out of credit" in r.json()["detail"] and PV.load()["connections"] == []
    r = add()  # offline for now: kept, and the page says the test didn't get through
    assert r.status_code == 200 and "Couldn't reach" in r.json()["tested"]["error"] and len(PV.load()["connections"]) == 1
    r = add()
    assert r.json()["tested"]["ok"] is True and len(PV.load()["connections"]) == 2


def test_the_pipeline_page_reads_as_the_first_labs_notebook_only_there(new_lab, tmp_path):
    # Review 5 Nov, item 1: Jay's dates, tests and history are his lab's; a buyer reads what each step does, plainly.
    jays = connect(tmp_path / "jays.db")
    jays.execute("INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES ('lab_origin', '\"first\"', 0)")
    first = {s["stage"]: s for s in ST.describe(jays)}
    assert first["transcribe"]["why"] == ST.BY_STAGE["transcribe"].why and first["transcribe"]["tried"]
    assert first["speakers"]["choices"][1]["why"].startswith("Merged two people into one speaker in a family")
    for s in ST.describe(new_lab):
        texts = [s["why"], *s["tried"], *(c["why"] for c in s["choices"])]
        assert not s["tried"] and not any(m in t for t in texts for m in (" Oct", "family", "colleagues", "B14"))
