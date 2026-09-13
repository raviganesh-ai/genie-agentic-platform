"""Application service and state machine for durable customer Discovery."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.discovery.models import (
    AiFeasibility,
    ArchitectureEdge,
    ArchitectureNode,
    DiscoveryCase,
    DiscoveryInsightSection,
    DiscoveryQaMode,
    DiscoveryQuestion,
    GapAnalysis,
    PersonaProfile,
    PricingQuery,
    ProposedSolution,
)
from app.discovery.parsing import DiscoveryAgentResponseError, parse_agent_response
from app.discovery.pricing_service import PricingService
from app.discovery.repository import DiscoveryCaseRepository, InMemoryDiscoveryCaseRepository
from app.governance.governance_service import GovernanceService
from app.memory.memory_service import MemoryService
from app.models.workflow_models import WorkflowStepInput
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.model_catalog_service import ModelCatalogService
from app.services.session_service import SessionAccessDeniedError, SessionService

_BUILD_WORKFLOW_ID = "discovery-build-workflow"
_BUILD_STEP_ID = "build-solution"

__all__ = [
    "DiscoveryCaseNotFoundError",
    "DiscoveryService",
    "DiscoveryStateConflictError",
    "create_discovery_service",
]


class DiscoveryCaseNotFoundError(RuntimeError):
    """Raised when a session has no durable Discovery case."""


class DiscoveryStateConflictError(RuntimeError):
    """Raised when an action is invalid for the current durable state."""


class _NamedPersonDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1)


class _PeopleEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    people: list[_NamedPersonDraft] = Field(
        validation_alias=AliasChoices("people", "personas")
    )


class _GapAnalysisDraft(GapAnalysis):
    model_config = ConfigDict(extra="ignore")


class _DiscoveryQuestionDraft(DiscoveryQuestion):
    model_config = ConfigDict(extra="ignore")
    suggested_answers: list[str] = Field(min_length=2, max_length=4)


class _DiscoveryInsightSectionDraft(DiscoveryInsightSection):
    model_config = ConfigDict(extra="ignore")


class _DeepDiveEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    deep_dive_findings: list[str] = Field(min_length=1)
    insight_sections: list[_DiscoveryInsightSectionDraft] = Field(min_length=1, max_length=5)
    gap_summary: str = Field(min_length=1, max_length=1200)
    assumption_summary: str = Field(min_length=1, max_length=1200)
    gap_analysis: _GapAnalysisDraft
    questions: list[_DiscoveryQuestionDraft] = Field(max_length=8)


class _RecommendationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation: str = Field(min_length=1)
    evidence_references: list[str] = Field(default_factory=list)


class _SolutionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=800)
    requirements_text: str = Field(min_length=1, max_length=4000)
    architecture_text: str = Field(min_length=1, max_length=4000)
    architecture_nodes: list[ArchitectureNode] = Field(min_length=1, max_length=10)
    architecture_edges: list[ArchitectureEdge] = Field(default_factory=list, max_length=12)
    pros: list[str] = Field(min_length=1, max_length=5)
    cons: list[str] = Field(min_length=1, max_length=5)
    ai_feasibility: AiFeasibility
    ai_feasibility_rationale: str = Field(min_length=1)
    evidence_references: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_references", "Evidence_references"),
    )
    pricing_queries: list[PricingQuery] = Field(default_factory=list, max_length=6)


class _SolutionsEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    solutions: list[_SolutionDraft] = Field(min_length=1, max_length=3)


class DiscoveryService:
    def __init__(
        self,
        *,
        session_service: SessionService,
        repository: DiscoveryCaseRepository,
        transient_repository: DiscoveryCaseRepository | None = None,
        orchestrator: AgentOrchestrator | None = None,
        pricing_service: PricingService | None = None,
        governance_service: GovernanceService | None = None,
        memory_service: MemoryService | None = None,
        model_catalog_service: ModelCatalogService | None = None,
    ) -> None:
        self._session_service = session_service
        self._repository = repository
        self._transient_repository = transient_repository or InMemoryDiscoveryCaseRepository()
        self._orchestrator = orchestrator
        self._pricing_service = pricing_service
        self._governance_service = governance_service
        self._memory_service = memory_service
        self._model_catalog_service = model_catalog_service

    async def create_or_resume(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        source_upload_ids: list[str],
        model_deployment_ref: str | None = None,
        save_enabled: bool = True,
    ) -> DiscoveryCase:
        await self._session_service.get_session(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
        )
        if model_deployment_ref and self._model_catalog_service is not None:
            await self._model_catalog_service.ensure_available(model_deployment_ref)
        unique_upload_ids = list(dict.fromkeys(source_upload_ids))
        for upload_id in unique_upload_ids:
            await self._session_service.get_upload(
                session_id=session_id,
                upload_id=upload_id,
                requesting_user_id=requesting_user_id,
            )

        existing = await self._get_stored_case(session_id=session_id)
        if existing is not None:
            self._assert_owner(existing, requesting_user_id)
            merged_upload_ids = list(
                dict.fromkeys([*existing.source_upload_ids, *unique_upload_ids])
            )
            selected_model = model_deployment_ref or existing.model_deployment_ref
            if (
                merged_upload_ids == existing.source_upload_ids
                and selected_model == existing.model_deployment_ref
            ):
                return existing
            return await self._save(
                existing,
                source_upload_ids=merged_upload_ids,
                model_deployment_ref=selected_model,
            )

        now = datetime.now(UTC)
        discovery_case = DiscoveryCase(
            id=str(uuid4()),
            session_id=session_id,
            owner_user_id=requesting_user_id,
            save_enabled=save_enabled,
            model_deployment_ref=model_deployment_ref,
            source_upload_ids=unique_upload_ids,
            created_at=now,
            updated_at=now,
        )
        await self._case_repository(discovery_case).put(discovery_case)
        await self._record_state(discovery_case, "created")
        return discovery_case

    async def get_case(self, *, session_id: str, requesting_user_id: str) -> DiscoveryCase:
        await self._session_service.get_session(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
        )
        discovery_case = await self._get_stored_case(session_id=session_id)
        if discovery_case is None:
            raise DiscoveryCaseNotFoundError(
                f"No Discovery case exists for session '{session_id}'."
            )
        self._assert_owner(discovery_case, requesting_user_id)
        return discovery_case

    async def list_cases(self, *, owner_user_id: str) -> list[DiscoveryCase]:
        return await self._repository.list_for_owner(owner_user_id=owner_user_id)

    async def set_save_preference(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        enabled: bool,
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
        )
        if discovery_case.save_enabled == enabled:
            return discovery_case
        if discovery_case.status == "build_started":
            raise DiscoveryStateConflictError(
                "Discovery save preference cannot change after its prototype build has started."
            )

        updated = discovery_case.model_copy(
            update={
                "save_enabled": enabled,
                "version": discovery_case.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if enabled:
            await self._repository.put(updated)
            await self._transient_repository.delete(discovery_case_id=updated.id)
            if updated.analysis_revision > 0:
                await self._write_memory(
                    updated,
                    agent_id="requirements-analyst",
                    artifact=f"saved-snapshot-v{updated.version}",
                    classification="requirement",
                    content={"discovery_case": updated.model_dump(mode="json")},
                )
        else:
            await self._transient_repository.put(updated)
            await self._delete_discovery_memory(discovery_case)
            await self._repository.delete(discovery_case_id=updated.id)
        await self._record_state(updated, "save-enabled" if enabled else "save-disabled")
        return updated

    async def remove_source_upload(
        self, *, session_id: str, upload_id: str, requesting_user_id: str
    ) -> None:
        discovery_case = await self._get_stored_case(session_id=session_id)
        if discovery_case is None:
            return
        self._assert_owner(discovery_case, requesting_user_id)
        if upload_id not in discovery_case.source_upload_ids:
            return
        updates: dict[str, Any] = {
            "source_upload_ids": [
                source_id
                for source_id in discovery_case.source_upload_ids
                if source_id != upload_id
            ],
            "analyzed_upload_ids": [
                source_id
                for source_id in discovery_case.analyzed_upload_ids
                if source_id != upload_id
            ],
        }
        if upload_id in discovery_case.analyzed_upload_ids:
            updates.update(
                status="created",
                personas=[],
                selected_persona_id=None,
                selected_persona_ids=[],
                deep_dive_findings=[],
                insight_sections=[],
                gap_summary="",
                assumption_summary="",
                gap_analysis=None,
                qa_mode=None,
                questions=[],
                proposed_solutions=[],
                selected_solution_id=None,
                build_workflow_run_id=None,
                last_error=None,
            )
        await self._save(discovery_case, **updates)

    async def analyze_personas(
        self, *, session_id: str, requesting_user_id: str
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(
            discovery_case,
            "created",
            "awaiting_persona_selection",
            "awaiting_qa_mode",
            "questioning",
            "ready_for_solutions",
            "awaiting_solution_selection",
            "ready_to_prototype",
            "failed",
        )
        source_material = await self._source_material(discovery_case, requesting_user_id)
        if not source_material.strip():
            raise DiscoveryStateConflictError(
                "Discovery needs at least one completed upload with extractable content."
            )
        previous_status = discovery_case.status
        discovery_case = await self._save(
            discovery_case, status="analyzing_personas", last_error=None
        )
        try:
            result = await self._execute(
                agent_id="requirements-analyst",
                prompt_id="discovery-persona-extraction-v1",
                variables={"source_material": source_material},
                discovery_case=discovery_case,
            )
            parsed = parse_agent_response(result, _PeopleEnvelope)
            personas = self._validated_named_people(
                parsed.people,
                source_material=source_material,
            )
        except DiscoveryStateConflictError as exc:
            await self._save(
                discovery_case,
                status=previous_status,
                last_error=str(exc),
            )
            raise
        except Exception:
            await self._save(
                discovery_case,
                status=previous_status,
                last_error="Persona extraction failed. Retry when Foundry is available.",
            )
            raise
        revision = discovery_case.analysis_revision + 1
        updated = await self._save(
            discovery_case,
            status="awaiting_persona_selection",
            analyzed_upload_ids=list(discovery_case.source_upload_ids),
            analysis_revision=revision,
            personas=personas,
            selected_persona_id=None,
            selected_persona_ids=[],
            deep_dive_findings=[],
            insight_sections=[],
            gap_summary="",
            assumption_summary="",
            gap_analysis=None,
            qa_mode=None,
            questions=[],
            proposed_solutions=[],
            selected_solution_id=None,
            build_workflow_run_id=None,
            last_error=None,
        )
        await self._write_memory(
            updated,
            agent_id="requirements-analyst",
            artifact="personas",
            classification="requirement",
            content={"personas": [item.model_dump(mode="json") for item in personas]},
        )
        return updated

    @staticmethod
    def _validated_named_people(
        drafts: list[_NamedPersonDraft], *, source_material: str
    ) -> list[PersonaProfile]:
        if not drafts:
            raise DiscoveryStateConflictError(
                "No named people were found in the selected evidence. Add material that "
                "identifies a person by name before continuing Discovery."
            )
        normalized_source = " ".join(source_material.casefold().split())
        invalid_names = [
            draft.name
            for draft in drafts
            if " ".join(draft.name.casefold().split()) not in normalized_source
        ]
        if invalid_names:
            raise DiscoveryStateConflictError(
                "Discovery returned entries that are not explicitly named people in the evidence: "
                + ", ".join(invalid_names)
                + ". Re-run analysis with evidence that explicitly identifies each person."
            )
        people: list[PersonaProfile] = []
        seen_names: set[str] = set()
        for index, draft in enumerate(drafts):
            normalized_name = " ".join(draft.name.split())
            comparison_name = normalized_name.casefold()
            if comparison_name in seen_names:
                continue
            seen_names.add(comparison_name)
            person_id = re.sub(r"[^a-z0-9]+", "-", comparison_name).strip("-")
            people.append(
                PersonaProfile(
                    id=person_id or f"person-{index + 1}",
                    name=normalized_name,
                )
            )
        return people

    async def select_persona(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        persona_id: str,
    ) -> DiscoveryCase:
        return await self.select_personas(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
            persona_ids=[persona_id],
        )

    async def select_personas(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        persona_ids: list[str],
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "awaiting_persona_selection")
        unique_ids = list(dict.fromkeys(persona_ids))
        if not unique_ids:
            raise DiscoveryStateConflictError("Select at least one person before running Discovery.")
        personas_by_id = {item.id: item for item in discovery_case.personas}
        unknown_ids = [persona_id for persona_id in unique_ids if persona_id not in personas_by_id]
        if unknown_ids:
            raise DiscoveryStateConflictError(
                "Unknown Discovery persona(s): " + ", ".join(unknown_ids) + "."
            )
        selected_personas = [personas_by_id[persona_id] for persona_id in unique_ids]
        discovery_case = await self._save(
            discovery_case, status="analyzing_persona", last_error=None
        )
        try:
            parsed = await self._execute_deep_dive(
                discovery_case=discovery_case,
                source_material=await self._source_material(
                    discovery_case, requesting_user_id
                ),
                selected_personas=selected_personas,
            )
        except Exception:
            await self._save(
                discovery_case,
                status="awaiting_persona_selection",
                last_error="Persona analysis failed. Select the persona to retry.",
            )
            raise
        updated = await self._save(
            discovery_case,
            status="awaiting_qa_mode" if parsed.questions else "ready_for_solutions",
            selected_persona_id=unique_ids[0],
            selected_persona_ids=unique_ids,
            deep_dive_findings=parsed.deep_dive_findings,
            insight_sections=parsed.insight_sections,
            gap_summary=parsed.gap_summary,
            assumption_summary=parsed.assumption_summary,
            gap_analysis=parsed.gap_analysis,
            qa_mode=None,
            questions=parsed.questions,
            proposed_solutions=[],
            selected_solution_id=None,
        )
        await self._write_memory(
            updated,
            agent_id="requirements-analyst",
            artifact="persona-deep-dive",
            classification="requirement",
            content={
                "personas": [persona.model_dump(mode="json") for persona in selected_personas],
                "insight_sections": [
                    section.model_dump(mode="json") for section in parsed.insight_sections
                ],
                "gap_summary": parsed.gap_summary,
                "assumption_summary": parsed.assumption_summary,
                "findings": parsed.deep_dive_findings,
                "gap_analysis": parsed.gap_analysis.model_dump(mode="json"),
                "questions": [item.model_dump(mode="json") for item in parsed.questions],
            },
        )
        return updated

    async def _execute_deep_dive(
        self,
        *,
        discovery_case: DiscoveryCase,
        source_material: str,
        selected_personas: list[PersonaProfile],
    ) -> _DeepDiveEnvelope:
        variables = {
            "source_material": source_material,
            "selected_persona": json.dumps(
                [persona.model_dump(mode="json") for persona in selected_personas]
            ),
            "retry_instruction": "",
        }
        for attempt in range(2):
            if attempt:
                variables["retry_instruction"] = (
                    "A prior response was malformed or truncated. Regenerate it from the "
                    "source as fresh JSON, honor every size limit, and close all arrays, "
                    "objects, and strings."
                )
            result = await self._execute(
                agent_id="requirements-analyst",
                prompt_id="discovery-persona-deep-dive-v1",
                variables=variables,
                discovery_case=discovery_case,
            )
            try:
                return parse_agent_response(result, _DeepDiveEnvelope)
            except DiscoveryAgentResponseError:
                if attempt == 1:
                    raise
        raise RuntimeError("Discovery deep-dive retry loop exited unexpectedly.")

    async def set_qa_mode(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        mode: DiscoveryQaMode,
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "awaiting_qa_mode", "questioning")
        if not discovery_case.questions:
            raise DiscoveryStateConflictError("Select and analyze a persona before starting Q&A.")
        return await self._save(discovery_case, status="questioning", qa_mode=mode)

    async def answer_question(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        question_id: str,
        answer: str | None,
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "questioning")
        if discovery_case.qa_mode is None:
            raise DiscoveryStateConflictError("Choose a Q&A mode before answering questions.")
        questions = list(discovery_case.questions)
        index = self._question_index(questions, question_id)
        normalized = answer.strip() if answer else None
        questions[index] = questions[index].model_copy(
            update={
                "status": "answered" if normalized else "recommendation_offered",
                "answer": normalized,
                "recommendation": None,
            }
        )
        return await self._save(
            discovery_case,
            status=self._qa_status(questions),
            questions=questions,
        )

    async def respond_to_recommendation(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        question_id: str,
        accepted: bool,
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "questioning")
        questions = list(discovery_case.questions)
        index = self._question_index(questions, question_id)
        question = questions[index]
        if question.status != "recommendation_offered":
            raise DiscoveryStateConflictError(
                "A recommendation can be accepted or declined only after Genie offers one."
            )
        if accepted:
            result = await self._execute(
                agent_id="architecture-designer",
                prompt_id="discovery-best-practice-recommendation-v1",
                variables={
                    "question": question.model_dump_json(),
                    "discovery_context": self._context_json(discovery_case),
                },
                discovery_case=discovery_case,
            )
            parsed = parse_agent_response(result, _RecommendationEnvelope)
            questions[index] = question.model_copy(
                update={
                    "status": "recommended",
                    "recommendation": parsed.recommendation,
                    "evidence_references": parsed.evidence_references,
                }
            )
        else:
            questions[index] = question.model_copy(update={"status": "recommendation_declined"})
        return await self._save(
            discovery_case,
            status=self._qa_status(questions),
            questions=questions,
        )

    async def generate_solutions(
        self, *, session_id: str, requesting_user_id: str
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "ready_for_solutions")
        if self._pricing_service is None:
            raise DiscoveryStateConflictError("Azure retail pricing is unavailable.")
        discovery_case = await self._save(
            discovery_case, status="generating_solutions", last_error=None
        )
        try:
            drafts = await self._execute_solution_drafts(
                discovery_case=discovery_case,
                source_material=await self._source_material(
                    discovery_case, requesting_user_id
                ),
            )
            solutions: list[ProposedSolution] = []
            for draft in drafts:
                estimate = await self._pricing_service.estimate(draft.pricing_queries)
                solutions.append(
                    ProposedSolution(**draft.model_dump(), cost_estimate=estimate)
                )
        except Exception:
            await self._save(
                discovery_case,
                status="ready_for_solutions",
                last_error="Solution generation failed. Retry when Foundry is available.",
            )
            raise
        updated = await self._save(
            discovery_case,
            status="awaiting_solution_selection",
            proposed_solutions=solutions,
            selected_solution_id=None,
        )
        await self._write_memory(
            updated,
            agent_id="architecture-designer",
            artifact="solutions",
            classification="architecture_finding",
            content={"solutions": [item.model_dump(mode="json") for item in solutions]},
        )
        return updated

    async def _execute_solution_drafts(
        self,
        *,
        discovery_case: DiscoveryCase,
        source_material: str,
    ) -> list[_SolutionDraft]:
        variables = {
            "discovery_context": self._context_json(discovery_case),
            "source_material": source_material,
            "retry_instruction": "",
        }
        for attempt in range(2):
            if attempt:
                variables["retry_instruction"] = (
                    "A prior response was malformed, truncated, or schema-invalid. Regenerate "
                    "it as fresh JSON, honor every field and size limit, and close all arrays, "
                    "objects, and strings."
                )
            result = await self._execute(
                agent_id="architecture-designer",
                prompt_id="discovery-probable-solutions-v1",
                variables=variables,
                discovery_case=discovery_case,
            )
            try:
                return parse_agent_response(result, _SolutionsEnvelope).solutions
            except DiscoveryAgentResponseError:
                if attempt == 1:
                    raise
        raise RuntimeError("Discovery solution retry loop exited unexpectedly.")

    async def select_solution(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        solution_id: str,
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "awaiting_solution_selection")
        if not any(item.id == solution_id for item in discovery_case.proposed_solutions):
            raise DiscoveryStateConflictError(f"Unknown Discovery solution '{solution_id}'.")
        updated = await self._save(
            discovery_case,
            status="ready_to_prototype",
            selected_solution_id=solution_id,
        )
        await self._write_memory(
            updated,
            agent_id="architecture-designer",
            artifact="selected-solution",
            classification="architecture_finding",
            content={"selected_solution_id": solution_id},
        )
        return updated

    async def start_prototype(
        self, *, session_id: str, requesting_user_id: str
    ) -> DiscoveryCase:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._require_status(discovery_case, "ready_to_prototype")
        solution = next(
            (
                item
                for item in discovery_case.proposed_solutions
                if item.id == discovery_case.selected_solution_id
            ),
            None,
        )
        if solution is None:
            raise DiscoveryStateConflictError("Select a probable solution before prototyping.")
        orchestrator = self._require_orchestrator()
        scope_id = None
        if discovery_case.model_deployment_ref:
            scope_id = f"model:{discovery_case.model_deployment_ref}"
            await orchestrator.provision_customer_agents(
                session_id=session_id,
                scope_id=scope_id,
                trace_id=f"discovery:{discovery_case.id}:provision",
                model_deployment_ref=discovery_case.model_deployment_ref,
            )
        run = await orchestrator.start_workflow_background(
            workflow_id=_BUILD_WORKFLOW_ID,
            session_id=session_id,
            trace_id=f"discovery:{discovery_case.id}:build",
            step_inputs={
                _BUILD_STEP_ID: WorkflowStepInput(
                    step_id=_BUILD_STEP_ID,
                    variables={
                        "requirements": solution.requirements_text,
                        "architecture": solution.architecture_text,
                        "policies": "",
                        "excluded_agents": "",
                        "user_message": "Build the approved Discovery solution.",
                    },
                )
            },
            agent_scope_id=scope_id,
        )
        return await self._save(
            discovery_case,
            status="build_started",
            build_workflow_run_id=run.workflow_run_id,
        )

    async def delete_case(
        self, *, session_id: str, requesting_user_id: str
    ) -> None:
        discovery_case = await self.get_case(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        if discovery_case.status == "build_started":
            raise DiscoveryStateConflictError(
                "Discovery cannot be deleted after its prototype build has started."
            )
        await self._delete_discovery_memory(discovery_case)
        await self._case_repository(discovery_case).delete(
            discovery_case_id=discovery_case.id
        )
        await self._record_state(discovery_case, "deleted")

    async def _delete_discovery_memory(self, discovery_case: DiscoveryCase) -> None:
        if (
            self._memory_service is None
            or self._orchestrator is None
            or "genie-orchestrator" not in self._orchestrator.agent_registry
        ):
            return
        await self._memory_service.shared.delete_prefix(
            agent=self._orchestrator.agent_registry.get("genie-orchestrator"),
            session_id=discovery_case.session_id,
            trace_id=f"discovery:{discovery_case.id}:delete",
            key_prefix=f"discovery:{discovery_case.id}:",
        )

    async def _source_material(
        self, discovery_case: DiscoveryCase, requesting_user_id: str
    ) -> str:
        parts: list[str] = []
        for upload_id in discovery_case.source_upload_ids:
            upload = await self._session_service.get_upload(
                session_id=discovery_case.session_id,
                upload_id=upload_id,
                requesting_user_id=requesting_user_id,
            )
            if upload.transcript_text:
                is_docx = upload.file_name.lower().endswith(".docx") or upload.content_type == (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
                if is_docx and upload.transcript_text.startswith("PK\x03\x04"):
                    raise DiscoveryStateConflictError(
                        f"'{upload.file_name}' was uploaded before Word document extraction "
                        "was available. Start a new Discovery and re-upload the DOCX files."
                    )
                parts.append(f"--- {upload.file_name} ---\n{upload.transcript_text}")
        return "\n\n".join(parts)

    async def _execute(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        discovery_case: DiscoveryCase,
    ) -> str:
        result = await self._require_orchestrator().execute_agent(
            agent_id=agent_id,
            prompt_id=prompt_id,
            variables=variables,
            session_id=discovery_case.session_id,
            trace_id=f"discovery:{discovery_case.id}:r{discovery_case.analysis_revision + 1}",
        )
        return result.output_text

    async def _save(self, discovery_case: DiscoveryCase, **updates: Any) -> DiscoveryCase:
        updates.update(
            version=discovery_case.version + 1,
            updated_at=datetime.now(UTC),
        )
        updated = discovery_case.model_copy(update=updates)
        await self._case_repository(updated).put(updated)
        await self._record_state(updated, updated.status)
        return updated

    async def _get_stored_case(self, *, session_id: str) -> DiscoveryCase | None:
        durable = await self._repository.get_for_session(session_id=session_id)
        if durable is not None:
            return durable
        return await self._transient_repository.get_for_session(session_id=session_id)

    def _case_repository(self, discovery_case: DiscoveryCase) -> DiscoveryCaseRepository:
        return self._repository if discovery_case.save_enabled else self._transient_repository

    async def _record_state(self, discovery_case: DiscoveryCase, state: str) -> None:
        if self._governance_service is None:
            return
        await self._governance_service.record_lifecycle_event(
            session_id=discovery_case.session_id,
            trace_id=f"discovery:{discovery_case.id}",
            agent_id="genie-orchestrator",
            state=f"discovery:{state}",
        )

    async def _write_memory(
        self,
        discovery_case: DiscoveryCase,
        *,
        agent_id: str,
        artifact: str,
        classification: str,
        content: dict[str, Any],
    ) -> None:
        if (
            not discovery_case.save_enabled
            or self._memory_service is None
            or self._orchestrator is None
        ):
            return
        agent = self._orchestrator.agent_registry.get(agent_id)
        await self._memory_service.shared.write(
            agent=agent,
            session_id=discovery_case.session_id,
            trace_id=f"discovery:{discovery_case.id}:r{discovery_case.analysis_revision}",
            key=f"discovery:{discovery_case.id}:r{discovery_case.analysis_revision}:{artifact}",
            classification=classification,  # type: ignore[arg-type]
            content=content,
            evidence_references=[f"discovery-case:{discovery_case.id}"],
        )

    @staticmethod
    def _assert_owner(discovery_case: DiscoveryCase, requesting_user_id: str) -> None:
        if discovery_case.owner_user_id != requesting_user_id:
            raise SessionAccessDeniedError(
                f"User '{requesting_user_id}' is not authorized to access "
                f"Discovery case '{discovery_case.id}'."
            )

    @staticmethod
    def _question_index(questions: list[DiscoveryQuestion], question_id: str) -> int:
        try:
            return next(index for index, item in enumerate(questions) if item.id == question_id)
        except StopIteration as exc:
            raise DiscoveryStateConflictError(
                f"Unknown Discovery question '{question_id}'."
            ) from exc

    @staticmethod
    def _qa_status(questions: list[DiscoveryQuestion]) -> str:
        resolved = {"answered", "recommended", "recommendation_declined"}
        return (
            "ready_for_solutions"
            if questions and all(item.status in resolved for item in questions)
            else "questioning"
        )

    @staticmethod
    def _context_json(discovery_case: DiscoveryCase) -> str:
        return json.dumps(
            {
                "selected_persona_id": discovery_case.selected_persona_id,
                "selected_persona_ids": discovery_case.selected_persona_ids,
                "personas": [item.model_dump(mode="json") for item in discovery_case.personas],
                "deep_dive_findings": discovery_case.deep_dive_findings,
                "insight_sections": [
                    section.model_dump(mode="json")
                    for section in discovery_case.insight_sections
                ],
                "gap_summary": discovery_case.gap_summary,
                "assumption_summary": discovery_case.assumption_summary,
                "gap_analysis": (
                    discovery_case.gap_analysis.model_dump(mode="json")
                    if discovery_case.gap_analysis
                    else None
                ),
                "questions": [item.model_dump(mode="json") for item in discovery_case.questions],
            }
        )

    def _require_orchestrator(self) -> AgentOrchestrator:
        if self._orchestrator is None:
            raise DiscoveryStateConflictError("Discovery agent execution is unavailable.")
        return self._orchestrator

    @staticmethod
    def _require_status(discovery_case: DiscoveryCase, *allowed: str) -> None:
        if discovery_case.status not in allowed:
            raise DiscoveryStateConflictError(
                f"Discovery action is unavailable while status is '{discovery_case.status}'."
            )


def create_discovery_service(
    *,
    session_service: SessionService,
    repository: DiscoveryCaseRepository | None = None,
    transient_repository: DiscoveryCaseRepository | None = None,
    orchestrator: AgentOrchestrator | None = None,
    pricing_service: PricingService | None = None,
    governance_service: GovernanceService | None = None,
    memory_service: MemoryService | None = None,
    model_catalog_service: ModelCatalogService | None = None,
) -> DiscoveryService:
    return DiscoveryService(
        session_service=session_service,
        repository=repository or InMemoryDiscoveryCaseRepository(),
        transient_repository=transient_repository,
        orchestrator=orchestrator,
        pricing_service=pricing_service,
        governance_service=governance_service,
        memory_service=memory_service,
        model_catalog_service=model_catalog_service,
    )