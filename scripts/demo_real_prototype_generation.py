"""One-off DEMO script (not part of the application): prove real, LLM-backed
Azure AI Foundry agent execution end-to-end for the sample transcript, using
the exact same production code path AzureAgentGateway/WorkflowStepExecutor
use in the real app - just invoked directly here instead of through the
FastAPI HTTP layer, so it can run without a full Entra ID browser login.

Chains 4 real agent calls (matching config/workflows/registry.yaml's actual
agent_id/prompt_id/variable_sources mappings for these steps):
  1. requirements-analyst   (requirements-extraction-v1)
  2. architecture-designer  (architecture-recommendation-v1)
  3. solution-architect-agent (business-agent-workshop-v1)
  4. ui-designer-agent      (prototype-generation-v1)  <- the real prototype HTML

Writes the final HTML to scripts/.generated_prototype.html for viewing, and
prints every intermediate agent's real output to stdout.

Usage (from repo root):
    python scripts/demo_real_prototype_generation.py \
        --endpoint https://genie-i4opvs55x5qu4-foundry.services.ai.azure.com/api/projects/genie-i4opvs55x5qu4-project
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# Windows consoles often default to a non-UTF-8 codepage (e.g. cp1252), which
# raises UnicodeEncodeError when LLM output contains characters like em-dashes
# or smart quotes. Reconfigure stdout/stderr to UTF-8 (with replacement for any
# truly unencodable bytes) so real model output never crashes this demo script.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.agents.azure_agent_gateway import AzureAgentGateway
from app.agents.foundry.agent_provider import FoundryAgentProvider
from app.agents.foundry.project_service import FoundryProjectService
from app.agents.models import AgentExecutionRequest
from app.agents.registry import AgentRegistry
from app.config.settings import get_settings
from app.prompts.registry import PromptRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_PATH = REPO_ROOT / "sample" / "business_problem_document_processing_transcript.txt"
OUTPUT_HTML_PATH = Path(__file__).resolve().parent / ".generated_prototype.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="Azure AI Foundry project endpoint.")
    parser.add_argument("--project", required=True, help="Azure AI Foundry project name.")
    args = parser.parse_args()

    settings = get_settings()
    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
    prompt_registry = PromptRegistry.load(settings.prompts_path)

    project_service = FoundryProjectService(endpoint=args.endpoint, project_name=args.project)
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=FoundryAgentProvider(project_service),
    )

    transcript_excerpt = TRANSCRIPT_PATH.read_text(encoding="utf-8")

    import asyncio

    async def run() -> None:
        print(f"=== Step 1/4: requirements-analyst (real Foundry agent call) ===")
        req1 = AgentExecutionRequest(
            agent_id="requirements-analyst",
            prompt_id="requirements-extraction-v1",
            variables={"transcript_excerpt": transcript_excerpt, "user_message": ""},
            correlation_id=str(uuid.uuid4()),
        )
        result1 = await gateway.execute(req1)
        print(result1.output_text)
        print()

        print(f"=== Step 2/4: architecture-designer (real Foundry agent call) ===")
        req2 = AgentExecutionRequest(
            agent_id="architecture-designer",
            prompt_id="architecture-recommendation-v1",
            variables={"approved_requirements": result1.output_text, "user_message": ""},
            correlation_id=str(uuid.uuid4()),
        )
        result2 = await gateway.execute(req2)
        print(result2.output_text)
        print()

        print(f"=== Step 3/4: solution-architect-agent (real Foundry agent call) ===")
        req3 = AgentExecutionRequest(
            agent_id="solution-architect-agent",
            prompt_id="business-agent-workshop-v1",
            variables={"context": result2.output_text, "user_message": ""},
            correlation_id=str(uuid.uuid4()),
        )
        result3 = await gateway.execute(req3)
        print(result3.output_text)
        print()

        print(f"=== Step 4/4: ui-designer-agent (real Foundry agent call - the prototype) ===")
        req4 = AgentExecutionRequest(
            agent_id="ui-designer-agent",
            prompt_id="prototype-generation-v1",
            variables={"solution_architecture": result3.output_text},
            correlation_id=str(uuid.uuid4()),
        )
        result4 = await gateway.execute(req4)
        print(result4.output_text[:500], "... (truncated in console)")

        OUTPUT_HTML_PATH.write_text(result4.output_text, encoding="utf-8")
        print(f"\nFull prototype HTML written to: {OUTPUT_HTML_PATH}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
