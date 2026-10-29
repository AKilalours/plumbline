"""One runtime for every model call: live (call and record), replay (recorded outputs only), or off.

    PLUMBLINE_AI_MODE   live | replay | off. Default: live when a model is configured
                        (PLUMBLINE_LLM_MODEL and PLUMBLINE_LLM_API_KEY), else replay.

Recordings live in docs/results/ai/<task>.json: {"model": ..., "calls": {prompt_sha: output}}. Only
hashes of prompts are stored, never the key. A replay of a prompt that was never recorded raises
NotRecorded, so a replayed answer is always one the model really gave for exactly that prompt.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import numpy as np
from pydantic import BaseModel, ValidationError

from ..agents.llm import ChatModel, LLMError

ROOT = Path(__file__).resolve().parents[3]
RECORDINGS = Path(os.environ.get("PLUMBLINE_AI_RECORDINGS") or ROOT / "docs" / "results" / "ai")
T = TypeVar("T", bound=BaseModel)


class NotRecorded(LookupError):
    pass


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


@dataclass
class Trace:
    """What happened on one structured call: shown in the app and kept in eval results."""
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw: str = ""


class Runtime:
    _lock = threading.Lock()

    def __init__(self, model: ChatModel | None = None, mode: str | None = None, store: Path = RECORDINGS):
        self.model = model
        self.mode = mode or os.environ.get("PLUMBLINE_AI_MODE") or ("live" if model else "replay")
        if self.mode not in ("live", "replay", "off"):
            raise ValueError(f"PLUMBLINE_AI_MODE must be live, replay or off, not {self.mode!r}")
        if self.mode == "live" and model is None:
            raise ValueError("live mode needs PLUMBLINE_LLM_MODEL and PLUMBLINE_LLM_API_KEY")
        self.store = Path(store)
        self._files: dict[str, dict] = {}

    @classmethod
    def from_env(cls, store: Path = RECORDINGS) -> "Runtime":
        return cls(ChatModel.from_env(), store=store)

    @property
    def available(self) -> bool:
        return self.mode != "off"

    # ------------------------------------------------------------- recordings
    def _file(self, task: str) -> dict:
        if task not in self._files:
            p = self.store / f"{task}.json"
            self._files[task] = json.loads(p.read_text()) if p.exists() else {"model": None, "calls": {}}
        return self._files[task]

    def model_name(self, task: str) -> str | None:
        return self.model.model if self.mode == "live" else self._file(task)["model"]

    def recorded(self, task: str) -> int:
        return len(self._file(task)["calls"])

    def save(self) -> None:
        if self.mode != "live":
            return
        with self._lock:
            self.store.mkdir(parents=True, exist_ok=True)
            for task, data in self._files.items():
                p = self.store / f"{task}.json"
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=0, sort_keys=True))
                tmp.replace(p)

    # ------------------------------------------------------------- calls
    def complete(self, task: str, system: str, user: str, max_tokens: int = 600, json_mode: bool = False) -> dict:
        if self.mode == "off":
            raise NotRecorded("model features are off")
        f = self._file(task)
        name = self.model_name(task)
        if name is None:
            raise NotRecorded(f"no recordings for {task}; run `python -m plumbline.ai.evals` with a model")
        key = _sha(name, system, user, str(json_mode))
        if key in f["calls"]:
            return f["calls"][key]
        if self.mode != "live":
            raise NotRecorded(f"{task}: this exact prompt was never recorded")
        c = self.model.complete(system, user, max_tokens=max_tokens, json_mode=json_mode)
        rec = {"text": c.text, "in": c.input_tokens, "out": c.output_tokens}
        with self._lock:
            f["model"] = name
            f["calls"][key] = rec
        return rec

    def structured(self, task: str, system: str, user: str, schema: type[T], max_tokens: int = 800) -> tuple[T | None, Trace]:
        """Ask for one JSON object matching `schema`. If it does not parse or validate, show the model
        its own error once and ask again. Returns (None, trace) when both attempts fail."""
        spec = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        sys_ = f"{system}\n\nReply with one JSON object that matches this JSON Schema, and nothing else:\n{spec}"
        trace, prompt = Trace(), user
        for _ in range(2):
            trace.attempts += 1
            rec = self.complete(task, sys_, prompt, max_tokens=max_tokens, json_mode=True)
            trace.input_tokens += rec["in"]
            trace.output_tokens += rec["out"]
            trace.raw = rec["text"]
            try:
                return schema.model_validate_json(_strip_fence(rec["text"])), trace
            except ValidationError as e:
                msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
                trace.errors.append(msg)
                prompt = f"{user}\n\nYour previous reply was rejected: {msg}. Reply again with valid JSON only."
        return None, trace

    def embed(self, task: str, texts: list[str]) -> np.ndarray:
        """Unit-normalised embeddings, recorded per text."""
        if self.mode == "off":
            raise NotRecorded("model features are off")
        f = self._file(task)
        emb_model = os.environ.get("PLUMBLINE_EMBED_MODEL", "text-embedding-3-small")
        name = f"{emb_model}" if self.mode == "live" else f["model"]
        if name is None:
            raise NotRecorded(f"no recordings for {task}")
        keys = [_sha(name, t) for t in texts]
        missing = [t for t, k in zip(texts, keys) if k not in f["calls"]]
        if missing:
            if self.mode != "live":
                raise NotRecorded(f"{task}: {len(missing)} texts were never embedded")
            vecs = self.model.embed(missing, emb_model)
            with self._lock:
                f["model"] = name
                for t, v in zip(missing, vecs):
                    f["calls"][_sha(name, t)] = [round(float(x), 5) for x in v]
        m = np.array([f["calls"][k] for k in keys], dtype=np.float32)
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


__all__ = ["Runtime", "NotRecorded", "Trace", "LLMError", "RECORDINGS"]
