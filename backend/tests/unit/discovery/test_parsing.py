from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from app.discovery.parsing import DiscoveryAgentResponseError, parse_agent_response


class _CountEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int


def test_schema_error_reports_field_without_rejected_customer_value() -> None:
    with pytest.raises(DiscoveryAgentResponseError) as caught:
        parse_agent_response('{"count":"private customer content"}', _CountEnvelope)

    message = str(caught.value)
    assert "count: Input should be a valid integer" in message
    assert "private customer content" not in message


def test_json_error_reports_location_without_response_content() -> None:
    with pytest.raises(DiscoveryAgentResponseError) as caught:
        parse_agent_response('{"private customer content"', _CountEnvelope)

    message = str(caught.value)
    assert "not valid JSON at line 1" in message
    assert "private customer content" not in message