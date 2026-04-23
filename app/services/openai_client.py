from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


class OpenAIResponseError(RuntimeError):
    pass


class OpenAIRefusalError(OpenAIResponseError):
    pass


@dataclass
class OpenAIStructuredResult:
    parsed: dict[str, Any]
    raw_response: dict[str, Any]
    request_payload: dict[str, Any]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    latency_ms: int


class OpenAIResponsesClient:
    def __init__(self, *, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request_structured_json(
        self,
        *,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        schema_name: str,
        reasoning_effort: str,
        verbosity: str,
        max_output_tokens: int = 1600,
    ) -> OpenAIStructuredResult:
        payload = {
            "model": model,
            "store": False,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning": {"effort": reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "text": {
                "verbosity": verbosity,
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }

        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.text
            except Exception:
                detail = str(exc)
            raise OpenAIResponseError(detail) from exc

        raw = response.json()
        refusal = _extract_refusal(raw)
        if refusal:
            raise OpenAIRefusalError(refusal)

        content = _extract_output_text(raw)
        if not content:
            raise OpenAIResponseError("OpenAI response did not contain parseable text.")

        try:
            parsed = json.loads(_extract_json_fragment(content))
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError(
                f"Could not decode structured response as JSON. Raw content: {content}"
            ) from exc

        usage = raw.get("usage", {})
        return OpenAIStructuredResult(
            parsed=parsed,
            raw_response=raw,
            request_payload=payload,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=latency_ms,
        )


def _extract_refusal(raw: dict[str, Any]) -> Optional[str]:
    for output in raw.get("output", []):
        for item in output.get("content", []):
            if item.get("type") == "refusal":
                return item.get("refusal")
    return None


def _extract_output_text(raw: dict[str, Any]) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    candidates: list[str] = []
    for output in raw.get("output", []):
        if isinstance(output.get("content"), str):
            candidates.append(output["content"])
        for item in output.get("content", []):
            for key in ("text", "content", "value"):
                value = item.get(key)
                if isinstance(value, str):
                    candidates.append(value)
            nested_text = item.get("text")
            if isinstance(nested_text, dict) and isinstance(nested_text.get("value"), str):
                candidates.append(nested_text["value"])

    return next((candidate.strip() for candidate in candidates if candidate.strip()), "")


def _extract_json_fragment(content: str) -> str:
    content = content.strip()
    if content.startswith("{") and content.endswith("}"):
        return content
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return content[start : end + 1]
    return content
