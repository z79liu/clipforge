"""Agent runner: loads a specialist's prompt file, calls Claude, validates JSON against its schema, retries on failure."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import BaseModel, ValidationError

from .schemas import SCHEMAS

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5-5",
}


class AgentSpec(BaseModel):
    name: str
    model: str
    max_tokens: int = 4000
    temperature: float = 0.3
    system: str


def load_agent(name: str) -> AgentSpec:
    text = (AGENTS_DIR / f"{name}.md").read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError(f"agents/{name}.md needs YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    model = MODEL_ALIASES.get(meta.get("model", "sonnet"), meta.get("model"))
    return AgentSpec(name=name, model=model, max_tokens=meta.get("max_tokens", 4000),
                     temperature=meta.get("temperature", 0.3), system=m.group(2).strip())


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return json.loads(text[start:])


class AgentRunner:
    """Calls one specialist per `run`. Pass `mock` (name, payload) -> dict for offline runs/tests."""

    def __init__(self, mock: Optional[Callable[[str, dict], dict]] = None, max_retries: int = 2,
                 exemplars: Optional[dict] = None, log_dir: Optional[Path] = None):
        self.mock = mock
        self.max_retries = max_retries
        self.exemplars = exemplars or {}
        self.log_dir = log_dir
        self._client = None
        if mock is None:
            import anthropic  # noqa: WPS433
            self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def run(self, name: str, payload: dict) -> BaseModel:
        schema = SCHEMAS[name]
        spec = load_agent(name)
        if self.mock:
            out = schema.model_validate(self.mock(name, payload))
            self._log(name, payload, out)
            return out

        system = spec.system
        if name in self.exemplars:
            system += "\n\n## Calibration examples from your own past results\n" + self.exemplars[name]
        system += ("\n\n## Output contract\nReturn ONLY one JSON object matching this JSON Schema. No prose.\n"
                   + json.dumps(schema.model_json_schema()))

        messages = [{"role": "user", "content": [
            # transcript-heavy payloads are cached so critic/transform re-reads are cheap
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False),
             "cache_control": {"type": "ephemeral"}}]}]
        last_err = None
        for _ in range(self.max_retries + 1):
            resp = self._client.messages.create(model=spec.model, max_tokens=spec.max_tokens,
                                                temperature=spec.temperature, system=system,
                                                messages=messages)
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            try:
                out = schema.model_validate(_extract_json(text))
                self._log(name, payload, out)
                return out
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_err = e
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"That failed schema validation:\n{e}\nReturn corrected JSON only."}]
        raise RuntimeError(f"{name} failed validation after retries: {last_err}")

    def _log(self, name: str, payload: dict, out: BaseModel) -> None:
        if not self.log_dir:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_dir / f"{name}.jsonl", "a") as f:
            f.write(json.dumps({"agent": name, "output": out.model_dump()}) + "\n")
