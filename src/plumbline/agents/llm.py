"""One small, provider-neutral model client: any endpoint that speaks the OpenAI-compatible
chat-completions protocol. Model name, endpoint and key come from the environment, never from the
repository, so nothing about a vendor is hard-coded and tests never need a key.

    PLUMBLINE_LLM_MODEL       required to enable the model; without it the agent runs offline
    PLUMBLINE_LLM_API_KEY     sent as a bearer token; never logged, never written
    PLUMBLINE_LLM_BASE_URL    default https://api.openai.com/v1
    PLUMBLINE_LLM_COST_IN / _COST_OUT   optional price per 1,000 tokens, for the usage report
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    cost: float | None


class LLMError(RuntimeError):
    pass


class ChatModel:
    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1",
                 cost_in: float | None = None, cost_out: float | None = None, timeout: float = 60,
                 max_retries: int = 4, sleep: Callable[[float], None] = time.sleep,
                 opener: Callable | None = None):
        if not api_key:
            raise LLMError("no API key")
        self.model, self._key, self.base_url = model, api_key, base_url.rstrip("/")
        self.cost_in, self.cost_out, self.timeout = cost_in, cost_out, timeout
        self.max_retries, self.sleep = max_retries, sleep
        self._open = opener or urllib.request.urlopen

    def __repr__(self) -> str:
        return f"ChatModel({self.model!r}, base_url={self.base_url!r}, key=***)"

    @classmethod
    def from_env(cls) -> "ChatModel | None":
        model, key = os.environ.get("PLUMBLINE_LLM_MODEL"), os.environ.get("PLUMBLINE_LLM_API_KEY")
        if not model or not key:
            return None
        f = lambda k: float(os.environ[k]) if os.environ.get(k) else None
        return cls(model, key, os.environ.get("PLUMBLINE_LLM_BASE_URL", "https://api.openai.com/v1"),
                   f("PLUMBLINE_LLM_COST_IN"), f("PLUMBLINE_LLM_COST_OUT"))

    def complete(self, system: str, user: str, max_tokens: int = 500) -> Completion:
        body = json.dumps({"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
            try:
                with self._open(req, timeout=self.timeout) as r:
                    d = json.loads(r.read())
                u = d.get("usage") or {}
                i, o = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
                cost = (i * self.cost_in + o * self.cost_out) / 1000 if self.cost_in is not None and self.cost_out is not None else None
                return Completion(d["choices"][0]["message"]["content"] or "", i, o, cost)
            except urllib.error.HTTPError as e:
                if e.code in (400, 401, 403, 404):
                    raise LLMError(f"model endpoint refused the request (HTTP {e.code})") from None
                wait = float(e.headers.get("Retry-After") or 2 ** attempt) if e.code == 429 else 2 ** attempt
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
                wait = 2 ** attempt
            if attempt == self.max_retries:
                raise LLMError("model endpoint unavailable after retries")
            self.sleep(wait)
        raise AssertionError("unreachable")
