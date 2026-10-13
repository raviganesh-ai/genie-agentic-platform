"""Unit tests for LessonsLearnedRepository's in-memory implementation
(Genie platform-change Phase F)."""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.lessons_learned import LessonLearned
from app.repositories.lessons_learned_repository import InMemoryLessonsLearnedRepository


def _lesson(**overrides: object) -> LessonLearned:
    defaults: dict[str, object] = {
        "id": "lesson-1",
        "session_id": "session-1",
        "workflow_run_id": "run-1",
        "diagnosed_at": datetime.now(UTC),
        "suggested_prompt_id": "build-generation-v1",
        "suggested_change": "Add an explicit field-forwarding requirement.",
    }
    defaults.update(overrides)
    return LessonLearned(**defaults)  # type: ignore[arg-type]


async def test_list_open_returns_only_open_lessons_in_insertion_order():
    repository = InMemoryLessonsLearnedRepository()
    await repository.put(_lesson(id="lesson-1"))
    await repository.put(_lesson(id="lesson-2", status="dismissed"))
    await repository.put(_lesson(id="lesson-3"))

    open_lessons = await repository.list_open()

    assert [lesson.id for lesson in open_lessons] == ["lesson-1", "lesson-3"]


async def test_put_replaces_an_existing_lesson_by_id():
    repository = InMemoryLessonsLearnedRepository()
    await repository.put(_lesson(id="lesson-1"))
    await repository.put(_lesson(id="lesson-1", status="applied"))

    assert await repository.list_open() == []
