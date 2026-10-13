"""Lessons-learned artifact: formalizes the "diagnosed failure -> prompt
registry change candidate" feedback loop (Genie platform-change Phase F).

Problem this exists to close: earlier in this platform's own development,
a real bug's root cause (a generated prototype's field silently dropped
between its Orchestrator and a tool wrapper) was eventually traced back to
a gap in a prompt's own instructions, and config/prompts/registry.yaml was
updated to fix it - but only because someone remembered to connect those
two things by hand. The Debugging Agent (``failure-diagnosis-v1``,
``app.orchestration.debugging_workflow_service``) already diagnoses a root
cause for every detected failure; nothing previously captured whether that
diagnosis implicated a specific prompt, or made that queryable as a
concrete backlog of candidate prompt/config changes for a human to review
and apply (through the normal git/PR process - this is deliberately never
an automatic registry.yaml edit, since config/prompts/registry.yaml is
loaded once at process startup, and because prompt content is reviewed
exactly like any other change to agent behavior).

Genie never invents or overrides the agent's own diagnosis here - this
only reports, as structured data, what the Debugging Agent already stated
in its own output text (see
``app.orchestration.debugging_workflow_service._parse_lessons_learned``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["LessonLearned", "LessonLearnedStatus"]

LessonLearnedStatus = Literal["open", "applied", "dismissed"]
"""
- open: a candidate prompt/config change has not yet been reviewed.
- applied: a human reviewed it and made the corresponding registry change.
- dismissed: a human reviewed it and decided no change was warranted.
"""


class LessonLearned(BaseModel):
    """One prompt/config change candidate extracted from a diagnosed failure."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    diagnosed_at: datetime
    suggested_prompt_id: str | None = Field(
        default=None,
        description=(
            "The prompt_id the Debugging Agent's own diagnosis named as the "
            "source of the gap, if any - never inferred or guessed by Genie "
            "itself."
        ),
    )
    suggested_change: str = Field(
        min_length=1,
        description="The Debugging Agent's own stated suggested change, verbatim.",
    )
    status: LessonLearnedStatus = "open"
