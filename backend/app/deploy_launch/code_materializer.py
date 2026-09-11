"""Materializes the Build Agent's generated code into real files on disk.

Parses the ``build-solution`` workflow step's own ``output_text`` (see
``build-generation-v1`` in ``config/prompts/registry.yaml``), which follows
an exact, already-established fenced-code-block convention: one
```python``` block per specialist agent (first line ``# agent: <name>``),
one more ```python``` block for the orchestrator (first line
``# agent: orchestrator``), and exactly one ```tsx``` block for the UI
(first line ``// agent: ui``). Never invents, reformats, or summarizes the
agent's own code - every byte written to disk is exactly what the agent
returned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "MaterializedBuild",
    "MaterializedCodeError",
    "generate_agent_config_module",
    "generate_backend_service_scaffold",
    "materialize_build",
]

# A ``` fence only counts when it OPENS ITS OWN LINE. Generated agent code
# legitimately contains triple backticks inside string literals (e.g. an
# agent that builds a markdown report with ``lines.append('```text')``);
# without the line anchors those would terminate the block early and
# materialize truncated, non-importable source.
_FENCE_PATTERN: Final = re.compile(
    r"^[ \t]*```(python|tsx)[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_AGENT_MARKER_PATTERN: Final = re.compile(r"^#\s*agent:\s*(.+)$")
_UI_MARKER_PATTERN: Final = re.compile(r"^//\s*agent:\s*ui\s*$", re.IGNORECASE)

_ORCHESTRATOR_MARKER: Final = "orchestrator"

# The generated backend proxy's main.py always does
# ``from orchestrator import OrchestratorAgent`` (see _MAIN_PY_TEMPLATE
# below) - this exact literal class name is the contract between the
# LLM-generated orchestrator module and that deterministic scaffold. A
# differently named class (e.g. ``FactoryOrchestratorAgent``) makes the
# import silently fail closed inside main.py's own broad except, so the
# deployed mission would forever fall back to a generic conversational
# reply instead of ever running its real business logic - fail this
# earlier, at build time, with an actionable error instead.
_ORCHESTRATOR_CLASS_PATTERN: Final = re.compile(r"^class\s+OrchestratorAgent\b", re.MULTILINE)
_DIRECT_FOUNDRY_AGENT_IMPORT_PATTERN: Final = re.compile(
    r"^from agent_framework\.foundry import FoundryAgent(?P<suffix>[^\n]*)$",
    re.MULTILINE,
)
_EXACT_FILE_NAME_COMPARISON_PATTERN: Final = re.compile(
    r"\b[A-Za-z_$][\w$]*\.name\s*(?:===|!==|==|!=)\s*"
    r"(?P<quote>['\"`])[^'\"`\r\n]*\.[A-Za-z0-9]{1,10}(?P=quote)",
)


class MaterializedCodeError(RuntimeError):
    """Raised when the Build Agent's output does not contain a materializable build."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "agent"


def _use_mission_foundry_runtime(code: str) -> str:
    """Routes generated Foundry calls through Genie's deterministic adapter."""

    return _DIRECT_FOUNDRY_AGENT_IMPORT_PATTERN.sub(
        r"from mission_foundry_runtime import MissionFoundryAgent as FoundryAgent\g<suffix>",
        code,
    )


@dataclass(frozen=True)
class MaterializedBuild:
    """The Build Agent's generated code, parsed into real, named files."""

    agent_modules: dict[str, str] = field(default_factory=dict)
    orchestrator_module: str | None = None
    ui_component: str | None = None

    def write_to_directory(
        self, root: Path, *, backend_service_scaffold: dict[str, str] | None = None
    ) -> list[Path]:
        """Writes every parsed piece to real files under ``root``, returns their paths.

        ``backend_service_scaffold`` (see ``generate_backend_service_scaffold``),
        when supplied, is written alongside the orchestrator/agent modules so
        ``root`` is a real, buildable backend service directory (with its own
        ``Dockerfile``) ready for ``BackendDeploymentService.deploy``.
        """

        root.mkdir(parents=True, exist_ok=True)
        agents_dir = root / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for agent_name, code in self.agent_modules.items():
            path = agents_dir / f"{_slugify(agent_name)}.py"
            path.write_text(_use_mission_foundry_runtime(code), encoding="utf-8")
            written.append(path)

        if self.orchestrator_module is not None:
            path = root / "orchestrator.py"
            path.write_text(
                _use_mission_foundry_runtime(self.orchestrator_module), encoding="utf-8"
            )
            written.append(path)

        if self.ui_component is not None:
            path = root / "MissionApp.tsx"
            path.write_text(self.ui_component, encoding="utf-8")
            written.append(path)

        for file_name, content in (backend_service_scaffold or {}).items():
            path = root / file_name
            path.write_text(content, encoding="utf-8")
            written.append(path)

        return written


