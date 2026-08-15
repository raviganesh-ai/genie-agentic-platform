"""Reanalysis domain model.

Implements the "REANALYSIS" requirement for Phase 6: a customer/agent may
challenge a recommendation, modify priorities, or request an alternative
architecture (lower cost, higher security, MVP, or Fabric-first redesign).
``app.orchestration.reanalysis_service.ReanalysisService`` routes each
request to an appropriate registered agent; it never performs the
redesign reasoning itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReanalysisRequestType = Literal[
    "challenge_recommendation",
    "modify_priorities",
    "request_alternative_architecture",
    "lower_cost_redesign",
    "higher_security_redesign",
    "mvp_redesign",
    "fabric_first_redesign",
]

ReanalysisStatus = Literal["routed", "failed"]

__all__ = ["ReanalysisRequest", "ReanalysisRequestType", "ReanalysisResult", "ReanalysisStatus"]


class ReanalysisRequest(BaseModel):
    """A single request to challenge or redesign a prior recommendation."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    request_type: ReanalysisRequestType
    target_recommendation_id: str | None = None
    rationale: str = ""
    requested_at: datetime


class ReanalysisResult(BaseModel):
    """The routing outcome for a single ``ReanalysisRequest``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    reanalysis_request_id: str = Field(min_length=1)
    routed_to_agent_id: str | None = None
    status: ReanalysisStatus
    detail: str = ""
    created_at: datetime
