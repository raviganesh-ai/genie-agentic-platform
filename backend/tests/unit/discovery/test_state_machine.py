from __future__ import annotations

import json
from collections import deque

import httpx
import pytest

from app.agents.models import AgentExecutionResult
from app.discovery.models import CostEstimate, PricingQuery
from app.discovery.parsing import DiscoveryAgentResponseError
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
        self.execution_requests: list[dict[str, object]] = []

    async def execute_agent(self, **kwargs: object) -> AgentExecutionResult:
        prompt_id = str(kwargs["prompt_id"])
        self.calls.append(prompt_id)
        self.execution_requests.append(kwargs)
        return AgentExecutionResult(
            agent_id=str(kwargs["agent_id"]),
            output_text=self.outputs.popleft(),
            correlation_id="trace-1",
        )


class _PricingService:
    def __init__(self) -> None:
        self.calls = 0

    async def estimate(self, queries: list[object]) -> CostEstimate:
        assert len(queries) == 1
        self.calls += 1
        return CostEstimate(
            region="eastus",
            monthly_amount=42.5,
            annual_amount=510,
            coverage="complete",
            assumptions=["One unit per month"],
            source_urls=["https://prices.azure.com/api/retail/prices"],
        )


async def _create_service(
    *,
    extracted_name: str = "Jordan Lee",
    transcript_text: str = "Jordan Lee is a claims reviewer who manually inspects every document.",
) -> tuple[DiscoveryService, _FakeOrchestrator, str]:
    outputs: list[dict[str, object]] = [
        {
            "people": [{"name": extracted_name}]
        },
        {
            "deep_dive_findings": ["Review latency is the primary constraint"],
            "insight_sections": [
                {
                    "title": "Manual review constrains response time",
                    "summary": (
                        "Jordan Lee's manual evidence review is the main source of delay, "
                        "putting the stated response target at risk as volume grows."
                    ),
                    "evidence_references": ["call.txt"],
                }
            ],
            "gap_summary": (
                "Peak monthly volume remains unknown, preventing confident capacity sizing."
            ),
            "assumption_summary": (
                "The analysis assumes documents continue to arrive digitally; the customer "
                "must validate that all intake channels follow this pattern."
            ),
            "analysis_summary": "Additional model metadata must not invalidate the response.",
            "gap_analysis": {
                "known_facts": ["Documents arrive digitally"],
                "risks": ["Manual review can miss time-sensitive claims"],
                "contradictions": ["The target response time conflicts with manual review"],
                "information_gaps": ["Peak monthly volume"],
                "assumptions": [],
                "evidence_references": ["call.txt"],
                "confidence_score": 0.8,
                "priority": "high",
            },
            "questions": [
                {
                    "id": "monthly-volume",
                    "text": "What is the peak monthly volume?",
                    "category": "capacity",
                    "suggested_answers": [
                        "Fewer than 10,000 documents",
                        "10,000 to 100,000 documents",
                        "More than 100,000 documents",
                    ],
                    "status": "pending",
                    "evidence_references": [],
                    "rationale": "Capacity affects the recommended Azure sizing.",
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
                    "requirements_text": "[REQ-001] " + ("Review uploaded claims. " * 200),
                    "architecture_text": "## Single-Page UI Design\n" + ("Evidence zone. " * 320),
                    "architecture_nodes": [
                        {
                            "id": "foundry",
                            "service_name": "Azure AI Search",
                            "azure_icon_key": "azure ai search",
                            "purpose": "Grounds the claims agent in approved evidence",
                            "x": 0,
                            "y": 0,
                        }
                    ],
                    "architecture_edges": [],
                    "pros": ["Auditable"],
                    "cons": ["Requires evaluation"],
                    "ai_feasibility": "recommended",
                    "ai_feasibility_rationale": "The source documents are machine readable.",
                    "Evidence_references": ["call.txt: manual document review"],
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
        transcript_text=transcript_text,
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


async def test_persona_extraction_rejects_role_archetype_not_named_in_evidence() -> None:
    service, _, session_id = await _create_service(extracted_name="Operations Manager")

    with pytest.raises(
        DiscoveryStateConflictError,
        match="not explicitly named people in the evidence: Operations Manager",
    ):
        await service.analyze_personas(
            session_id=session_id,
            requesting_user_id="user-1",
        )

    reloaded = await service.get_case(
        session_id=session_id,
        requesting_user_id="user-1",
    )
    assert reloaded.status == "created"
    assert reloaded.personas == []
    assert reloaded.last_error is not None
    assert "Operations Manager" in reloaded.last_error


async def test_find_people_persists_only_unique_names_before_selection() -> None:
    service, _, session_id = await _create_service()

    case = await service.analyze_personas(
        session_id=session_id,
        requesting_user_id="user-1",
    )

    assert case.status == "awaiting_persona_selection"
    assert len(case.personas) == 1
    assert case.personas[0].model_dump() == {
        "id": "jordan-lee",
        "name": "Jordan Lee",
        "role_or_context": None,
        "description": None,
        "pain_points": [],
        "evidence_references": [],
        "confidence_score": None,
    }


async def test_find_people_accepts_prior_personas_shape_but_discards_analysis() -> None:
    service, orchestrator, session_id = await _create_service()
    orchestrator.outputs[0] = json.dumps(
        {
            "personas": [
                {
                    "id": "model-generated-id",
                    "name": "Jordan Lee",
                    "description": "Premature analysis",
                    "pain_points": ["Premature pain point"],
                }
            ]
        }
    )

    case = await service.analyze_personas(
        session_id=session_id,
        requesting_user_id="user-1",
    )

    assert case.personas[0].id == "jordan-lee"
    assert case.personas[0].description is None
    assert case.personas[0].pain_points == []


async def test_deep_dive_accepts_multiple_people_and_extra_agent_metadata() -> None:
    service, orchestrator, session_id = await _create_service(
        transcript_text=(
            "Jordan Lee manually inspects every document. "
            "Morgan Chen reports that claim handoffs are frequently delayed."
        )
    )
    orchestrator.outputs[0] = json.dumps(
        {"people": [{"name": "Jordan Lee"}, {"name": "Morgan Chen"}]}
    )

    await service.analyze_personas(
        session_id=session_id,
        requesting_user_id="user-1",
    )
    case = await service.select_personas(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_ids=["jordan-lee", "morgan-chen"],
    )

    assert case.selected_persona_id == "jordan-lee"
    assert case.selected_persona_ids == ["jordan-lee", "morgan-chen"]
    assert case.insight_sections[0].title == "Manual review constrains response time"
    assert "Peak monthly volume" in case.gap_summary
    assert "assumes documents continue" in case.assumption_summary
    assert case.questions[0].suggested_answers == [
        "Fewer than 10,000 documents",
        "10,000 to 100,000 documents",
        "More than 100,000 documents",
    ]
    assert case.status == "awaiting_qa_mode"


async def test_deep_dive_without_material_questions_is_ready_for_solutions() -> None:
    service, orchestrator, session_id = await _create_service()
    await service.analyze_personas(
        session_id=session_id,
        requesting_user_id="user-1",
    )
    deep_dive = json.loads(orchestrator.outputs[0])
    deep_dive["questions"] = []
    orchestrator.outputs[0] = json.dumps(deep_dive)

    case = await service.select_personas(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_ids=["jordan-lee"],
    )

    assert case.questions == []
    assert case.qa_mode is None
    assert case.status == "ready_for_solutions"


async def test_deep_dive_retries_once_after_truncated_json() -> None:
    service, orchestrator, session_id = await _create_service()
    orchestrator.outputs.insert(1, '{"deep_dive_findings":["truncated response')

    await service.analyze_personas(
        session_id=session_id,
        requesting_user_id="user-1",
    )
    case = await service.select_personas(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_ids=["jordan-lee"],
    )

    assert case.status == "awaiting_qa_mode"
    assert case.deep_dive_findings == ["Review latency is the primary constraint"]
    assert orchestrator.calls == [
        "discovery-persona-extraction-v1",
        "discovery-persona-deep-dive-v1",
        "discovery-persona-deep-dive-v1",
    ]


async def test_deep_dive_fails_closed_after_two_malformed_responses() -> None:
    service, orchestrator, session_id = await _create_service()
    orchestrator.outputs[1] = '{"deep_dive_findings":["first truncated response'
    orchestrator.outputs.insert(2, '{"deep_dive_findings":["second truncated response')

    await service.analyze_personas(
        session_id=session_id,
        requesting_user_id="user-1",
    )
    with pytest.raises(DiscoveryAgentResponseError, match="not valid JSON"):
        await service.select_personas(
            session_id=session_id,
            requesting_user_id="user-1",
            persona_ids=["jordan-lee"],
        )

    case = await service.get_case(
        session_id=session_id,
        requesting_user_id="user-1",
    )
    assert case.status == "awaiting_persona_selection"
    assert orchestrator.calls == [
        "discovery-persona-extraction-v1",
        "discovery-persona-deep-dive-v1",
        "discovery-persona-deep-dive-v1",
    ]


async def test_skip_requires_consent_before_recommendation_and_state_is_durable() -> None:
    service, orchestrator, session_id = await _create_service()

    case = await service.analyze_personas(
        session_id=session_id, requesting_user_id="user-1"
    )
    case = await service.select_persona(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_id="jordan-lee",
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
    assert len(reloaded.proposed_solutions[0].requirements_text) > 4000
    assert len(reloaded.proposed_solutions[0].architecture_text) > 4000
    assert reloaded.gap_analysis is not None
    assert reloaded.gap_analysis.risks == ["Manual review can miss time-sensitive claims"]
    assert reloaded.gap_analysis.contradictions == [
        "The target response time conflicts with manual review"
    ]
    assert reloaded.proposed_solutions[0].evidence_references == [
        "call.txt: manual document review"
    ]


async def test_solution_generation_retries_once_after_truncated_json() -> None:
    service, orchestrator, session_id = await _create_service()
    case = await service.analyze_personas(
        session_id=session_id, requesting_user_id="user-1"
    )
    case = await service.select_persona(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_id="jordan-lee",
    )
    case = await service.set_qa_mode(
        session_id=session_id,
        requesting_user_id="user-1",
        mode="batch",
    )
    case = await service.answer_question(
        session_id=session_id,
        requesting_user_id="user-1",
        question_id="monthly-volume",
        answer="10,000 to 100,000 documents",
    )
    assert case.status == "ready_for_solutions"
    orchestrator.outputs.popleft()
    orchestrator.outputs.appendleft('{"solutions":[{"id":"truncated"')

    case = await service.generate_solutions(
        session_id=session_id, requesting_user_id="user-1"
    )

    assert case.status == "awaiting_solution_selection"
    assert len(case.proposed_solutions) == 1
    assert orchestrator.calls[-2:] == [
        "discovery-probable-solutions-v1",
        "discovery-probable-solutions-v1",
    ]
    retry_variables = orchestrator.execution_requests[-1]["variables"]
    assert isinstance(retry_variables, dict)
    assert "not valid JSON" in str(retry_variables["retry_instruction"])
    assert "between 1,200 and 2,500 characters" in str(
        retry_variables["retry_instruction"]
    )


async def test_refresh_solution_pricing_preserves_generated_solution() -> None:
    service, _, session_id = await _create_service()
    await service.analyze_personas(session_id=session_id, requesting_user_id="user-1")
    await service.select_persona(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_id="jordan-lee",
    )
    await service.set_qa_mode(
        session_id=session_id,
        requesting_user_id="user-1",
        mode="batch",
    )
    await service.answer_question(
        session_id=session_id,
        requesting_user_id="user-1",
        question_id="monthly-volume",
        answer="10,000 to 100,000 documents",
    )
    generated = await service.generate_solutions(
        session_id=session_id, requesting_user_id="user-1"
    )

    refreshed = await service.refresh_solution_pricing(
        session_id=session_id, requesting_user_id="user-1"
    )

    assert refreshed.status == generated.status
    assert refreshed.version == generated.version + 1
    assert refreshed.proposed_solutions[0].architecture_nodes == (
        generated.proposed_solutions[0].architecture_nodes
    )
    assert refreshed.proposed_solutions[0].cost_estimate.monthly_amount == 42.5


async def test_solution_generation_retries_when_pricing_is_outside_architecture() -> None:
    service, orchestrator, session_id = await _create_service()
    await service.analyze_personas(
        session_id=session_id, requesting_user_id="user-1"
    )
    await service.select_persona(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_id="jordan-lee",
    )
    await service.set_qa_mode(
        session_id=session_id,
        requesting_user_id="user-1",
        mode="batch",
    )
    await service.answer_question(
        session_id=session_id,
        requesting_user_id="user-1",
        question_id="monthly-volume",
        answer="10,000 to 100,000 documents",
    )
    orchestrator.outputs.popleft()
    valid_output = json.loads(orchestrator.outputs.popleft())
    invalid_output = json.loads(json.dumps(valid_output))
    invalid_output["solutions"][0]["pricing_queries"][0]["service_name"] = (
        "Azure SQL Database"
    )
    orchestrator.outputs.extend(
        [json.dumps(invalid_output), json.dumps(valid_output)]
    )

    case = await service.generate_solutions(
        session_id=session_id, requesting_user_id="user-1"
    )

    assert case.status == "awaiting_solution_selection"
    assert case.proposed_solutions[0].pricing_queries[0].service_name == "Azure AI Search"
    assert orchestrator.calls[-2:] == [
        "discovery-probable-solutions-v1",
        "discovery-probable-solutions-v1",
    ]
    retry_variables = orchestrator.execution_requests[-1]["variables"]
    assert isinstance(retry_variables, dict)
    assert "pricing query services must match architecture node service names" in str(
        retry_variables["retry_instruction"]
    )


async def test_solution_generation_fails_closed_after_two_malformed_responses() -> None:
    service, orchestrator, session_id = await _create_service()
    await service.analyze_personas(
        session_id=session_id, requesting_user_id="user-1"
    )
    await service.select_persona(
        session_id=session_id,
        requesting_user_id="user-1",
        persona_id="jordan-lee",
    )
    await service.set_qa_mode(
        session_id=session_id,
        requesting_user_id="user-1",
        mode="batch",
    )
    case = await service.answer_question(
        session_id=session_id,
        requesting_user_id="user-1",
        question_id="monthly-volume",
        answer="10,000 to 100,000 documents",
    )
    assert case.status == "ready_for_solutions"
    orchestrator.outputs.clear()
    orchestrator.outputs.extend(
        [
            '{"solutions":[{"id":"first-truncated"',
            '{"solutions":[{"id":"second-truncated"',
        ]
    )

    with pytest.raises(DiscoveryAgentResponseError, match="not valid JSON"):
        await service.generate_solutions(
            session_id=session_id, requesting_user_id="user-1"
        )

    reloaded = await service.get_case(
        session_id=session_id, requesting_user_id="user-1"
    )
    assert reloaded.status == "ready_for_solutions"
    assert reloaded.last_error == "Solution generation failed. Retry when Foundry is available."
    assert orchestrator.calls[-2:] == [
        "discovery-probable-solutions-v1",
        "discovery-probable-solutions-v1",
    ]


async def test_removing_analyzed_upload_invalidates_derived_discovery_state() -> None:
    service, _, session_id = await _create_service()
    analyzed = await service.analyze_personas(
        session_id=session_id, requesting_user_id="user-1"
    )

    await service.remove_source_upload(
        session_id=session_id,
        upload_id=analyzed.source_upload_ids[0],
        requesting_user_id="user-1",
    )

    reloaded = await service.get_case(
        session_id=session_id, requesting_user_id="user-1"
    )
    assert reloaded.status == "created"
    assert reloaded.source_upload_ids == []
    assert reloaded.analyzed_upload_ids == []
    assert reloaded.personas == []
    assert reloaded.selected_persona_id is None
    assert reloaded.deep_dive_findings == []
    assert reloaded.insight_sections == []
    assert reloaded.gap_summary == ""
    assert reloaded.assumption_summary == ""
    assert reloaded.gap_analysis is None
    assert reloaded.qa_mode is None
    assert reloaded.questions == []
    assert reloaded.proposed_solutions == []
    assert reloaded.selected_solution_id is None
    assert reloaded.build_workflow_run_id is None


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


async def test_pricing_recovers_from_architecture_label_and_global_region() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"Items": []})
        assert "contains(serviceName, 'Front Door')" in request.url.params["$filter"]
        assert "armRegionName" not in request.url.params["$filter"]
        return httpx.Response(
            200,
            json={
                "Items": [
                    {
                        "serviceName": "Azure Front Door Service",
                        "productName": "Azure Front Door",
                        "skuName": "Standard",
                        "meterName": "Standard Data Transfer Out",
                        "armRegionName": "Zone 1",
                        "unitOfMeasure": "1 GB",
                        "retailPrice": 0.083,
                    },
                    {
                        "serviceName": "Azure Front Door Service",
                        "productName": "Azure Front Door",
                        "skuName": "Standard",
                        "meterName": "Standard Requests",
                        "armRegionName": "US Gov Zone 1",
                        "unitOfMeasure": "10K",
                        "retailPrice": 0.01125,
                        "tierMinimumUnits": 0,
                        "meterId": "front-door-gov-requests",
                    },
                    {
                        "serviceName": "Azure Front Door Service",
                        "productName": "Azure Front Door",
                        "skuName": "Standard",
                        "meterName": "Standard Requests",
                        "armRegionName": "Zone 1",
                        "unitOfMeasure": "10K",
                        "retailPrice": 0.009,
                        "tierMinimumUnits": 0,
                        "meterId": "front-door-requests",
                    },
                    {
                        "serviceName": "Azure Front Door Service",
                        "productName": "Azure Front Door",
                        "skuName": "Standard",
                        "meterName": "Standard Requests",
                        "armRegionName": "",
                        "unitOfMeasure": "10K",
                        "retailPrice": 0.0065,
                        "tierMinimumUnits": 25000,
                        "meterId": "front-door-requests",
                    },
                ]
            },
        )

    service = AzureRetailPricingService(
        endpoint="https://prices.azure.com/api/retail/prices",
        transport=httpx.MockTransport(handler),
    )
    estimate = await service.estimate(
        [
            PricingQuery(
                service_name="Azure Front Door",
                arm_region_name="eastus",
                sku_name="Standard_AzureFrontDoor",
                meter_name="Standard Requests",
                unit_of_measure="10K",
                units_per_month=30000,
                assumption="300 million requests represented as 30,000 10K billing units",
            )
        ]
    )

    assert len(requests) == 2
    assert estimate.coverage == "complete"
    assert estimate.monthly_amount == 257.5
    assert estimate.annual_amount == 3090


async def test_state_cannot_advance_out_of_order() -> None:
    service, _, session_id = await _create_service()

    with pytest.raises(DiscoveryStateConflictError):
        await service.generate_solutions(
            session_id=session_id,
            requesting_user_id="user-1",
        )