def materialize_build(output_text: str) -> MaterializedBuild:
    """Parses the Build Agent's ``output_text`` into a ``MaterializedBuild``.

    Raises ``MaterializedCodeError`` if no fenced code blocks at all could
    be found - a real failure, never silently treated as an empty build.
    """

    agent_modules: dict[str, str] = {}
    orchestrator_module: str | None = None
    ui_component: str | None = None

    for language, body in _FENCE_PATTERN.findall(output_text):
        lines = body.splitlines()
        first_line = lines[0].strip() if lines else ""

        if language.lower() == "python":
            marker_match = _AGENT_MARKER_PATTERN.match(first_line)
            if marker_match is None:
                continue
            agent_name = marker_match.group(1).strip()
            if agent_name.lower() == _ORCHESTRATOR_MARKER:
                orchestrator_module = body.strip("\n")
            else:
                agent_modules[agent_name] = body.strip("\n")
        elif language.lower() == "tsx":
            if _UI_MARKER_PATTERN.match(first_line):
                ui_component = body.strip("\n")

    if not agent_modules and orchestrator_module is None and ui_component is None:
        raise MaterializedCodeError(
            "No materializable agent, orchestrator, or UI code block was found in "
            "the build-solution step's output."
        )

    if orchestrator_module is not None and not _ORCHESTRATOR_CLASS_PATTERN.search(orchestrator_module):
        raise MaterializedCodeError(
            "The generated orchestrator module does not define a top-level "
            "'class OrchestratorAgent' - this mission's backend proxy always "
            "does 'from orchestrator import OrchestratorAgent', so any other "
            "class name would silently fall back to a generic conversational "
            "reply instead of running this mission's real pipeline."
        )

    if ui_component is not None and _EXACT_FILE_NAME_COMPARISON_PATTERN.search(ui_component):
        raise MaterializedCodeError(
            "The generated mission UI compares an uploaded file's name to an exact "
            "literal. Generated prototypes must validate uploaded content and file "
            "type, never an end-user-controlled filename or example filename."
        )

    return MaterializedBuild(
        agent_modules=agent_modules,
        orchestrator_module=orchestrator_module,
        ui_component=ui_component,
    )


