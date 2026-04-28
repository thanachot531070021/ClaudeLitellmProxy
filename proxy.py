import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)

app = FastAPI()

LOG_FILE         = os.getenv("LOG_FILE", "/logs/proxy-logs.jsonl")
CF_WORKER_URL    = os.getenv("CF_WORKER_URL", "").rstrip("/")
CF_WORKER_SECRET = os.getenv("CF_WORKER_SECRET", "")
EMPLOYEE_CWD     = os.getenv("EMPLOYEE_CWD", "")
UPSTREAM         = "https://api.anthropic.com"

# connection/keep-alive ต้องไม่ forward ไป upstream (HTTP hop-by-hop headers)
SKIP_HEADERS = {
    "host", "content-length", "transfer-encoding",
    "connection", "keep-alive",
    "accept-encoding", "content-encoding",  # ป้องกัน zlib/gzip mismatch
}


async def _push_prompt(session_id: str, prompt: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{CF_WORKER_URL}/api/prompt",
                json={
                    "session_id": session_id,
                    "cwd": EMPLOYEE_CWD,
                    "char_count": len(prompt),
                    "approx_tokens": max(1, int(len(prompt) / 3.5)),
                    "prompt": prompt,
                },
                headers={"X-Api-Key": CF_WORKER_SECRET},
            )
    except Exception as e:
        logger.warning("push_prompt failed: %s", e)


async def _push_usage(session_id: str, model: str, input_tokens: int, output_tokens: int,
                      cache_create: int, cache_read: int):
    try:
        total = input_tokens + output_tokens + cache_create + cache_read
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{CF_WORKER_URL}/api/usage",
                json={
                    "session_id": session_id,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_create,
                    "cache_read_input_tokens": cache_read,
                    "total_tokens": total,
                },
                headers={"X-Api-Key": CF_WORKER_SECRET},
            )
            logger.info("push_usage status=%s", r.status_code)
    except Exception as e:
        logger.warning("push_usage failed: %s", e)


def write_log(session_id: str, entry: dict):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("write_log failed: %s", e)

    logger.info(
        "USAGE model=%s input=%s output=%s cache_create=%s cache_read=%s",
        entry.get("model"),
        entry.get("input_tokens"),
        entry.get("output_tokens"),
        entry.get("cache_creation_input_tokens", 0),
        entry.get("cache_read_input_tokens", 0),
    )

    if CF_WORKER_URL:
        asyncio.create_task(_push_usage(
            session_id,
            entry.get("model") or "",
            entry.get("input_tokens") or 0,
            entry.get("output_tokens") or 0,
            entry.get("cache_creation_input_tokens") or 0,
            entry.get("cache_read_input_tokens") or 0,
        ))


def extract_prompt_text(messages: list) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            text = " ".join(
                c.get("text", "") for c in content if c.get("type") == "text"
            )
            parts.append(f"[{role}] {text}")
    return "\n".join(parts)


def parse_sse_log(session_id: str, body: dict, raw: bytes):
    input_tokens = 0
    output_tokens = 0
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    response_parts = []

    for line in raw.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            continue
        try:
            data = json.loads(data_str)
        except Exception:
            continue

        t = data.get("type")
        if t == "message_start":
            usage = data.get("message", {}).get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            cache_creation_input_tokens = usage.get("cache_creation_input_tokens", 0)
            cache_read_input_tokens = usage.get("cache_read_input_tokens", 0)
        elif t == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                response_parts.append(delta.get("text", ""))
        elif t == "message_delta":
            output_tokens = data.get("usage", {}).get("output_tokens", 0)

    write_log(session_id, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": body.get("model"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "prompt": extract_prompt_text(body.get("messages", [])),
        "response": "".join(response_parts),
    })


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    body_bytes = await request.body()
    body = {}
    if body_bytes:
        try:
            body = json.loads(body_bytes)
        except Exception:
            pass

    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in SKIP_HEADERS
    }

    is_messages = path.rstrip("/") == "v1/messages"
    is_streaming = body.get("stream", False) if is_messages else False

    logger.info("REQUEST path=%s stream=%s model=%s", path, is_streaming, body.get("model"))

    # สร้าง session_id ต่อ 1 request และส่ง prompt ไป Worker ทันที
    session_id = str(uuid.uuid4())
    if is_messages and CF_WORKER_URL:
        prompt_text = extract_prompt_text(body.get("messages", []))
        asyncio.create_task(_push_prompt(session_id, prompt_text))

    if is_messages and is_streaming:
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        try:
            upstream_request = client.build_request(
                request.method,
                f"{UPSTREAM}/{path}",
                content=body_bytes,
                headers=fwd_headers,
                params=dict(request.query_params),
            )
            upstream_resp = await client.send(upstream_request, stream=True)
        except Exception as e:
            await client.aclose()
            logger.error("Upstream connect error: %s", e)
            return Response(content=json.dumps({"error": str(e)}), status_code=502,
                            media_type="application/json")

        logger.info("UPSTREAM status=%s", upstream_resp.status_code)

        resp_headers = {
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in SKIP_HEADERS
        }

        async def generate():
            raw = bytearray()
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    raw.extend(chunk)
                    yield chunk
                if raw:
                    try:
                        parse_sse_log(session_id, body, bytes(raw))
                    except Exception as e:
                        logger.error("parse_sse_log error: %s", e)
            except asyncio.CancelledError:
                logger.warning("Client disconnected during streaming")
                if raw:
                    try:
                        parse_sse_log(session_id, body, bytes(raw))
                    except Exception:
                        pass
                raise
            except Exception as e:
                logger.error("Streaming error: %s", e)
                err_event = json.dumps({"type": "error", "error": {"type": "proxy_error", "message": str(e)}})
                yield f"data: {err_event}\n\n".encode()
            finally:
                await upstream_resp.aclose()
                await client.aclose()

        return StreamingResponse(
            generate(),
            status_code=upstream_resp.status_code,
            media_type="text/event-stream",
            headers=resp_headers,
        )

    # Non-streaming
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        try:
            r = await client.request(
                request.method,
                f"{UPSTREAM}/{path}",
                content=body_bytes,
                headers=fwd_headers,
                params=dict(request.query_params),
            )
        except Exception as e:
            logger.error("Upstream request error: %s", e)
            return Response(content=json.dumps({"error": str(e)}), status_code=502,
                            media_type="application/json")

    logger.info("UPSTREAM status=%s", r.status_code)

    if is_messages and r.status_code == 200:
        try:
            resp_json = r.json()
            usage = resp_json.get("usage", {})
            content = resp_json.get("content", [])
            response_text = " ".join(
                c.get("text", "") for c in content if c.get("type") == "text"
            )
            write_log(session_id, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": body.get("model"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "prompt": extract_prompt_text(body.get("messages", [])),
                "response": response_text,
            })
        except Exception as e:
            logger.error("Log non-stream error: %s", e)

    resp_headers = {
        k: v for k, v in r.headers.items()
        if k.lower() not in SKIP_HEADERS
    }
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)
