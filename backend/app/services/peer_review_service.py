"""Peer Review application service.

Parses the Peer Review Agent's own structured Peer Review verdict (see
``peer-review-v1`` in ``config/prompts/registry.yaml``) out of the
``peer-review`` workflow step's ``output_text`` - following the exact same
"agents return marker-line text, never JSON, Genie only reports the
agent's own stated verdict" convention already established by
``app.services.requirements_service``. Also drives "Apply Selected Fixes":
regenerating the build with a customer-selected subset of findings and
forcing the security/test/peer-review gate steps to re-run against it.
"""
from __future__ import annotations

import re
from typing import Final

from app.models.governance_gate_report import (
    AgentAssessment,
    AgentAssessmentsReport,
    GateName,
    GateStatus,
    GovernanceFinding,
    GovernanceGateReport,
    PeerReviewDecision,
)
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = ["PeerReviewGateBlockedError", "PeerReviewService", "create_peer_review_service"]

_BUILD_STEP_ID: Final = "build-solution"


class PeerReviewGateBlockedError(RuntimeError):
    """Raised when a deploy approval is attempted while Peer Review has blocked the
    build and no risk acceptance has been recorded for that workflow run."""

_GATE_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "requirements_gate": re.compile(r"REQUIREMENTS_GATE:\s*(PASS|FAIL)", re.IGNORECASE),
    "security_gate": re.compile(r"SECURITY_GATE:\s*(PASS|FAIL)", re.IGNORECASE),
    "test_coverage_gate": re.compile(r"TEST_COVERAGE_GATE:\s*(PASS|FAIL)", re.IGNORECASE),
    "architecture_gate": re.compile(r"ARCHITECTURE_GATE:\s*(PASS|FAIL)", re.IGNORECASE),
    "code_quality_gate": re.compile(r"CODE_QUALITY_GATE:\s*(PASS|FAIL)", re.IGNORECASE),
}
_DECISION_PATTERN: Final = re.compile(r"PEER_REVIEW_DECISION:\s*(APPROVED|BLOCKED)", re.IGNORECASE)
_FINDING_LINE_PATTERN: Final = re.compile(
    r"^-\s*\[(requirements|security|test_coverage|architecture|code_quality)\|"
    r"(critical|high|medium|low)\|([^\]]+)\]\s*(.+?)"
    r"(?:\s*\|\s*Recommendation:\s*(.+))?$",
    re.IGNORECASE,
)


def _parse_gate_report(output_text: str, *, assessed_by_agent_id: str) -> GovernanceGateReport:
    gate_values: dict[str, GateStatus | None] = {}
    for field_name, pattern in _GATE_PATTERNS.items():
        match = pattern.search(output_text)
        gate_values[field_name] = match.group(1).lower() if match else None  # type: ignore[assignment]

    decision_match = _DECISION_PATTERN.search(output_text)
    decision: PeerReviewDecision | None = (
        decision_match.group(1).lower() if decision_match else None  # type: ignore[assignment]
    )

    findings: list[GovernanceFinding] = []
    for line in output_text.splitlines():
        finding_match = _FINDING_LINE_PATTERN.match(line.strip())
        if finding_match is None:
            continue
        gate, severity, finding_id, description, recommendation = finding_match.groups()
        findings.append(
            GovernanceFinding(
                id=finding_id.strip(),
                gate=gate.lower(),  # type: ignore[arg-type]
                severity=severity.lower(),  # type: ignore[arg-type]
                description=description.strip(),
                recommendation=(recommendation or "").strip(),
            )
        )

    if not any(gate_values.values()) and decision is None:
        return GovernanceGateReport(status="undetermined", assessed_by_agent_id=assessed_by_agent_id)

    return GovernanceGateReport(
        status="reviewed",
        requirements_gate=gate_values["requirements_gate"],
        security_gate=gate_values["security_gate"],
        test_coverage_gate=gate_values["test_coverage_gate"],
        architecture_gate=gate_values["architecture_gate"],
        code_quality_gate=gate_values["code_quality_gate"],
        findings=findings,
        decision=decision,
        assessed_by_agent_id=assessed_by_agent_id,
    )


_CODE_FENCE_PATTERN: Final = re.compile(r"^```", re.MULTILINE)


def _count_code_blocks(output_text: str) -> int:
    """Counts fenced code blocks (```...```) the Test Generation Agent actually
    wrote, by counting fence markers in pairs - a real, directly-counted
    quantity, never an estimated or invented test count."""
    return len(_CODE_FENCE_PATTERN.findall(output_text)) // 2