_MAIN_PY_TEMPLATE = '''"""Real, deterministically generated backend proxy for one deployed mission.

Never LLM-authored: this file is generated by
``app.deploy_launch.code_materializer.generate_backend_service_scaffold``
every time a mission is deployed. Its job is to receive the mission UI's
same-origin calls, persist every uploaded attachment's FULL content to this
mission's own working directory (never truncated or folded into a single
chat turn, so every requirement in an uploaded package is genuinely
considered), and run this mission's own real, deterministic Orchestrator
Agent (``orchestrator.py``, generated alongside this file) against it. Only
when that structured run cannot proceed (for example, a free-form
conversational request rather than the JSON payload this mission's own
input zone(s) compose) does it fall back to a direct conversational reply
from this mission's Orchestrator Agent, already provisioned in Azure AI
Foundry during the "Deploy Agents to Foundry" pipeline step - the
Orchestrator Agent itself is never reachable directly from the browser.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from agent_framework.foundry import FoundryAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title={mission_title!r})
{authentication_middleware}

_logger = logging.getLogger("mission.backend")

_ORCHESTRATOR_AGENT_NAME = "{orchestrator_agent_name}"

# Generous but bounded - guards against unbounded memory/disk usage from an
# oversized upload (OWASP: resource consumption). Plain text only, no binary
# storage - kept deliberately simple.
_MAX_ATTACHMENT_CHARS = 200_000


class Attachment(BaseModel):
    name: str
    content: str


class InvokeRequest(BaseModel):
    message: str
    attachments: list[Attachment] = []


class InvokeResponse(BaseModel):
    output_text: str


def _check_attachment_size(attachments: list[Attachment]) -> None:
    total_chars = sum(len(attachment.content) for attachment in attachments)
    if total_chars > _MAX_ATTACHMENT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Attached file content exceeds the {{_MAX_ATTACHMENT_CHARS:,}} character limit.",
        )


def _persist_attachments(attachments: list[Attachment]) -> None:
    """Writes every uploaded attachment's FULL content to this mission's own
    working directory so the real, deterministic Orchestrator/specialist
    agent pipeline can read every uploaded requirement in full - never
    truncated or summarized into a single chat turn, so every requirement
    in an uploaded package is genuinely taken into consideration.
    """
    if not attachments:
        return
    working_dir = Path(os.getenv("FACTORY_WORKING_DIR", "/data"))
    working_dir.mkdir(parents=True, exist_ok=True)
    for attachment in attachments:
        # Strip any path components from the browser-supplied file name so
        # a crafted name cannot escape the working directory (OWASP: path
        # traversal).
        safe_name = Path(attachment.name).name
        if not safe_name:
            continue
        (working_dir / safe_name).write_text(attachment.content, encoding="utf-8")


def _compose_message(request: InvokeRequest) -> str:
    """Folds any uploaded file attachments into one message for a direct
    conversational reply from the Orchestrator Agent.

    Only used as a fallback (see ``_run_orchestrator_pipeline`` below) when
    this mission's real, deterministic Orchestrator pipeline cannot accept
    the submitted request. Each attachment's full text content is embedded
    ahead of the user's own message, clearly labeled by file name.
    """
    if not request.attachments:
        return request.message
    sections = [
        f"--- Attached file: {{attachment.name}} ---\\n{{attachment.content}}"
        for attachment in request.attachments
    ]
    return "\\n\\n".join([*sections, request.message])


def _is_structured_mission_request(message: str) -> bool:
    try:
        json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


async def _run_orchestrator_pipeline(
    message: str, *, on_progress: Callable[[str], Awaitable[None]] | None = None
) -> str | None:
    """Runs this mission's real, deterministic Orchestrator pipeline.

    ``on_progress``, when given, is forwarded to ``OrchestratorAgent.run``
    so each specialist hand-off can be narrated to the caller AS IT
    HAPPENS rather than only once the entire pipeline has finished (see
    ``_stream_agent_response`` below, which is what makes the mission UI's
    live Agent Pipeline animation actually light up node by node instead
    of jumping straight from all-pending to all-complete).

    Returns the pipeline's own structured result as JSON text, or ``None``
    when this mission's Orchestrator cannot accept the submitted request
    (for example, ``message`` is a free-form conversational request rather
    than the JSON-encoded configuration this mission's own input zone(s)
    compose) - callers fall back to a direct conversational reply in that
    case.
    """
    try:
        from orchestrator import OrchestratorAgent
    except ImportError:
        if _is_structured_mission_request(message):
            raise
        return None
    try:
        orchestrator = OrchestratorAgent()
        run_parameters = inspect.signature(orchestrator.run).parameters.values()
        supports_progress = any(
            parameter.name == "on_progress"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in run_parameters
        )
        if supports_progress:
            result = await orchestrator.run(message, on_progress=on_progress)
        else:
            # An orchestrator generated before the on_progress contract can
            # still run; it simply cannot emit specialist hand-off narration.
            result = await orchestrator.run(message)
    except Exception:
        # The Orchestrator is generated code whose exact failure modes
        # cannot be enumerated in advance. A structured request came from
        # the generated mission UI and must never be disguised as a
        # successful conversational response; propagate it so deployed
        # acceptance tests fail and trigger the bounded repair workflow.
        if _is_structured_mission_request(message):
            _logger.exception("Structured mission orchestrator execution failed.")
            raise
        # Plain-text quick requests are outside the generated form contract
        # and may still use the mission's conversational Foundry fallback.
        _logger.warning(
            "Orchestrator pipeline run did not complete; falling back to a "
            "conversational reply.",
            exc_info=True,
        )
        return None
    return json.dumps(result)


async def _conversational_reply(message: str) -> str:
    endpoint = os.environ["FOUNDRY_ENDPOINT"]
    project_name = os.environ["FOUNDRY_PROJECT_NAME"]
    async with DefaultAzureCredential() as credential:
        async with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
            agent = FoundryAgent(
                project_client=project_client,
                agent_name=_ORCHESTRATOR_AGENT_NAME,
                agent_version=os.getenv("FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "1"),
            )
            response = await agent.run(message)
            return (getattr(response, "text", None) or "").strip()


@app.get("/health")
async def health() -> dict[str, str]:
    return {{"status": "ok"}}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    try:
        from orchestrator import OrchestratorAgent

        OrchestratorAgent()
    except Exception as exc:
        _logger.exception("Generated mission orchestrator failed readiness validation.")
        raise HTTPException(
            status_code=503,
            detail="Generated mission orchestrator is not ready.",
        ) from exc
    return {{"status": "ready"}}


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    _check_attachment_size(request.attachments)
    _persist_attachments(request.attachments)

    pipeline_output = await _run_orchestrator_pipeline(request.message)
    if pipeline_output is not None:
        return InvokeResponse(output_text=pipeline_output)

    output_text = await _conversational_reply(_compose_message(request))
    return InvokeResponse(output_text=output_text)


async def _stream_agent_response(request: InvokeRequest) -> AsyncIterator[str]:
    """Yields Server-Sent Events as the mission's response streams in.

    Each event line is a JSON object: ``{{"delta": "<incremental text>"}}``
    while the response is still being generated, then exactly one final
    ``{{"done": true, "output_text": "<full response>"}}`` once finished -
    lets the mission UI show the Orchestrator genuinely working in real
    time instead of waiting on one long blocking call. When this mission's
    real, deterministic Orchestrator pipeline runs, it is started as a
    background task and its own ``on_progress`` hand-off narration
    (e.g. "Handing off to <Agent Name>...") is relayed as its own delta
    event THE MOMENT each specialist agent starts/finishes - never
    buffered until the whole pipeline completes - so the mission UI's live
    Agent Pipeline visualization can actually light up node by node while
    real work is happening, not just flash from all-pending to
    all-complete once the entire run is already done.
    """
    _check_attachment_size(request.attachments)
    _persist_attachments(request.attachments)

    progress_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _on_progress(narration: str) -> None:
        await progress_queue.put(narration)

    pipeline_result: dict[str, str | None] = {{"output": None}}

    async def _run_pipeline() -> None:
        try:
            pipeline_result["output"] = await _run_orchestrator_pipeline(
                request.message, on_progress=_on_progress
            )
        finally:
            await progress_queue.put(None)

    pipeline_task = asyncio.create_task(_run_pipeline())
    while True:
        narration = await progress_queue.get()
        if narration is None:
            break
        yield "data: " + json.dumps(dict(delta=narration)) + "\\n\\n"
    await pipeline_task

    pipeline_output = pipeline_result["output"]
    if pipeline_output is not None:
        yield "data: " + json.dumps(dict(done=True, output_text=pipeline_output)) + "\\n\\n"
        return

    message = _compose_message(request)
    endpoint = os.environ["FOUNDRY_ENDPOINT"]
    project_name = os.environ["FOUNDRY_PROJECT_NAME"]
    async with DefaultAzureCredential() as credential:
        async with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
            agent = FoundryAgent(
                project_client=project_client,
                agent_name=_ORCHESTRATOR_AGENT_NAME,
                agent_version=os.getenv("FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "1"),
            )
            accumulated = ""
            async for update in agent.run(message, tools=None, stream=True):
                delta = getattr(update, "text", None) or ""
                if not delta:
                    continue
                accumulated += delta
                yield "data: " + json.dumps(dict(delta=delta)) + "\\n\\n"
            yield "data: " + json.dumps(dict(done=True, output_text=accumulated.strip())) + "\\n\\n"


@app.post("/invoke/stream")
async def invoke_stream(request: InvokeRequest) -> StreamingResponse:
    return StreamingResponse(_stream_agent_response(request), media_type="text/event-stream")
'''

