"""
Claude Monitor — mitmproxy addon (สำหรับ Claude Desktop + Claude Code + API)

รองรับ:
  - api.anthropic.com/v1/messages          (Claude Code CLI, VS Code, API key)
  - claude.ai /api/organizations/.../chat_conversations/.../completion  (Claude Desktop)

โหมด Server (Docker) — พนักงานไม่ต้องลงอะไร:
  1. docker compose up --build -d  (รันบน server)
  2. พนักงานตั้ง system proxy → <server_ip>:8081
  3. พนักงาน download cert จาก http://<server_ip>:8080/cert แล้วติดตั้ง
     (หรือรัน setup-employee.ps1 ซึ่งทำให้อัตโนมัติ)

Environment Variables (ตั้งใน docker-compose.yml หรือ .env):
  CF_WORKER_URL     — Cloudflare Worker URL
  CF_WORKER_SECRET  — X-Api-Key
  LOG_DIR           — path เก็บ log (default: /logs ใน Docker, ./logs บน local)
"""

import json
import os
import re
import socket
import threading
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from mitmproxy import http

# โหลด .env จาก directory เดียวกับไฟล์นี้
_BASE_DIR = Path(__file__).parent
load_dotenv(_BASE_DIR / ".env")

WORKER_URL    = os.getenv("CF_WORKER_URL", "").rstrip("/")
API_KEY       = os.getenv("CF_WORKER_SECRET", "")
HOSTNAME      = socket.gethostname()

# โหลด account_email จาก CLAUDE_USER_SETTINGS (เหมือน proxy.py) หรือ env var
def _load_account_email() -> str:
    email = os.getenv("ACCOUNT_EMAIL", "")
    if email:
        return email
    path = os.getenv("CLAUDE_USER_SETTINGS", "")
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8-sig") as f:
            s = json.load(f)
        attrs = s.get("env", {}).get("OTEL_RESOURCE_ATTRIBUTES", "")
        for part in attrs.split(","):
            part = part.strip()
            if part.startswith("user.email="):
                return part.split("=", 1)[1]
    except Exception:
        pass
    return ""

ACCOUNT_EMAIL = _load_account_email()

# Local log directory — ใช้ env var LOG_DIR ถ้ามี (Docker ตั้งเป็น /logs)
_log_dir_env = os.getenv("LOG_DIR", "")
LOG_DIR = Path(_log_dir_env) if _log_dir_env else _BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _log_path() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"mitm_{today}.jsonl"

def _write_local(payload: dict):
    try:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# Pricing USD / 1M tokens
_PRICE = {
    "opus":   dict(inp=15,   out=75,  cr=1.50, cw=18.75),
    "sonnet": dict(inp=3,    out=15,  cr=0.30, cw=3.75),
    "haiku":  dict(inp=0.80, out=4,   cr=0.08, cw=1.00),
}

def _calc_cost(model: str, inp: int, out: int, cr: int, cw: int) -> float:
    tier = "opus" if "opus" in model else "haiku" if "haiku" in model else "sonnet"
    p = _PRICE[tier]
    return (inp * p["inp"] + out * p["out"] + cr * p["cr"] + cw * p["cw"]) / 1_000_000


def _detect_client(headers) -> str:
    ua   = str(headers.get("user-agent",            "")).lower()
    name = str(headers.get("anthropic-client-name", "")).lower()
    app  = str(headers.get("x-app",                 "")).lower()
    if "claude-code" in name or "claude-code" in ua or "claude-code" in app:
        return "claude-code"
    if "vscode" in ua or "vscode" in name:
        return "vscode"
    if "electron" in ua or "claude" in ua or "anthropic" in ua:
        return "claude-desktop"
    return "api"


