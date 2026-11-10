"""Where the steps that read text run: the review, the Hindi merge and the coach (product plan 4.1, 7 Oct).

A lab's owner connects the services they want to use; the review step's choice on the Pipeline page
then decides which one reads (octo/settings.py, `reader`). Three kinds:

- **anthropic**: Claude through the Messages API, on the owner's own API key (console.anthropic.com).
- **openai**: any OpenAI-compatible endpoint: a base URL, a key if it needs one, and the model names
  the owner adds (OpenAI, Google's compatible endpoint, OpenRouter, LM Studio, Ollama's /v1 …).
- **claude-code**: `claude -p` on the Mac's own Claude Code login (octo/claude_code.py). Only in a lab
  that had it before the product (the first lab, since 2 Oct): Anthropic doesn't let a product run on its
  users' Claude subscriptions (P5), so a new lab never offers it.

Keys live in data/providers.json, readable by the Mac's user only, like the TickTick token and the
recorder's keys. They are never sent back to the page, never stored with a run, and not in the daily
backup (it copies the database only). What leaves the Mac is what the step sends, to the service the
owner chose: transcript text, names and numbers. Audio and voiceprints never do (P6).

A remote endpoint must use HTTPS; plain HTTP only to this Mac (a local model server).
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import DATA
from . import files as FL

CONFIG = DATA / "providers.json"
TIMEOUT_S = 1200  # a long conversation at high effort takes minutes

# Claude through the API: what each model costs per million tokens (input, output), from Anthropic's
# model list as of 25 Sep 2026, so a review's cost can be shown. An estimate: the bill is the truth.
# "effort": the model takes output_config.effort; "fallbacks": the server-side refusal fallback.
CLAUDE_MODELS: dict[str, dict] = {
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "price": (4.0, 20.0), "effort": True, "fallbacks": True,
                        "max_tokens": 64000,
                        "why": "The model the review was written and checked with. About $0.10 plus $0.006 a minute "
                               "of conversation at list prices (14 reviews measured)."},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "price": (2.0, 10.0), "effort": True, "fallbacks": True,
                          "max_tokens": 64000,
                          "why": "Half Opus's price. Not yet compared with Opus on real conversations."},
    # Haiku 5.5 in 4.5's place (review 10 Nov; id, price and effort from Anthropic's model list of 6 Oct). Its refusals
    # have no server-side fallback. shortcut: one price, its rate for prompts up to 100K tokens ($0.50/$2.50 beyond);
    # a transcript that long would show a low estimate.
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5", "price": (0.1, 0.5), "effort": True, "fallbacks": False,
                         "max_tokens": 64000,
                         "why": "The cheapest by far. Not yet compared on real conversations; likely to miss what the "
                                "bigger models find."},
    # Kept so a lab that chose it still runs (it takes no effort); no longer offered.
    "claude-haiku-4-5": {"label": "Claude Haiku 4.5", "price": (1.0, 5.0), "effort": False, "fallbacks": False,
                         "max_tokens": 32000, "offered": False,
                         "why": "The cheapest. Not yet compared on real conversations; likely to miss what the "
                                "bigger models find."},
}
OFFERED = [m for m, spec in CLAUDE_MODELS.items() if spec.get("offered", True)]
FALLBACK_BETA = "server-side-fallback-2026-07-01"
KINDS = ("anthropic", "openai")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ProviderError(RuntimeError):
    """A failure with a message for the owner: what went wrong and what to do about it."""


# A failure that says nothing about the request (a rate limit, an outage, the network): every such message ends
# with this, and the pipeline waits and tries again without counting it as an attempt (review 8 Oct, finding 5:
# before, an hour offline used up every queued review's three attempts). "(it asked for N s)" before it is a
# service's own Retry-After. A key that's refused or out of credit is counted as before: retrying won't fix it.
TRANSIENT = "it will be tried again later"


def _asked(headers) -> str:
    """A Retry-After in seconds, as the pipeline reads it; "" without one (or as a date)."""
    v = (headers or {}).get("retry-after") or (headers or {}).get("Retry-After")
    return f" (it asked for {int(float(v))} s)" if v and str(v).replace(".", "", 1).isdigit() else ""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A 3xx is an answer, not an address to send the key to (review 8 Oct, finding 9): urllib follows one with
    every header but the body's, the Authorization header included, to any URL, http:// too."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


# ---- the owner's connections -------------------------------------------------------------------

def load() -> dict:
    """For reading: no file, or a damaged one, is no connections (changes refuse a damaged one: _change)."""
    try:
        return FL.read_json(CONFIG, {"connections": []})
    except FL.Unreadable:
        return {"connections": []}


def save(cfg: dict) -> None:
    with FL.locked(CONFIG):
        FL.write_private(CONFIG, json.dumps(cfg, indent=2))


def _change(change) -> dict:
    """Read, change and save under the lock (octo/files.py, review 8 Oct finding 11): a double click on Connect
    can't lose a connection, and a damaged file is left alone rather than saved over as empty."""
    try:
        return FL.update_json(CONFIG, change, {"connections": []})
    except FL.Unreadable:
        raise ProviderError(f"{CONFIG} can't be read, so nothing was changed: it holds the model keys; "
                            "fix it or move it aside, then try again")


