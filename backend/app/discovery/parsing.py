"""Strict parsing for structured Discovery agent responses."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)


class DiscoveryAgentResponseError(RuntimeError):
    """Raised when a Foundry agent does not satisfy a Discovery JSON contract."""


def _validation_summary(error: ValidationError) -> str:
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    summaries = [
        f"{'.'.join(str(part) for part in issue['loc']) or '<root>'}: {issue['msg']}"
        for issue in issues[:5]
    ]
    remaining = len(issues) - len(summaries)
    if remaining:
        summaries.append(f"{remaining} additional validation issue(s)")
    return "; ".join(summaries)


def parse_agent_response[ModelT: BaseModel](
    output_text: str, model_type: type[ModelT]
) -> ModelT:
    raw = output_text.strip()
    match = _JSON_FENCE.fullmatch(raw)
    if match:
        raw = match.group(1)
    try:
        payload = json.loads(raw)
        return model_type.model_validate(payload)
    except json.JSONDecodeError as exc:
        raise DiscoveryAgentResponseError(
            f"Discovery agent response was not valid JSON at line {exc.lineno}, "
            f"column {exc.colno}."
        ) from exc
    except ValidationError as exc:
        raise DiscoveryAgentResponseError(
            f"Discovery agent response did not match the required {model_type.__name__} "
            f"schema: {_validation_summary(exc)}."
        ) from exc