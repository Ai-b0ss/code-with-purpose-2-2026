"""LangChain transport for OpenAI-compatible chat providers.

The public ``complete`` contract matches the legacy test adapter.  The
application selects this implementation for configured live evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from urllib.parse import urlsplit
from typing import Callable, TypeVar

import httpx
from jsonschema import ValidationError as JsonSchemaError, validate as validate_schema
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError as PydanticValidationError

T = TypeVar("T")


@dataclass(frozen=True)
class LangChainChatAdapter:
    endpoint: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 30.0
    json_mode: bool = True

    def __post_init__(self) -> None:
        # Import here to avoid an evaluator import cycle.
        from backend.evaluator import EvaluationError
        from math import isfinite

        parsed = urlsplit(self.endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.endswith("/chat/completions")
                or not self.model.strip() or not isfinite(self.timeout)
                or not 0 < self.timeout <= 300):
            raise EvaluationError("invalid_configuration")

    def complete(self, *, system: str, user: str, schema: dict) -> str:
        from backend.evaluator import EvaluationError

        base_url = self.endpoint.removesuffix("/chat/completions")
        # Keep redirects off: candidate answers and credentials must stay on
        # the configured host. Disable SDK retries; the graph owns retry policy.
        client = httpx.Client(follow_redirects=False, trust_env=True)
        try:
            model = ChatOpenAI(
                model=self.model,
                api_key=self.api_key or "local-no-key",
                base_url=base_url,
                timeout=self.timeout,
                temperature=0,
                max_retries=0,
                http_client=client,
                model_kwargs=(
                    {"response_format": {"type": "json_object"}}
                    if self.json_mode else {}
                ),
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", "{system}"), ("human", "{user}"),
            ])
            response = (prompt | model).invoke({"system": system, "user": user})
            if response.response_metadata.get("finish_reason") not in (None, "stop"):
                raise EvaluationError("incomplete_response")
            if not isinstance(response.content, str) or len(response.content) > 1_000_000:
                raise EvaluationError("invalid_provider_response")
            return response.content
        except APIStatusError as exc:
            code = exc.status_code
            if isinstance(code, int) and 100 <= code <= 599:
                raise EvaluationError(f"provider_http_{code}") from None
            raise EvaluationError("provider_http_error") from None
        except APITimeoutError:
            raise EvaluationError("provider_timeout") from None
        except APIConnectionError:
            raise EvaluationError("provider_network_error") from None
        finally:
            client.close()


@dataclass
class FailoverChatAdapter:
    """Try configured cloud models, then an explicitly configured local model."""

    primary: LangChainChatAdapter
    backups: tuple[LangChainChatAdapter, ...] = ()
    local: LangChainChatAdapter | None = None
    last_model: str | None = field(default=None, init=False)
    attempted_models: list[str] = field(default_factory=list, init=False, repr=False)
    failed_attempts: list[dict[str, str | int]] = field(default_factory=list, init=False, repr=False)

    def _record_failure(self, model: str, code: str) -> None:
        existing = next((item for item in self.failed_attempts
                         if item["model"] == model and item["code"] == code), None)
        if existing is None:
            self.failed_attempts.append({"model": model, "code": code, "count": 1})
        else:
            existing["count"] += 1
        logging.getLogger(__name__).warning("llm_failover model=%s code=%s", model, code)

    def complete(self, *, system: str, user: str, schema: dict) -> str:
        return self.complete_validated(system=system, user=user, schema=schema, validator=lambda raw: raw)

    def complete_validated(self, *, system: str, user: str, schema: dict,
                           validator: Callable[[str], T],
                           validate_raw_schema: bool = True) -> T:
        from backend.evaluator import EvaluationError

        last: EvaluationError | None = None
        for adapter in (self.primary, *self.backups, *([self.local] if self.local else [])):
            model_name = getattr(adapter, "model", "test_adapter")
            if model_name not in self.attempted_models:
                self.attempted_models.append(model_name)
            try:
                content = adapter.complete(system=system, user=user, schema=schema)
                stripped = content.strip()
                if stripped.startswith("```json") and stripped.endswith("```"):
                    stripped = stripped[7:-3].strip()
                decoded = json.loads(stripped)
                if validate_raw_schema:
                    validate_schema(instance=decoded, schema=schema)
                result = validator(stripped)
                self.last_model = model_name
                return result
            except (json.JSONDecodeError, JsonSchemaError, PydanticValidationError, ValueError, TypeError) as exc:
                last = EvaluationError("invalid_model_json", (type(exc).__name__,))
                self._record_failure(model_name, last.code)
            except EvaluationError as exc:
                last = exc
                self._record_failure(model_name, exc.code)
        raise last or EvaluationError("invalid_configuration")
