"""
LLM provider interface with DeepSeek thinking-mode support.

Defaults to DeepSeek (OpenAI-compatible API). Override via env vars:
    LLM_PROVIDER=deepseek | openai | none
    LLM_API_KEY=sk-xxx
    LLM_BASE_URL=https://api.deepseek.com/v1

    # Complex tier (default: deep reasoning, thinking ON)
    LLM_COMPLEX_MODEL=deepseek-v4-pro
    LLM_COMPLEX_THINKING=enabled            # enabled | disabled
    LLM_COMPLEX_REASONING_EFFORT=high       # high | max

    # Simple tier (default: fast classification, thinking OFF)
    LLM_SIMPLE_MODEL=deepseek-v4-flash
    LLM_SIMPLE_THINKING=disabled
    LLM_SIMPLE_TEMPERATURE=0.0

    LLM_EMBEDDING_MODEL=text-embedding-3-small

Per DeepSeek docs (https://api-docs.deepseek.com/zh-cn/guides/thinking_mode):
- Thinking mode is enabled by `extra_body={"thinking": {"type": "enabled"}}`.
- In thinking mode, `temperature`, `top_p`, `presence_penalty`, `frequency_penalty`
  are silently ignored, so we skip them.
- `reasoning_effort` ∈ {"high", "max"} controls thinking depth.
- The chain of thought comes back at `choices[0].message.reasoning_content`.
"""

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


def _envbool_thinking(value: str) -> bool:
    return value.strip().lower() in ("enabled", "true", "on", "1", "yes")


@dataclass
class ModelTier:
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.0
    thinking_enabled: bool = False
    reasoning_effort: Optional[str] = None  # "high" | "max" | None

    def describe(self) -> str:
        bits = [self.model]
        if self.thinking_enabled:
            bits.append(f"thinking=on(effort={self.reasoning_effort or 'default'})")
        else:
            bits.append(f"thinking=off,temp={self.temperature}")
        return " ".join(bits)


@dataclass
class LLMConfig:
    provider: str = "none"
    api_key: str = ""
    base_url: Optional[str] = None

    complex_tier: ModelTier = field(default_factory=lambda: ModelTier(
        model="deepseek-v4-pro",
        max_tokens=8192,
        temperature=0.0,
        thinking_enabled=True,
        reasoning_effort="high",
    ))
    simple_tier: ModelTier = field(default_factory=lambda: ModelTier(
        model="deepseek-v4-flash",
        max_tokens=4096,
        temperature=0.0,
        thinking_enabled=False,
        reasoning_effort=None,
    ))
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: Optional[int] = 1024
    embedding_batch_size: int = 10

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = os.environ.get("LLM_PROVIDER", "none").lower()
        default_base = "https://api.deepseek.com/v1" if provider == "deepseek" else None
        base_url = os.environ.get("LLM_BASE_URL", default_base)

        complex_thinking = _envbool_thinking(
            os.environ.get("LLM_COMPLEX_THINKING", "enabled")
        )
        simple_thinking = _envbool_thinking(
            os.environ.get("LLM_SIMPLE_THINKING", "disabled")
        )

        return cls(
            provider=provider,
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=base_url,
            complex_tier=ModelTier(
                model=os.environ.get("LLM_COMPLEX_MODEL", "deepseek-v4-pro"),
                max_tokens=int(os.environ.get("LLM_COMPLEX_MAX_TOKENS", "8192")),
                temperature=float(os.environ.get("LLM_COMPLEX_TEMPERATURE", "0.0")),
                thinking_enabled=complex_thinking,
                reasoning_effort=(os.environ.get("LLM_COMPLEX_REASONING_EFFORT", "high")
                                  if complex_thinking else None),
            ),
            simple_tier=ModelTier(
                model=os.environ.get("LLM_SIMPLE_MODEL", "deepseek-v4-flash"),
                max_tokens=int(os.environ.get("LLM_SIMPLE_MAX_TOKENS", "4096")),
                temperature=float(os.environ.get("LLM_SIMPLE_TEMPERATURE", "0.0")),
                thinking_enabled=simple_thinking,
                reasoning_effort=(os.environ.get("LLM_SIMPLE_REASONING_EFFORT", "high")
                                  if simple_thinking else None),
            ),
            embedding_model=os.environ.get("LLM_EMBEDDING_MODEL", "text-embedding-v4"),
            embedding_dimensions=(int(os.environ["LLM_EMBEDDING_DIMENSIONS"])
                                  if os.environ.get("LLM_EMBEDDING_DIMENSIONS") else 1024),
            embedding_batch_size=int(os.environ.get("LLM_EMBEDDING_BATCH_SIZE", "10")),
        )


class LLMProvider(ABC):

    @abstractmethod
    def complete(self, prompt: str, *, system: Optional[str] = None,
                 tier: str = "complex") -> str:
        ...

    @abstractmethod
    def complete_json(self, prompt: str, *, system: Optional[str] = None,
                      tier: str = "complex") -> dict:
        ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    @property
    def last_reasoning(self) -> str:
        return ""


