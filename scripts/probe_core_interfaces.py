#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe the gateway's core text, media, and panel interfaces.

The default run is intentionally safe: it exercises text requests, media
catalogue reads, and local validation failures, but does not submit an image
or video generation.  Pass ``--spend-image`` or ``--spend-video`` to opt in to
the billable paths.

The probe never prints API keys or panel tokens.  It can read the panel's
``settings.json`` directly, or use ``--api-key`` for a remote deployment.

Examples:

    python scripts/probe_core_interfaces.py \
        --base-url http://192.168.90.126:8788 \
        --accounts-dir ./accounts

    python scripts/probe_core_interfaces.py --api-key "$WB_API_KEY" \
        --base-url http://127.0.0.1:8788

    python scripts/probe_core_interfaces.py --panel-password admin \
        --test-accounts
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = os.environ.get("WB_PROBE_BASE", "http://127.0.0.1:8788")


def _json_request(base, method, path, key=None, body=None, timeout=60):
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base.rstrip("/") + path, data=data,
                                     method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", "Bearer " + key)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8", "replace") or "{}")
            except ValueError:
                parsed = {"raw": raw[:300].decode("utf-8", "replace")}
            return response.status, parsed, time.time() - started
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            parsed = {"raw": raw[:300].decode("utf-8", "replace")}
        return exc.code, parsed, time.time() - started
    except Exception as exc:
        return None, {"error": str(exc)[:300]}, time.time() - started


def _stream_request(base, path, key, body, timeout=120):
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST")
    request.add_header("Authorization", "Bearer " + key)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    started = time.time()
    events = []
    text = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                events.append(event)
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        text.append(content)
            return response.status, events, "".join(text), time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, [], "", time.time() - started
    except Exception:
        return None, [], "", time.time() - started


def _mask_key(entry):
    name = str(entry.get("name") or "key").strip()
    realm = str(entry.get("realm") or "auto").strip()
    return "%s/%s" % (name[:24], realm)


def _load_keys(path, explicit, realm):
    if explicit:
        return [{"name": "cli", "realm": realm or "auto", "key": explicit}]
    try:
        with open(os.path.join(path, "settings.json"), encoding="utf-8") as handle:
            settings = json.load(handle)
    except Exception as exc:
        raise SystemExit("cannot read %s/settings.json: %s" % (path, exc))
    keys = []
    for entry in settings.get("api_keys") or []:
        if not isinstance(entry, dict) or not entry.get("enabled"):
            continue
        value = str(entry.get("key") or "").strip()
        if value and (not realm or entry.get("realm") == realm):
            keys.append(dict(entry, key=value))
    if not keys:
        raise SystemExit("no enabled API key found (use --api-key)")
    return keys


class Report(object):
    def __init__(self):
        self.rows = []

    def add(self, scope, name, status, elapsed, detail="", expected=False,
            expected_status=None):
        ok = status is not None and 200 <= status < 300
        if expected_status is not None:
            statuses = ((expected_status,) if isinstance(expected_status, int)
                        else tuple(expected_status))
            ok = status in statuses
            expected = True
        elif expected:
            ok = status is not None and 400 <= status < 500
        self.rows.append({
            "scope": scope, "name": name, "status": status,
            "ok": ok, "expected": expected, "elapsed": round(elapsed, 3),
            "detail": detail,
        })
        marker = "PASS" if ok else "FAIL"
        print("  [%s] %-32s HTTP %-3s %7.2fs %s" % (
            marker, name, status if status is not None else "-", elapsed,
            detail[:160]))

    def failed(self):
        return [row for row in self.rows if not row["ok"]]


def _pick_model(models):
    ids = [str(item.get("id") or "") for item in models
           if isinstance(item, dict)]
    for wanted in ("deepseek-v4.1-flash", "hy3", "glm-5.3"):
        if wanted in ids:
            return wanted
    return ids[0] if ids else ""


