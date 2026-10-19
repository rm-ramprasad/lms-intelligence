"""Thin Bedrock Converse wrapper: fixed model, temperature 0, timeout, optional guardrail, token/cost
accounting. Injectable so tests use a fake runtime client (no AWS calls in unit tests)."""
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("bi_chat.llm")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "semantic" / "tables.yaml"


class LlmError(RuntimeError):
    """Any failure talking to Bedrock; callers fall back to the deterministic path."""


@dataclass
class LlmResponse:
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_use_id: str | None = None
    text: str = ""
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw_message: dict = field(default_factory=dict)

    @property
    def guardrail_blocked(self) -> bool:
        return self.stop_reason == "guardrail_intervened"


@dataclass(frozen=True)
class BedrockSettings:
    model_id: str
    model_label: str
    timeout_seconds: float
    max_tokens: int
    temperature: float
    price_in: float
    price_out: float
    guardrail_id: str | None
    guardrail_version: str
    enabled: bool = True

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "BedrockSettings":
        b = yaml.safe_load(path.read_text(encoding="utf-8"))["bedrock"]
        return cls(b["model_id"], b["model_label"], float(b["timeout_seconds"]), int(b["max_tokens"]),
                   float(b["temperature"]), float(b["price_per_1k_input"]), float(b["price_per_1k_output"]),
                   b.get("guardrail_id"), str(b.get("guardrail_version", "1")), bool(b.get("enabled", True)))


class BedrockClient:
    def __init__(self, runtime, settings: BedrockSettings, use_guardrail: bool = True):
        self.runtime = runtime
        self.s = settings
        self.use_guardrail = use_guardrail and bool(settings.guardrail_id)

    @classmethod
    def from_aws(cls, profile: str | None, region: str, settings: BedrockSettings) -> "BedrockClient":
        import boto3
        from botocore.config import Config
        cfg = Config(read_timeout=settings.timeout_seconds, connect_timeout=5, retries={"max_attempts": 2})
        session = boto3.Session(profile_name=profile, region_name=region)
        return cls(session.client("bedrock-runtime", config=cfg), settings)

    def cost(self, r: LlmResponse) -> float:
        return r.input_tokens / 1000 * self.s.price_in + r.output_tokens / 1000 * self.s.price_out

    def converse(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> LlmResponse:
        req = {
            "modelId": self.s.model_id,
            "system": [{"text": system}],
            "messages": messages,
            "inferenceConfig": {"maxTokens": self.s.max_tokens, "temperature": self.s.temperature},
        }
        if tools:
            req["toolConfig"] = {"tools": [{"toolSpec": t} for t in tools], "toolChoice": {"any": {}}}
        if self.use_guardrail:
            req["guardrailConfig"] = {"guardrailIdentifier": self.s.guardrail_id,
                                      "guardrailVersion": self.s.guardrail_version, "trace": "disabled"}
        started = time.monotonic()
        try:
            out = self.runtime.converse(**req)
        except Exception as e:  # botocore ClientError, timeouts, connection errors
            code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else type(e).__name__
            log.warning("bedrock converse failed code=%s", code)
            raise LlmError(str(code)) from e
        msg = out.get("output", {}).get("message", {}) or {}
        r = LlmResponse(stop_reason=out.get("stopReason", ""), raw_message=msg,
                        input_tokens=out.get("usage", {}).get("inputTokens", 0),
                        output_tokens=out.get("usage", {}).get("outputTokens", 0),
                        latency_ms=out.get("metrics", {}).get("latencyMs", int((time.monotonic() - started) * 1000)))
        for block in msg.get("content", []):
            if "toolUse" in block and r.tool_name is None:
                tu = block["toolUse"]
                r.tool_name, r.tool_use_id = tu.get("name"), tu.get("toolUseId")
                inp = tu.get("input")
                r.tool_input = json.loads(inp) if isinstance(inp, str) else (inp or {})
            elif "text" in block:
                r.text += block["text"]
        log.info("bedrock converse model=%s stop=%s in=%s out=%s ms=%s guardrail=%s", self.s.model_id,
                 r.stop_reason, r.input_tokens, r.output_tokens, r.latency_ms, self.use_guardrail)
        return r