class NoOpProvider(LLMProvider):

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 tier: str = "complex") -> str:
        return ""

    def complete_json(self, prompt: str, *, system: Optional[str] = None,
                      tier: str = "complex") -> dict:
        return {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class OpenAICompatibleProvider(LLMProvider):
    """Works with OpenAI-compatible Chat Completions endpoints (OpenAI, DeepSeek).

    Handles DeepSeek thinking mode: when a tier has `thinking_enabled=True`,
    we set `extra_body={"thinking": {"type": "enabled"}}`, optionally pass
    `reasoning_effort`, and skip temperature (silently ignored in thinking
    mode per DeepSeek docs).
    """

    def __init__(self, config: LLMConfig):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "openai package not installed. Run: pip install 'openai>=1.0'"
            ) from e

        if not config.api_key:
            # Surface the workspace path and config file location so users
            # know exactly where to put the key.
            try:
                from config import get_config
                cfg_path = get_config().config_file_path()
                hint = (
                    f"\n\n  Edit your config file and set the api_key:\n"
                    f"    {cfg_path}\n\n"
                    f"  Under the [llm] section:\n"
                    f"    [llm]\n"
                    f'    api_key = "sk-YOUR_KEY_HERE"\n\n'
                    f"  Or run `paperdb init` if the config doesn't exist yet."
                )
            except Exception:
                hint = "\n  (Set the LLM_API_KEY env var or paste into config.toml under [llm].)"
            raise ValueError(f"LLM_API_KEY is required for OpenAICompatibleProvider.{hint}")

        self._config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._last_reasoning: str = ""

        # Embedding endpoint can differ from chat endpoint. Defaults route to
        # Alibaba ModelStudio (Qwen text-embedding-v4) which is OpenAI-compatible.
        embed_base = os.environ.get(
            "LLM_EMBEDDING_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
        embed_key = os.environ.get("LLM_EMBEDDING_API_KEY", config.api_key)
        if embed_base and embed_base != config.base_url:
            self._embed_client = OpenAI(api_key=embed_key, base_url=embed_base)
        else:
            self._embed_client = self._client

    def _tier(self, tier: str) -> ModelTier:
        return self._config.complex_tier if tier == "complex" else self._config.simple_tier

    @property
    def last_reasoning(self) -> str:
        return self._last_reasoning

    def _chat(self, prompt: str, system: Optional[str], tier: str,
              response_format: Optional[dict] = None) -> str:
        t = self._tier(tier)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = dict(
            model=t.model,
            messages=messages,
            max_tokens=t.max_tokens,
        )
        if response_format:
            kwargs["response_format"] = response_format

        # Only DeepSeek (and openai-compatible) understand `extra_body.thinking`.
        is_deepseek_like = self._config.provider in ("deepseek", "openai-compatible")
        extra_body: dict = {}
        if t.thinking_enabled:
            if is_deepseek_like:
                extra_body["thinking"] = {"type": "enabled"}
            if t.reasoning_effort:
                kwargs["reasoning_effort"] = t.reasoning_effort
            # NOTE: skip temperature/top_p — DeepSeek silently ignores them.
        else:
            if is_deepseek_like:
                # DeepSeek default is enabled; pass disabled explicitly to keep
                # cost / latency predictable on the simple tier.
                extra_body["thinking"] = {"type": "disabled"}
            kwargs["temperature"] = t.temperature
            if t.reasoning_effort:
                kwargs["reasoning_effort"] = t.reasoning_effort

        if extra_body:
            kwargs["extra_body"] = extra_body

        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                self._last_reasoning = getattr(msg, "reasoning_content", "") or ""
                return (msg.content or "").strip()
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after 3 attempts: {last_err}")

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 tier: str = "complex") -> str:
        return self._chat(prompt, system, tier)

    def complete_json(self, prompt: str, *, system: Optional[str] = None,
                      tier: str = "complex") -> dict:
        sys_with_json = (system or "") + (
            "\n\nRespond ONLY with a valid JSON object. No prose, no code fences."
        )
        text = self._chat(
            prompt, sys_with_json.strip(), tier,
            response_format={"type": "json_object"},
        )
        return _safe_json_parse(text)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts.

        Handles two constraints automatically:
          - Qwen text-embedding-v4 caps batch at 10 → slice and concat.
          - Empty strings break some providers → coerce to single space.
        """
        if not texts:
            return []

        sanitized = [t if (t and t.strip()) else " " for t in texts]
        batch_size = max(1, self._config.embedding_batch_size or 10)

        out: list[list[float]] = []
        for i in range(0, len(sanitized), batch_size):
            chunk = sanitized[i:i + batch_size]
            kwargs: dict = dict(
                model=self._config.embedding_model,
                input=chunk,
            )
            if self._config.embedding_dimensions:
                kwargs["dimensions"] = self._config.embedding_dimensions
            resp = self._embed_client.embeddings.create(**kwargs)
            out.extend(d.embedding for d in resp.data)
        return out


def _safe_json_parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise


def get_llm_provider(config: Optional[LLMConfig] = None) -> LLMProvider:
    cfg = config or LLMConfig.from_env()
    if cfg.provider in ("none", ""):
        return NoOpProvider()
    if cfg.provider in ("deepseek", "openai", "openai-compatible"):
        if not cfg.api_key:
            raise RuntimeError(
                f"LLM_PROVIDER={cfg.provider} but LLM_API_KEY is empty. "
                "Set LLM_API_KEY in the environment."
            )
        return OpenAICompatibleProvider(cfg)
    raise NotImplementedError(
        f"Provider '{cfg.provider}' not yet implemented. "
        "Supported: none, deepseek, openai, openai-compatible."
    )