def probe_key(report, base, entry, spend_image=False, spend_video=False,
              timeout=90):
    label = _mask_key(entry)
    key = entry["key"]
    print("\n[%s]" % label)

    status, models, elapsed = _json_request(
        base, "GET", "/v1/models", key=key, timeout=timeout)
    model_ids = [item.get("id") for item in (models.get("data") or [])
                 if isinstance(item, dict)]
    report.add(label, "models", status, elapsed,
               "%d models" % len(model_ids))
    if status != 200:
        return
    model = _pick_model(models.get("data") or [])
    if not model:
        report.add(label, "chat model selection", 500, 0, "empty catalogue")
        return

    status, body, elapsed = _json_request(
        base, "POST", "/v1/chat/completions", key=key,
        body={"model": model, "messages": [
            {"role": "user", "content": "Reply with exactly OK."}],
            "max_tokens": 64, "temperature": 0},
        timeout=timeout)
    reply = ""
    if isinstance(body, dict):
        reply = str(((body.get("choices") or [{}])[0].get("message") or {})
                    .get("content") or "")
    report.add(label, "chat/completions", status, elapsed,
               "model=%s reply=%r" % (model, reply[:40]))

    status, events, streamed, elapsed = _stream_request(
        base, "/v1/chat/completions", key,
        {"model": model, "stream": True, "messages": [
            {"role": "user", "content": "Reply with exactly OK."}],
         "max_tokens": 64, "temperature": 0},
        timeout=timeout)
    report.add(label, "chat/completions stream", status, elapsed,
               "events=%d text=%r" % (len(events), streamed[:40]))

    status, body, elapsed = _json_request(
        base, "POST", "/v1/responses", key=key,
        body={"model": model, "input": "Reply with exactly OK.",
              "max_output_tokens": 64},
        timeout=timeout)
    output = str((body or {}).get("output_text") or "")
    report.add(label, "responses", status, elapsed,
               "status=%s output=%r" % ((body or {}).get("status"), output[:40]))

    status, events, streamed, elapsed = _stream_request(
        base, "/v1/responses", key,
        {"model": model, "input": "Reply with exactly OK.",
         "stream": True, "max_output_tokens": 64},
        timeout=timeout)
    report.add(label, "responses stream", status, elapsed,
               "events=%d" % len(events))

    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]
    status, body, elapsed = _json_request(
        base, "POST", "/v1/chat/completions", key=key,
        body={"model": model, "messages": [
            {"role": "user", "content": "Use get_weather for Shanghai."}],
            "tools": tools, "tool_choice": "auto", "max_tokens": 128},
        timeout=timeout)
    finish = None
    tool_name = ""
    if isinstance(body, dict):
        choice = (body.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        calls = (choice.get("message") or {}).get("tool_calls") or []
        if calls:
            tool_name = str((calls[0].get("function") or {}).get("name") or "")
    report.add(label, "chat tools", status, elapsed,
               "finish=%s tool=%s" % (finish, tool_name or "-"))

    status, body, elapsed = _json_request(
        base, "GET", "/v1/media/models", key=key, timeout=timeout)
    media = [item.get("id") for item in (body.get("models") or [])
             if isinstance(item, dict)]
    report.add(label, "media/models", status, elapsed,
               "%d models" % len(media))

    status, body, elapsed = _json_request(
        base, "GET", "/v1/media/tasks?limit=5", key=key, timeout=timeout)
    report.add(label, "media/tasks", status, elapsed,
               "%d tasks" % len((body or {}).get("tasks") or []))

    status, body, elapsed = _json_request(
        base, "GET", "/v1/balance-summary", key=key, timeout=timeout)
    report.add(label, "balance-summary", status, elapsed)

    status, body, elapsed = _json_request(
        base, "POST", "/v1/images/generations", key=key,
        body={"prompt": "probe", "size": "bad-size"}, timeout=timeout)
    report.add(label, "images/generations validation", status, elapsed,
               "expected 400; no spend", expected=True)

    status, body, elapsed = _json_request(
        base, "POST", "/v1/images/edits", key=key,
        body={"prompt": "probe", "image": "/etc/passwd"}, timeout=timeout)
    report.add(label, "images/edits validation", status, elapsed,
               "expected 400; local path rejected", expected=True)

    status, body, elapsed = _json_request(
        base, "POST", "/v1/videos", key=key,
        body={"prompt": "probe", "seconds": 1}, timeout=timeout)
    report.add(label, "videos validation", status, elapsed,
               "expected 4xx; no upstream submission", expected=True)

    if spend_image:
        status, body, elapsed = _json_request(
            base, "POST", "/v1/images/generations", key=key,
            body={"prompt": "A simple blue circle on white.",
                  "size": "1024x1024"}, timeout=timeout)
        report.add(label, "images/generations paid", status, elapsed,
                   "credit=%s model=%s" % ((body or {}).get("credit"),
                                            (body or {}).get("model")))

    if spend_video:
        status, body, elapsed = _json_request(
            base, "POST", "/v1/videos", key=key,
            body={"prompt": "A simple blue circle moving slowly.",
                  "model": "kling-video-v3", "seconds": 3},
            timeout=timeout)
        report.add(label, "videos paid submission", status, elapsed,
                   "task=%s status=%s" % ((body or {}).get("id"),
                                           (body or {}).get("status")))


def probe_panel(report, base, password, test_accounts=False, timeout=60):
    print("\n[panel]")
    status, body, elapsed = _json_request(
        base, "POST", "/panel/login", body={"password": password},
        timeout=timeout)
    report.add("panel", "panel/login", status, elapsed,
               "default=%s" % (body or {}).get("using_default_password"))
    if status != 200:
        return
    token = str((body or {}).get("token") or "")
    if not token:
        report.add("panel", "panel token", 500, 0, "missing token")
        return

    # Panel routes authenticate with X-Panel-Token, not the API bearer key.
    def panel_get(path):
        request = urllib.request.Request(base.rstrip("/") + path)
        request.add_header("X-Panel-Token", token)
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw or b"{}"), time.time() - started
        except urllib.error.HTTPError as exc:
            return exc.code, {}, time.time() - started
        except Exception:
            return None, {}, time.time() - started

    for path, name in (
            ("/settings", "settings"),
            ("/accounts", "accounts"),
            ("/scheduler", "scheduler"),
            ("/tasks", "tasks"),
            ("/pricing", "pricing"),
            ("/updates", "updates"),
            ("/usage/remaining", "usage/remaining"),
            ("/usage/analytics?range=7d", "usage/analytics"),
            ("/activity/history?range=7d", "activity/history"),
            ("/accounts/credits", "accounts/credits"),
            ("/logs?limit=5", "logs"),
            ("/proxy/slots", "proxy/slots"),
            ("/agents/available", "agents/available"),
            ("/updates/backups", "updates/backups")):
        status, body, elapsed = panel_get(path)
        report.add("panel", name, status, elapsed)

    status, body, elapsed = panel_get("/codearts/overview")
    report.add("panel", "codearts/overview", status, elapsed,
               "200 when configured; 503 when CodeArts is not configured",
               expected_status=(200, 503))

    if test_accounts:
        # This is an explicit opt-in because it sends a small request through
        # every enabled text account.
        request = urllib.request.Request(
            base.rstrip("/") + "/accounts/test-all",
            data=b"{}", method="POST")
        request.add_header("X-Panel-Token", token)
        request.add_header("Content-Type", "application/json")
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read() or b"{}")
                status = response.status
        except urllib.error.HTTPError as exc:
            status, body = exc.code, {}
        report.add("panel", "accounts/test-all", status, time.time() - started,
                   "tested=%s" % ((body or {}).get("summary", {})
                                  .get("tested")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--accounts-dir",
                        default=os.path.join(ROOT, "accounts"))
    parser.add_argument("--api-key", default="")
    parser.add_argument("--realm", choices=("intl", "cn"), default="")
    parser.add_argument("--panel-password", default="")
    parser.add_argument("--test-accounts", action="store_true")
    parser.add_argument("--spend-image", action="store_true")
    parser.add_argument("--spend-video", action="store_true")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)

    report = Report()
    status, body, elapsed = _json_request(
        args.base_url, "GET", "/health", timeout=args.timeout)
    report.add("gateway", "health", status, elapsed,
               "ok=%s accounts_ready=%s" % ((body or {}).get("ok"),
                                            (body or {}).get("accounts_ready")))
    if status != 200:
        print("\nGateway is not reachable; stop after health failure.")
        return 1

    try:
        keys = _load_keys(args.accounts_dir, args.api_key, args.realm)
    except SystemExit as exc:
        print("\n%s" % exc)
        return 2
    for entry in keys:
        probe_key(report, args.base_url, entry,
                  spend_image=args.spend_image,
                  spend_video=args.spend_video,
                  timeout=args.timeout)

    if args.panel_password:
        probe_panel(report, args.base_url, args.panel_password,
                    test_accounts=args.test_accounts,
                    timeout=args.timeout)

    failed = report.failed()
    print("\nSummary: %d checks, %d unexpected failures" % (
        len(report.rows), len(failed)))
    for row in failed:
        print("  - %s/%s: HTTP %s %s" % (
            row["scope"], row["name"], row["status"], row["detail"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