_AGENT_CONFIG_PY_TEMPLATE = '''"""Deterministically generated agent-name configuration - never LLM-authored.

Maps this mission's own logical specialist agent names (exactly as named in
the approved architecture's "## Multi-Agent Workflow" section) to their
real, already-provisioned Azure AI Foundry agent names, written fresh by
``app.deploy_launch.code_materializer.generate_agent_config_module`` every
time this mission is deployed. The generated ``orchestrator.py``'s
``call_<agent>`` delegation tools import this module and look up each
specialist's Foundry agent name here - never hardcoding it.
"""
from __future__ import annotations

AGENT_FOUNDRY_NAMES: dict[str, str] = {agent_foundry_names!r}
'''

_MISSION_FOUNDRY_RUNTIME_PY = '''"""Stable runtime boundary for generated mission agents.

Generated code depends on this small Genie-owned API instead of coupling itself
to changing Azure AI Projects and Microsoft Agent Framework constructor and
response details.
"""
from __future__ import annotations

import json
import os
from typing import Any

from agent_framework.foundry import FoundryAgent as _SdkFoundryAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential


class MissionAgentResult(dict[str, Any]):
    """Dictionary result that also exposes Agent Framework-style attributes."""

    def __init__(self, *, raw_text: str, value: Any) -> None:
        mapping = value if isinstance(value, dict) else {"output_text": raw_text}
        super().__init__(mapping)
        self.text = raw_text
        self.value = value


class MissionFoundryAgent:
    """Runs one existing mission agent using verified keyword-only SDK calls."""

    def __init__(
        self,
        agent_name: str | None = None,
        *,
        agent_version: str | None = None,
        **_: Any,
    ) -> None:
        if not agent_name:
            raise ValueError("A non-empty mission Foundry agent name is required.")
        self._agent_name = agent_name
        self._agent_version = agent_version

    async def run(self, messages: Any, **kwargs: Any) -> MissionAgentResult:
        if kwargs.get("stream"):
            raise ValueError("MissionFoundryAgent streaming is not supported inside delegation tools.")

        input_text = messages if isinstance(messages, str) else json.dumps(messages)
        endpoint = os.environ["FOUNDRY_ENDPOINT"]
        async with DefaultAzureCredential() as credential:
            async with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
                agent_version = self._agent_version
                if not agent_version:
                    details = await project_client.agents.get(self._agent_name)
                    agent_version = details.versions.latest.version
                agent = _SdkFoundryAgent(
                    project_client=project_client,
                    agent_name=self._agent_name,
                    agent_version=agent_version,
                )
                response = await agent.run(input_text, tools=kwargs.get("tools"))

        raw_text = (getattr(response, "text", None) or "").strip()
        if not raw_text:
            raise RuntimeError(
                f"Mission Foundry agent '{self._agent_name}' returned no output text."
            )
        try:
            value: Any = json.loads(raw_text)
        except json.JSONDecodeError:
            value = {"output_text": raw_text}
        return MissionAgentResult(raw_text=raw_text, value=value)
'''

