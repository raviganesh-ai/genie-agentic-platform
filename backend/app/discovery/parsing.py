"""Strict parsing for structured Discovery agent responses."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)


class DiscoveryAgentResponseError(RuntimeError):
    """Raised when a Foundry agent does not satisfy a Discovery JSON contract."""


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
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DiscoveryAgentResponseError(
            f"Discovery agent response did not match the required {model_type.__name__} schema."
        ) from exc