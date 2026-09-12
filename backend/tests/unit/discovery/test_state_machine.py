from __future__ import annotations

import json
from collections import deque

import httpx
import pytest

from app.agents.models import AgentExecutionResult
from app.discovery.models import CostEstimate, PricingQuery
from app.discovery.pricing_service import AzureRetailPricingService
from app.discovery.repository import InMemoryDiscoveryCaseRepository
from app.discovery.service import DiscoveryService, DiscoveryStateConflictError
from app.repositories.session_repository import InMemorySessionRepository
from app.repositories.upload_repository import InMemoryUploadRepository
from app.services.session_service import SessionService


class _FakeOrchestrator:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = deque(json.dumps(output) for output in outputs)
        self.calls: list[str] = []

    async def execute_agent(self, **kwargs: object) -> AgentExecutionResult:
        prompt_id = str(kwargs["prompt_id"])
        self.calls.append(prompt_id)
        return AgentExecutionResult(
            agent_id=str(kwargs["agent_id"]),
            output_text=self.outputs.popleft(),
            correlation_id="trace-1",
        )


class _PricingService:
    async def estimate(self, queries: list[object]) -> CostEstimate:
        assert len(queries) == 1
        return CostEstimate(
            region="eastus",
            monthly_amount=42.5,
            annual_amount=510,
            coverage="complete",
            assumptions=["One unit per month"],
            source_urls=["https://prices.azure.com/api/retail/prices"],
        )


async def _create_service() -> tuple[DiscoveryService, _FakeOrchestrator, str]:
    outputs: list[dict[str, object]] = [
        {
            "personas": [
                {
                    "id": "claims-reviewer",
                    "name": "Claims Reviewer",
                    "description": "Operations specialist who resolves claims",
                    "pain_points": ["Manual document review"],
                    "evidence_references": ["call.txt: claims review"],
                    "confidence_score": 0.9,
                }
            ]
        },
        {
            "deep_dive_findings": ["Review latency is the primary constraint"],
            "gap_analysis": {
                "known_facts": ["Documents arrive digitally"],
                "information_gaps": ["Peak monthly volume"],
                "assumptions": [],
                "evidence_references": ["call.txt"],
                "confidence_score": 0.8,
            },
            "questions": [
                {
                    "id": "monthly-volume",
                    "text": "What is the peak monthly volume?",
                    "category": "capacity",
                    "status": "pending",
                    "evidence_references": [],
                }
            ],
        },
        {
            "recommendation": "Start with measured autoscaling and review after 30 days.",
            "evidence_references": ["Microsoft Well-Architected Framework"],
        },
        {
            "solutions": [
                {
                    "id": "document-intelligence",
                    "name": "Document intelligence workflow",
                    "summary": "Extract and review claim evidence.",
                    "requirements_text": "REQ-001: Review uploaded claims.",
                    "architecture_text": "Azure AI Foundry coordinates Azure AI Search.",
                    "architecture_nodes": [
                        {
                            "id": "foundry",
                            "service_name": "Azure AI Foundry",
                            "azure_icon_key": "azure ai foundry",
                            "purpose": "Claims agent",
                            "x": 0,
                            "y": 0,
                        }
                    ],
                    "architecture_edges": [],
                    "pros": ["Auditable"],
                    "cons": ["Requires evaluation"],
                    "ai_feasibility": "recommended",
                    "ai_feasibility_rationale": "The source documents are machine readable.",
                    "pricing_queries": [
                        {
                            "service_name": "Azure AI Search",
                            "arm_region_name": "eastus",
                            "sku_name": "Basic",
                            "units_per_month": 1,
                            "assumption": "One unit per month",
                        }
                    ],
                }
            ]
        },
    ]
    orchestrator = _FakeOrchestrator(outputs)
    repository = InMemoryDiscoveryCaseRepository()
    session_service = SessionService(
        orchestrator=object(),  # type: ignore[arg-type]
        session_repository=InMemorySessionRepository(),
        upload_repository=InMemoryUploadRepository(),
    )
    session = await session_service.create_session(owner_user_id="user-1", title="Discovery")
    upload = await session_service.register_upload(
        session_id=session.id,
        requesting_user_id="user-1",
        upload_type="transcript",
        file_name="call.txt",
        content_type="text/plain",
        size_bytes=10,
        transcript_text="Claims reviewers manually inspect every document.",
    )
    service = DiscoveryService(
        session_service=session_service,
        repository=repository,
        orchestrator=orchestrator,  # type: ignore[arg-type]
        pricing_service=_PricingService(),  # type: ignore[arg-type]
    )
    await service.create_or_resume(
        session_id=session.id,
        requesting_user_id="user-1",
        source_upload_ids=[upload.id],
    )
    return service, orchestrator, session.id


async def test_skip_requires_consent_before_recommendation_and_state_is_durable() -> None:
    service, orchestrator, session_id = await _create_service()

    case = await service.analyze_personas(
        session_id=session_id, requesting_user_id="user-1"
    )
    case = await service.select_persona(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_id="claims-reviewer",
    )
    case = await service.set_qa_mode(
        session_id=session_id,
        requesting_user_id="user-1",
        mode="interactive",
    )
    case = await service.answer_question(
        session_id=session_id,
        requesting_user_id="user-1",
        question_id="monthly-volume",
        answer=None,
    )

    assert case.questions[0].status == "recommendation_offered"
    assert case.questions[0].recommendation is None
    assert len(orchestrator.calls) == 2

    case = await service.respond_to_recommendation(
        session_id=session_id,
        requesting_user_id="user-1",
        question_id="monthly-volume",
        accepted=True,
    )
    assert case.status == "ready_for_solutions"
    assert case.questions[0].status == "recommended"
    assert len(orchestrator.calls) == 3

    case = await service.generate_solutions(
        session_id=session_id, requesting_user_id="user-1"
    )
    reloaded = await service.get_case(
        session_id=session_id, requesting_user_id="user-1"
    )
    assert reloaded == case
    assert reloaded.proposed_solutions[0].cost_estimate.monthly_amount == 42.5
    assert reloaded.proposed_solutions[0].cost_estimate.coverage == "complete"


async def test_pricing_without_queries_is_unavailable_not_synthetic() -> None:
    estimate = await AzureRetailPricingService(
        endpoint="https://prices.azure.com/api/retail/prices"
    ).estimate([])

    assert estimate.coverage == "unavailable"
    assert estimate.monthly_amount is None
    assert estimate.annual_amount is None
    assert estimate.source_urls == []


async def test_pricing_uses_verified_retail_unit_price() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "serviceName eq 'Azure AI Search'" in request.url.params["$filter"]
        return httpx.Response(200, json={"Items": [{"retailPrice": 2.5}]})

    service = AzureRetailPricingService(
        endpoint="https://prices.azure.com/api/retail/prices",
        transport=httpx.MockTransport(handler),
    )
    estimate = await service.estimate(
        [
            PricingQuery(
                service_name="Azure AI Search",
                arm_region_name="eastus",
                sku_name="Basic",
                units_per_month=10,
                assumption="Ten billable units per month",
            )
        ]
    )

    assert estimate.coverage == "complete"
    assert estimate.monthly_amount == 25
    assert estimate.annual_amount == 300
    assert estimate.source_urls[0].startswith("https://prices.azure.com/")


async def test_state_cannot_advance_out_of_order() -> None:
    service, _, session_id = await _create_service()

    with pytest.raises(DiscoveryStateConflictError):
        await service.generate_solutions(
            session_id=session_id,
            requesting_user_id="user-1",
        )