_REQUIREMENTS_TXT = """fastapi>=0.115,<1.0
uvicorn>=0.32,<1.0
azure-ai-projects>=2.3,<3.0
azure-identity>=1.19,<2.0
agent-framework>=1.0
"""

_DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""


def generate_agent_config_module(agent_foundry_names: dict[str, str]) -> str:
    """Returns the real, deterministic ``agent_config.py`` module content mapping
    every one of this mission's own logical agent names (specialists and the
    orchestrator itself) to their real, already-provisioned Foundry agent
    names - never LLM-authored, so the generated ``orchestrator.py`` always has
    a real, non-hardcoded source of truth for which Foundry agent to call."""

    return _AGENT_CONFIG_PY_TEMPLATE.format(agent_foundry_names=agent_foundry_names)


def generate_backend_service_scaffold(
    *,
    mission_title: str,
    orchestrator_agent_name: str,
    agent_foundry_names: dict[str, str],
) -> dict[str, str]:
    """Returns the real, deterministic mission backend service scaffold.

    This includes ``mission_foundry_runtime.py``, the stable boundary between
    LLM-generated orchestration logic and version-sensitive SDK behavior. The
    scaffold is
    never LLM-authored, so every deployed mission's backend proxy is
    consistent and auditable."""

    return {
        "main.py": _MAIN_PY_TEMPLATE.format(
            mission_title=f"{mission_title} Backend",
            orchestrator_agent_name=orchestrator_agent_name,
            authentication_middleware="",
        ),
        "requirements.txt": _REQUIREMENTS_TXT,
        "Dockerfile": _DOCKERFILE,
        "agent_config.py": generate_agent_config_module(agent_foundry_names),
        "mission_foundry_runtime.py": _MISSION_FOUNDRY_RUNTIME_PY,
    }