# ── SSE parser: api.anthropic.com ────────────────────────────────────────────
def _parse_sse_api(text: str) -> dict:
    resp_text = ""
    inp = out = cr = cw = 0
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[6:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
            t   = obj.get("type", "")
            if t == "message_start":
                u   = obj.get("message", {}).get("usage", {})
                inp = u.get("input_tokens", 0)
                cr  = u.get("cache_read_input_tokens", 0)
                cw  = u.get("cache_creation_input_tokens", 0)
            elif t == "content_block_delta":
                d = obj.get("delta", {})
                if d.get("type") == "text_delta":
                    resp_text += d.get("text", "")
            elif t == "message_delta":
                out = obj.get("usage", {}).get("output_tokens", 0)
        except Exception:
            pass
    return dict(response=resp_text, input_tokens=inp, output_tokens=out,
                cache_read_tokens=cr, cache_creation_tokens=cw)


# ── SSE parser: claude.ai Desktop ────────────────────────────────────────────
_DEBUG_SSE = os.getenv("DEBUG_SSE", "0") == "1"

def _parse_sse_desktop(text: str) -> dict:
    resp_text = ""
    inp = out = cr = cw = 0
    model = ""
    seen_types: list[str] = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[6:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
            t   = obj.get("type", "")
            if t not in seen_types:
                seen_types.append(t)
            if t == "message_start":
                u     = obj.get("message", {}).get("usage", {})
                inp   = u.get("input_tokens", 0)
                cr    = u.get("cache_read_input_tokens", 0)
                cw    = u.get("cache_creation_input_tokens", 0)
                model = obj.get("message", {}).get("model", model)
                # fallback: บาง format ใช้ชื่อ field ต่างกัน
                if inp == 0:
                    inp = u.get("inputTokens", u.get("input_token_count", 0))
                if cr == 0:
                    cr = u.get("cache_read_tokens", 0)
                if cw == 0:
                    cw = u.get("cache_creation_tokens", 0)
            elif t == "content_block_delta":
                d = obj.get("delta", {})
                if d.get("type") == "text_delta":
                    resp_text += d.get("text", "")
            elif t == "message_delta":
                usage = obj.get("usage", {})
                out = usage.get("output_tokens", usage.get("outputTokens", 0))
            # รูปแบบเก่าของ claude.ai
            elif t == "completion":
                resp_text += obj.get("completion", "")
                inp = obj.get("usage", {}).get("input_tokens", inp)
                out = obj.get("usage", {}).get("output_tokens", out)
            elif "delta" in obj and "type" not in obj:
                delta = obj.get("delta", {})
                if isinstance(delta, dict):
                    resp_text += delta.get("text", "")
        except Exception:
            pass

    # debug: ถ้า tokens = 0 แต่มี response ให้ dump SSE ลงไฟล์เพื่อดู format จริง
    if inp == 0 and out == 0 and resp_text:
        if _DEBUG_SSE:
            _dump_sse_debug(text, seen_types)
        else:
            print(f"[claude-monitor] WARN tokens=0 | event types found: {seen_types} "
                  f"| set DEBUG_SSE=1 to dump raw SSE")

    return dict(response=resp_text, input_tokens=inp, output_tokens=out,
                cache_read_tokens=cr, cache_creation_tokens=cw, model=model)


def _dump_sse_debug(text: str, seen_types: list):
    """dump raw SSE ลงไฟล์เพื่อ debug — เปิดด้วย DEBUG_SSE=1"""
    try:
        debug_path = LOG_DIR / "sse_debug.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"=== event types: {seen_types} ===\n\n")
            for line in text.splitlines()[:60]:
                f.write(line + "\n")
        print(f"[claude-monitor] SSE debug → {debug_path}", flush=True)
    except Exception as e:
        print(f"[claude-monitor] dump_sse_debug failed: {e}", flush=True)


def _dump_raw_response(ct: str, text: str):
    """dump content-type + raw response body เพื่อดู format จริง"""
    try:
        debug_path = LOG_DIR / "sse_debug.txt"
        lines = text.splitlines()
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"=== Content-Type: {ct} | total lines: {len(lines)} ===\n\n")
            # เขียนทั้งหมด แต่ skip บรรทัด content_block_delta ที่ซ้ำๆ
            skip_next = False
            delta_count = 0
            for line in lines:
                if '"content_block_delta"' in line:
                    delta_count += 1
                    if delta_count > 3:
                        skip_next = True
                        continue
                else:
                    if skip_next and delta_count > 3:
                        f.write(f"  ... ({delta_count} content_block_delta lines skipped) ...\n")
                    skip_next = False
                    delta_count = 0
                f.write(line + "\n")
        print(f"[claude-monitor] raw dump → {debug_path}", flush=True)
    except Exception as e:
        print(f"[claude-monitor] dump_raw_response failed: {e}", flush=True)


# ── Extract prompt: api.anthropic.com ────────────────────────────────────────
def _extract_prompt_api(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        # ลบ XML tags ที่ Claude Code inject
        text = re.sub(r"<[a-zA-Z_][^>]*>.*?</[a-zA-Z_][^>]*>", "", text, flags=re.DOTALL)
        return text.strip()
    return ""


# ── Extract prompt: claude.ai Desktop ────────────────────────────────────────
def _extract_prompt_desktop(req_body: dict) -> str:
    # Format 1: messages array (รูปแบบใหม่)
    if "messages" in req_body:
        return _extract_prompt_api(req_body["messages"])
    # Format 2: prompt string (รูปแบบเก่า)
    if "prompt" in req_body:
        prompt = req_body["prompt"]
        if "\n\nHuman:" in prompt:
            parts = prompt.split("\n\nHuman:")
            last  = parts[-1]
            if "\n\nAssistant:" in last:
                last = last.split("\n\nAssistant:")[0]
            return last.strip()
        return prompt.strip()
    # Format 3: text field
    if "text" in req_body:
        return req_body["text"]
    return ""


# ── ส่งไป Cloudflare Worker (bypass system proxy เพื่อไม่ loop กลับมาหา mitmproxy) ──
# ใช้ endpoint เดียวกับ proxy.py: /api/prompt และ /api/usage
_no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def _post_worker(endpoint: str, payload: dict):
    if not WORKER_URL or not API_KEY:
        return
    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(f"{WORKER_URL}{endpoint}", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Api-Key",    API_KEY)
        req.add_header("User-Agent",   "claude-monitor-mitm/1.0")
        resp   = _no_proxy_opener.open(req, timeout=8)
        status = resp.getcode()
        if status != 200:
            print(f"[claude-monitor] WARN {endpoint} returned {status}")
    except Exception as e:
        print(f"[claude-monitor] ERROR {endpoint}: {type(e).__name__}: {e}")


def _send_to_worker(log: dict):
    """ส่ง prompt และ usage ไป Worker แยก 2 endpoint เหมือน proxy.py"""
    session_id = log.get("id", "")
    prompt     = log.get("prompt", "")
    model      = log.get("model", "")
    inp        = log.get("input_tokens", 0)
    out        = log.get("output_tokens", 0)
    cw         = log.get("cache_creation_tokens", 0)
    cr         = log.get("cache_read_tokens", 0)

    def _do_send():
        # prompt ต้องถึง Worker ก่อน usage เสมอ (ส่งตามลำดับใน thread เดียวกัน)
        _post_worker("/api/prompt", {
            "session_id":    session_id,
            "cwd":           "",
            "char_count":    len(prompt),
            "approx_tokens": max(1, int(len(prompt) / 3.5)),
            "prompt":        prompt,
            "account":       log.get("account_email", ""),
            "ip_address":    log.get("ip_address", ""),
            "source":        log.get("client", "claude-desktop"),
        })
        _post_worker("/api/usage", {
            "session_id":                  session_id,
            "model":                       model,
            "input_tokens":                inp,
            "output_tokens":               out,
            "cache_creation_input_tokens": cw,
            "cache_read_input_tokens":     cr,
            "total_tokens":                inp + out + cw + cr,
        })

    threading.Thread(target=_do_send, daemon=True).start()


def _build_log(client: str, model: str, prompt: str, parsed: dict,
               ip_address: str = "") -> dict:
    inp = parsed.get("input_tokens", 0)
    out = parsed.get("output_tokens", 0)
    cr  = parsed.get("cache_read_tokens", 0)
    cw  = parsed.get("cache_creation_tokens", 0)
    return {
        "id":                    str(uuid.uuid4()),
        "timestamp":             datetime.utcnow().isoformat() + "Z",
        "client":                client,
        "machine_name":          HOSTNAME,
        "ip_address":            ip_address,
        "account_email":         ACCOUNT_EMAIL,
        "model":                 model,
        "prompt":                prompt,
        "prompt_chars":          len(prompt),
        "response_chars":        len(parsed.get("response", "")),
        "input_tokens":          inp,
        "output_tokens":         out,
        "cache_creation_tokens": cw,
        "cache_read_tokens":     cr,
        "total_tokens":          inp + out + cr + cw,
        "cost_usd":              _calc_cost(model, inp, out, cr, cw),
    }


# ── mitmproxy addon: api.anthropic.com ───────────────────────────────────────
class ClaudeAPIMonitor:
    """ดัก api.anthropic.com/v1/messages — Claude Code, VS Code, API key"""

    def response(self, flow: http.HTTPFlow):
        if flow.request.host   != "api.anthropic.com": return
        if flow.request.path   != "/v1/messages":       return
        if flow.request.method != "POST":               return

        try:
            req = json.loads(flow.request.content)
        except Exception:
            return

        model    = req.get("model", "unknown")
        prompt   = _extract_prompt_api(req.get("messages", []))
        client   = _detect_client(flow.request.headers)

        ct     = flow.response.headers.get("content-type", "")
        is_sse = "event-stream" in ct
        text   = flow.response.content.decode("utf-8", errors="replace")

        if is_sse:
            parsed = _parse_sse_api(text)
        else:
            try:
                rj    = json.loads(text)
                usage = rj.get("usage", {})
                rtext = "".join(
                    b.get("text", "") for b in rj.get("content", [])
                    if b.get("type") == "text"
                )
                parsed = dict(
                    response=rtext,
                    input_tokens          = usage.get("input_tokens", 0),
                    output_tokens         = usage.get("output_tokens", 0),
                    cache_read_tokens     = usage.get("cache_read_input_tokens", 0),
                    cache_creation_tokens = usage.get("cache_creation_input_tokens", 0),
                )
            except Exception:
                return

        ip = flow.client_conn.peername[0] if flow.client_conn.peername else ""
        log = _build_log(client, model, prompt, parsed, ip_address=ip)
        _write_local(log)
        _send_to_worker(log)
        print(f"[api] {client} | {ip} | {model} | "
              f"in={log['input_tokens']:,} out={log['output_tokens']:,} | "
              f"${log['cost_usd']:.5f}")


# ── mitmproxy addon: claude.ai Desktop ───────────────────────────────────────
_COMPLETION_RE = re.compile(
    r"^/api/organizations/[^/]+/chat_conversations/[^/]+/completion$"
)

class ClaudeDesktopMonitor:
    """ดัก claude.ai — Claude Desktop app"""

    def response(self, flow: http.HTTPFlow):
        if "claude.ai" not in flow.request.host: return
        if flow.request.method != "POST":          return
        if not _COMPLETION_RE.match(flow.request.path): return

        try:
            req_body = json.loads(flow.request.content)
        except Exception:
            req_body = {}

        prompt = _extract_prompt_desktop(req_body)
        model  = req_body.get("model", "unknown")

        ct     = flow.response.headers.get("content-type", "")
        is_sse = "event-stream" in ct
        text   = flow.response.content.decode("utf-8", errors="replace")

        # dump raw response ครั้งแรกที่เจอ เพื่อ debug format
        if _DEBUG_SSE:
            _dump_raw_response(ct, text)

        if is_sse:
            parsed = _parse_sse_desktop(text)
            if model == "unknown" and parsed.get("model"):
                model = parsed["model"]
        else:
            try:
                rj    = json.loads(text)
                usage = rj.get("usage", {})
                rtext = "".join(
                    b.get("text", "") for b in rj.get("content", [])
                    if b.get("type") == "text"
                )
                parsed = dict(
                    response=rtext,
                    input_tokens          = usage.get("input_tokens", 0),
                    output_tokens         = usage.get("output_tokens", 0),
                    cache_read_tokens     = usage.get("cache_read_input_tokens", 0),
                    cache_creation_tokens = usage.get("cache_creation_input_tokens", 0),
                )
            except Exception:
                return

        # claude.ai ไม่ส่ง token counts มาใน SSE เลย — ประมาณจาก chars แทน
        if parsed.get("input_tokens", 0) == 0 and parsed.get("output_tokens", 0) == 0:
            resp_len = len(parsed.get("response", ""))
            parsed["input_tokens"]  = max(1, int(len(prompt) / 3.5))
            parsed["output_tokens"] = max(1, int(resp_len / 3.5))

        ip = flow.client_conn.peername[0] if flow.client_conn.peername else ""
        log = _build_log("claude-desktop", model, prompt, parsed, ip_address=ip)
        _write_local(log)
        _send_to_worker(log)
        print(f"[desktop] {ip} | {model} | prompt={len(prompt)}ch | "
              f"in~{log['input_tokens']:,} out~{log['output_tokens']:,} | "
              f"~${log['cost_usd']:.5f}", flush=True)


addons = [ClaudeAPIMonitor(), ClaudeDesktopMonitor()]