def connection(conn_id: str, cfg: dict | None = None) -> dict | None:
    return next((c for c in (cfg or load())["connections"] if c["id"] == conn_id), None)


def _slug(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "service"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def check_url(url: str) -> str:
    """The base URL as it will be called, or ProviderError: HTTPS, or HTTP to this Mac only."""
    u = urllib.parse.urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ProviderError("The base URL must start with https:// (or http:// for a server on this Mac)")
    if u.scheme == "http" and u.hostname not in LOCAL_HOSTS:
        raise ProviderError("A service on another computer must use https://: the transcript would travel unencrypted")
    return url.strip().rstrip("/")


def add(kind: str, label: str = "", api_key: str = "", base_url: str = "", models: list[str] | None = None) -> dict:
    """Add a connection. Anthropic: a key. OpenAI-compatible: a base URL, the models to offer, a key if
    the service needs one. Returns it as the page sees it (no key)."""
    if kind not in KINDS:
        raise ProviderError(f"unknown kind of service: {kind}")
    api_key = api_key.strip()
    if kind == "anthropic":
        if not api_key:
            raise ProviderError("Paste an API key from console.anthropic.com (Settings → API keys)")
        label = label.strip() or "Anthropic"
        entry = {"kind": kind, "label": label, "api_key": api_key}
    else:
        models = [m.strip() for m in (models or []) if m.strip()]
        if not models:
            raise ProviderError("Add at least one model name, exactly as the service spells it")
        label = label.strip() or (urllib.parse.urlparse(base_url).hostname or "Service")
        entry = {"kind": kind, "label": label, "base_url": check_url(base_url), "api_key": api_key, "models": models}
    def put(cfg: dict) -> None:
        entry["id"] = _slug(label, {c["id"] for c in cfg["connections"]})
        entry["added_at"] = time.time()
        cfg["connections"].append(entry)
    _change(put)
    return public(entry)


def update(conn_id: str, api_key: str | None = None, models: list[str] | None = None,
           daily_calls: int | None = None) -> dict:
    """daily_calls: a budget of calls a day on this connection (review 8 Oct, finding 15); 0 for none."""
    kept = [m.strip() for m in models or [] if m.strip()]
    found = {}

    def change(cfg: dict) -> None:
        c = connection(conn_id, cfg)
        if not c:
            raise KeyError(conn_id)
        if api_key is not None and api_key.strip():
            c["api_key"] = api_key.strip()
        if models is not None and c["kind"] == "openai":
            if not kept:
                raise ProviderError("Keep at least one model, or remove the service")
            c["models"] = kept
        if daily_calls is not None:
            if daily_calls < 0:
                raise ProviderError("A budget is a number of calls a day, or 0 for none")
            if daily_calls:
                c["daily_calls"] = daily_calls
            else:
                c.pop("daily_calls", None)
        found.update(c)
    _change(change)
    return public(found)


def remove(conn_id: str) -> None:
    _change(lambda cfg: {**cfg, "connections": [c for c in cfg["connections"] if c["id"] != conn_id]})


def public(c: dict) -> dict:
    """A connection as the page may see it: the key only as its last four characters."""
    key = c.get("api_key") or ""
    out = {k: v for k, v in c.items() if k != "api_key"}
    out["key"] = f"…{key[-4:]}" if len(key) >= 8 else ("set" if key else None)
    if c["kind"] == "anthropic":
        out["models"] = list(OFFERED)
    return out


def listing() -> list[dict]:
    return [public(c) for c in load()["connections"]]


# ---- Claude Code on this Mac (any lab that chooses it, P5 since 31 Oct) -----------------------------

def claude_code_default(conn) -> bool:
    """Whether Claude Code runs here when the owner hasn't chosen (the first lab, as before 7 Oct): decided once per
    lab (octo/db.py). Any lab may choose it; this only says whether it was chosen for them."""
    from . import labconfig
    return labconfig.flag(conn, "claude_code")


# ---- asking ------------------------------------------------------------------------------------

def strict(schema: dict) -> dict:
    """The schema as structured outputs need it: every object closed (additionalProperties false) and
    every property required. The answers' own schemas already require every field."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(s)
    return s


def ask(reader: dict, system: str, prompt: str, schema: dict, timeout: int = TIMEOUT_S) -> tuple[dict, dict]:
    """(the structured answer, usage) from whatever `reader` names (settings.reader). Raises
    ProviderError with a message for the owner on any failure. Every call made, or failed after it may have been billed,
    is a row in model_calls (db.model_call; Claude Code's in claude_code.ask)."""
    kind = reader["kind"]
    if kind == "claude-code":
        from .claude_code import ask as cc_ask
        return cc_ask(reader["model"], reader.get("effort", "high"), system, prompt, schema, timeout=timeout)
    from .db import model_call
    c = connection(reader["connection"])
    if not c:
        raise ProviderError(f"The service “{reader['connection']}” isn't connected any more (Settings → Models and keys)")
    try:
        if kind == "anthropic":
            out = _anthropic(c, reader["model"], reader.get("effort", "high"), system, prompt, schema, timeout)
        elif kind == "openai":
            out = _openai(c, reader["model"], system, prompt, schema, timeout)
        else:
            raise ProviderError(f"unknown kind of service: {kind}")
    except Billed as e:
        model_call(reader, False)
        raise ProviderError(str(e)) from None
    model_call(reader, True)
    return out


class Billed(ProviderError):
    """A failure after the service worked on the request (a timeout, an answer cut off or not in JSON): counted in the
    day's calls, since it may have been billed (review 5 Nov, risk 7)."""


def cost(model: str, usage) -> float | None:
    """Dollars at list price, or None for a model without a known price."""
    price = CLAUDE_MODELS.get(model, {}).get("price")
    if not price:
        return None
    tin = (usage.input_tokens or 0) + 1.25 * (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        + 0.1 * (getattr(usage, "cache_read_input_tokens", 0) or 0)
    return round((tin * price[0] + (usage.output_tokens or 0) * price[1]) / 1e6, 4)


def _anthropic(c: dict, model: str, effort: str, system: str, prompt: str, schema: dict, timeout: int) -> tuple[dict, dict]:
    import anthropic

    spec = CLAUDE_MODELS.get(model, {"effort": True, "fallbacks": False, "max_tokens": 32000})
    output_config: dict = {"format": {"type": "json_schema", "schema": strict(schema)}}
    if spec["effort"]:
        output_config["effort"] = effort
    extra: dict = {}
    if spec["fallbacks"]:  # a declined request is run again server-side on the model Anthropic recommends
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    client = anthropic.Anthropic(api_key=c["api_key"], timeout=float(timeout), max_retries=2)
    t0 = time.time()
    try:
        with client.beta.messages.stream(model=model, max_tokens=spec["max_tokens"], system=system,
                                         messages=[{"role": "user", "content": prompt}],
                                         output_config=output_config, **extra) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ProviderError("Anthropic didn't accept the API key: check it under Settings → Models and keys")
    except anthropic.PermissionDeniedError:
        raise ProviderError(f"This Anthropic key isn't allowed to use {model}")
    except anthropic.NotFoundError:
        raise ProviderError(f"Anthropic doesn't offer the model {model} to this key")
    except anthropic.RateLimitError as e:
        raise ProviderError(f"Anthropic's rate limit for this key was reached{_asked(e.response.headers)}: {TRANSIENT}")
    except anthropic.BadRequestError as e:
        if "credit balance" in str(e.message).lower():
            raise ProviderError("The Anthropic account is out of credit: add some at console.anthropic.com (Billing)")
        raise ProviderError(f"Anthropic refused the request: {e.message}")
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:  # 500, 529 overloaded: theirs, and passing
            raise ProviderError(f"Anthropic had a problem ({e.status_code}){_asked(e.response.headers)}: {TRANSIENT}")
        raise ProviderError(f"Anthropic refused the request ({e.status_code}): {e.message}")
    except anthropic.APITimeoutError:  # it may have worked on it, and billed it, before the time ran out
        raise Billed(f"Anthropic didn't answer within {timeout} s: {TRANSIENT}")
    except anthropic.APIConnectionError:
        raise ProviderError(f"Couldn't reach Anthropic (is the Mac online?): {TRANSIENT}")
    if msg.stop_reason == "refusal":
        why = getattr(getattr(msg, "stop_details", None), "category", None)
        raise Billed(f"Claude declined to answer{f' ({why})' if why else ''}")
    if msg.stop_reason == "max_tokens":
        raise Billed("The answer was cut off before it finished (the output limit was reached)")
    text = ""
    for block in msg.content:
        if block.type == "fallback":  # anything before a switch came from the model that declined
            text = ""
        elif block.type == "text":
            text += block.text
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        raise Billed("Claude's answer wasn't the JSON asked for")
    u = msg.usage
    return answer, {"provider": "anthropic", "model": msg.model, "cost_usd": cost(msg.model, u),
                    "duration_ms": round((time.time() - t0) * 1000), "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens}


def _openai(c: dict, model: str, system: str, prompt: str, schema: dict, timeout: int) -> tuple[dict, dict]:
    url = check_url(c["base_url"]) + "/chat/completions"
    body = {"model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "answer", "strict": True, "schema": strict(schema)}}}
    headers = {"Content-Type": "application/json"}
    if c.get("api_key"):
        headers["Authorization"] = f"Bearer {c['api_key']}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    name = c.get("label") or c["id"]
    t0 = time.time()
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if 300 <= e.code < 400:
            raise ProviderError(f"{name} answered with a redirect ({e.code}) to {e.headers.get('Location') or 'another address'}: "
                                "not followed, since the key would go with it. Use that address as its base URL")
        if e.code in (401, 403):
            raise ProviderError(f"{name} didn't accept the key: check it under Settings → Models and keys")
        if e.code == 404:
            raise ProviderError(f"{name} doesn't know the model “{model}” (or the base URL is wrong)")
        if e.code == 429:
            raise ProviderError(f"{name}'s rate limit was reached{_asked(e.headers)}: {TRANSIENT}")
        if e.code >= 500:
            raise ProviderError(f"{name} had a problem ({e.code}){_asked(e.headers)}: {TRANSIENT}")
        raise ProviderError(f"{name} answered {e.code}: {detail}")
    except TimeoutError:  # sent, and no answer in time: it may have been billed
        raise Billed(f"{name} didn't answer within {timeout} s: {TRANSIENT}")
    except (urllib.error.URLError, OSError) as e:
        raise ProviderError(f"Couldn't reach {name} ({getattr(e, 'reason', e)}): {TRANSIENT}")
    try:
        choice = d["choices"][0]
        if choice.get("finish_reason") == "length":
            raise Billed("The answer was cut off before it finished (the output limit was reached)")
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise Billed(f"{name}'s answer wasn't in the expected form")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())  # some models fence their JSON
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        raise Billed(f"{name}'s answer wasn't the JSON asked for")
    u = d.get("usage") or {}
    return answer, {"provider": c["id"], "model": d.get("model") or model, "cost_usd": None,
                    "duration_ms": round((time.time() - t0) * 1000), "input_tokens": u.get("prompt_tokens"),
                    "output_tokens": u.get("completion_tokens")}


# ---- a quick test of a connection ---------------------------------------------------------------

def test(conn_id: str, model: str | None = None) -> dict:
    """One tiny call: is the key accepted and does the model answer in JSON? Costs a fraction of a cent."""
    c = connection(conn_id)
    if not c:
        raise KeyError(conn_id)
    model = model or ("claude-haiku-5-5" if c["kind"] == "anthropic" else c["models"][0])
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    reader = {"kind": c["kind"], "connection": c["id"], "model": model, "effort": "low"}
    t0 = time.time()
    answer, usage = ask(reader, "Answer in JSON.", 'Reply with {"ok": true}.', schema, timeout=60)
    return {"ok": answer.get("ok") is True, "model": usage.get("model") or model, "seconds": round(time.time() - t0, 1)}
