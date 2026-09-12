"""Strongly typed durable state for a customer Discovery case."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiscoveryStatus = Literal[
    "created",
    "analyzing_personas",
    "awaiting_persona_selection",
    "analyzing_persona",
    "awaiting_qa_mode",
    "questioning",
    "ready_for_solutions",
    "generating_solutions",
    "awaiting_solution_selection",
    "ready_to_prototype",
    "build_started",
    "failed",
]
DiscoveryQaMode = Literal["batch", "interactive"]
DiscoveryQuestionStatus = Literal[
    "pending",
    "answered",
    "recommendation_offered",
    "recommendation_declined",
    "recommended",
]
AiFeasibility = Literal["recommended", "feasible_with_tradeoffs", "not_feasible"]
PricingCoverage = Literal["complete", "partial", "unavailable"]

__all__ = [
    "AiFeasibility",
    "ArchitectureEdge",
    "ArchitectureNode",
    "CostEstimate",
    "DiscoveryCase",
    "DiscoveryQaMode",
    "DiscoveryQuestion",
    "DiscoveryQuestionStatus",
    "DiscoveryStatus",
    "GapAnalysis",
    "PersonaProfile",
    "ProposedSolution",
]


class PersonaProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    pain_points: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0, le=1)


class GapAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_facts: list[str] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0, le=1)


class DiscoveryQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: DiscoveryQuestionStatus = "pending"
    answer: str | None = None
    recommendation: str | None = None
    evidence_references: list[str] = Field(default_factory=list)


class ArchitectureNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    service_name: str = Field(min_length=1)
    azure_icon_key: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    x: float
    y: float


class ArchitectureEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    label: str | None = None


class CostEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    region: str = Field(min_length=1)
    monthly_amount: float | None = Field(default=None, ge=0)
    annual_amount: float | None = Field(default=None, ge=0)
    coverage: PricingCoverage
    assumptions: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    retrieved_at: datetime | None = None


class ProposedSolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    requirements_text: str = Field(min_length=1)
    architecture_text: str = Field(min_length=1)
    architecture_nodes: list[ArchitectureNode] = Field(default_factory=list)
    architecture_edges: list[ArchitectureEdge] = Field(default_factory=list)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    ai_feasibility: AiFeasibility
    ai_feasibility_rationale: str = Field(min_length=1)
    cost_estimate: CostEstimate


class DiscoveryCase(BaseModel):
    """Persisted snapshot for one resumable Discovery experience."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    status: DiscoveryStatus = "created"
    source_upload_ids: list[str] = Field(default_factory=list)
    analyzed_upload_ids: list[str] = Field(default_factory=list)
    analysis_revision: int = Field(default=0, ge=0)
    personas: list[PersonaProfile] = Field(default_factory=list)
    selected_persona_id: str | None = None
    deep_dive_findings: list[str] = Field(default_factory=list)
    gap_analysis: GapAnalysis | None = None
    qa_mode: DiscoveryQaMode | None = None
    questions: list[DiscoveryQuestion] = Field(default_factory=list)
    proposed_solutions: list[ProposedSolution] = Field(default_factory=list)
    selected_solution_id: str | None = None
    build_workflow_run_id: str | None = None
    last_error: str | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime