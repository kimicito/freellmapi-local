#!/usr/bin/env python3
"""GitHub Models -> OpenAI JSON shim.

GitHub Models (models.github.ai/inference) free tier может отдавать ответы
с content-type: text/plain (сырой текст вместо chat.completion JSON).
Этот shim нормализует: если тело — валидный chat.completion JSON, пропускает
как есть; иначе оборачивает текст в корректный OpenAI-ответ.

Использование: freellmapi custom provider -> http://127.0.0.1:8300/v1
Ключ: любой (shim подставляет GITHUB_PAT из env или файла).
"""
import json, os, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("SHIM_PORT", "8300"))
GH_URL = "https://models.github.ai/inference/chat/completions"
PAT_FILE = "/root/.openclaw/secrets/github-pat.txt"
TOKEN = os.environ.get("GITHUB_PAT") or open(PAT_FILE).read().strip()


def call_github(body: bytes) -> tuple[int, str, str]:
    req = urllib.request.Request(
        GH_URL, data=body,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")


def wrap_as_chat_completion(text: str, model: str, req_body: dict) -> dict:
    msgs = req_body.get("messages", [])
    prompt_chars = sum(len(str(m.get("content", ""))) for m in msgs)
    return {
        "id": "chatcmpl-shim",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": max(1, prompt_chars // 4),
            "completion_tokens": max(1, len(text) // 4),
            "total_tokens": max(1, prompt_chars // 4) + max(1, len(text) // 4),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [
                {"id": "openai/gpt-4o-mini", "object": "model"},
                {"id": "openai/gpt-4o", "object": "model"},
            ]})
        else:
            self._send(200, {"status": "ok", "shim": "ghmodels"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "only /v1/chat/completions and /v1/models"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req_body = json.loads(body)
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return
        status, ctype, text = call_github(body)
        if status != 200:
            self._send(502, {"error": {"message": f"github models http {status}: {text[:300]}"}})
            return
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("choices"):
                self._send(200, parsed)
                return
        except json.JSONDecodeError:
            pass
        self._send(200, wrap_as_chat_completion(text, req_body.get("model", "unknown"), req_body))


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