def _parse_agent_assessment(
    output_text: str,
    *,
    gate_field_name: str,
    gate_name: GateName,
    assessed_by_agent_id: str,
    count_tests: bool = False,
) -> AgentAssessment:
    """Parses one specialist agent's own single-gate verdict (Security
    Assessment Agent's SECURITY_GATE, or Test Generation Agent's
    TEST_COVERAGE_GATE) directly from that agent's own step output, reusing
    the exact same gate/finding marker-line conventions as
    ``_parse_gate_report`` - never invents a verdict the agent didn't
    itself state.
    """
    gate_match = _GATE_PATTERNS[gate_field_name].search(output_text)
    if gate_match is None:
        return AgentAssessment(
            status="undetermined", summary=output_text, assessed_by_agent_id=assessed_by_agent_id
        )

    findings: list[GovernanceFinding] = []
    for line in output_text.splitlines():
        finding_match = _FINDING_LINE_PATTERN.match(line.strip())
        if finding_match is None:
            continue
        gate, severity, finding_id, description, recommendation = finding_match.groups()
        if gate.lower() != gate_name:
            continue
        findings.append(
            GovernanceFinding(
                id=finding_id.strip(),
                gate=gate_name,
                severity=severity.lower(),  # type: ignore[arg-type]
                description=description.strip(),
                recommendation=(recommendation or "").strip(),
            )
        )

    return AgentAssessment(
        status="reviewed",
        gate=gate_match.group(1).lower(),  # type: ignore[arg-type]
        summary=output_text,
        findings=findings,
        tests_generated=_count_code_blocks(output_text) if count_tests else 0,
        assessed_by_agent_id=assessed_by_agent_id,
    )


class PeerReviewService:
    """Reads the Peer Reviewer's gate verdict and applies human-selected fixes."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        session_service: SessionService,
        peer_review_step_id: str,
        security_assessment_step_id: str,
        test_generation_step_id: str,
        gated_step_ids: tuple[str, ...],
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._peer_review_step_id = peer_review_step_id
        self._security_assessment_step_id = security_assessment_step_id
        self._test_generation_step_id = test_generation_step_id
        self._gated_step_ids = gated_step_ids

    async def get_gate_report(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> GovernanceGateReport:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = self._get_run(workflow_run_id)

        step = next(
            (r for r in run.step_results if r.step_id == self._peer_review_step_id), None
        )
        if step is None or step.status != "completed":
            return GovernanceGateReport(status="pending")

        return _parse_gate_report(step.output_text or "", assessed_by_agent_id=step.agent_id)

    async def get_agent_assessments(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> AgentAssessmentsReport:
        """Returns each specialist agent's own single-gate assessment, read directly
        from that agent's step output as soon as it completes - available well
        before the slower, consolidated ``get_gate_report`` verdict (which also
        waits on the Peer Review Agent's own review step).
        """
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = self._get_run(workflow_run_id)

        return AgentAssessmentsReport(
            security_assessment=self._parse_step_assessment(
                run,
                step_id=self._security_assessment_step_id,
                gate_field_name="security_gate",
                gate_name="security",
            ),
            test_generation=self._parse_step_assessment(
                run,
                step_id=self._test_generation_step_id,
                gate_field_name="test_coverage_gate",
                gate_name="test_coverage",
                count_tests=True,
            ),
        )

    def _parse_step_assessment(
        self,
        run: WorkflowRunResult,
        *,
        step_id: str,
        gate_field_name: str,
        gate_name: GateName,
        count_tests: bool = False,
    ) -> AgentAssessment:
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        if step is None or step.status != "completed":
            return AgentAssessment(status="pending")
        return _parse_agent_assessment(
            step.output_text or "",
            gate_field_name=gate_field_name,
            gate_name=gate_name,
            assessed_by_agent_id=step.agent_id,
            count_tests=count_tests,
        )

    async def apply_selected_fixes(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        selected_findings: list[str],
        trace_id: str | None = None,
    ) -> WorkflowRunResult:
        """Regenerates the build to resolve ``selected_findings`` and re-runs every
        Peer Review gate step against the regenerated build.

        Additionally names every configured ``gated_step_ids`` in
        ``step_inputs`` so ``WorkflowRuntime`` re-executes those
        already-completed steps too (a step is only ever re-run if it is
        either still pending or explicitly named in ``step_inputs`` - see
        ``WorkflowRuntime.run_workflow``), instead of silently leaving
        stale gate results in place after the build changes.
        """
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        self._get_run(workflow_run_id)

        if selected_findings:
            instruction = (
                "Regenerate the build to resolve the following issues raised by Peer "
                "Review, keeping every other part of the UI, agent code, and "
                "Multi-Agent Workflow section exactly the same as before: "
                + "; ".join(selected_findings)
            )
        else:
            instruction = ""

        step_inputs = {
            _BUILD_STEP_ID: WorkflowStepInput(step_id=_BUILD_STEP_ID, variables={"user_message": instruction}),
        }
        for step_id in self._gated_step_ids:
            step_inputs[step_id] = WorkflowStepInput(step_id=step_id, variables={"user_message": ""})

        return await self._orchestrator.resume_workflow(
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
        )

    def _get_run(self, workflow_run_id: str) -> WorkflowRunResult:
        run = self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found.")
        return run


def create_peer_review_service(
    *,
    orchestrator: AgentOrchestrator,
    session_service: SessionService,
    peer_review_step_id: str = "peer-review",
    security_assessment_step_id: str = "security-assessment",
    test_generation_step_id: str = "test-generation",
    gated_step_ids: tuple[str, ...] = ("security-assessment", "test-generation", "peer-review"),
) -> PeerReviewService:
    return PeerReviewService(
        orchestrator=orchestrator,
        session_service=session_service,
        peer_review_step_id=peer_review_step_id,
        security_assessment_step_id=security_assessment_step_id,
        test_generation_step_id=test_generation_step_id,
        gated_step_ids=gated_step_ids,
    )
