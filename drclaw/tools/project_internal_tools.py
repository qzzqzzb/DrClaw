"""Project-internal routing tools used by project manager agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from drclaw.models.project import StudentAgentConfig
from drclaw.tools.base import Tool


class RouteToStudentTool(Tool):
    """Route a message from a project manager to one of its student agents."""

    def __init__(
        self,
        students: list[StudentAgentConfig],
        router: Callable[[StudentAgentConfig, str], Awaitable[str]],
    ) -> None:
        self._students = {student.id: student for student in students}
        self._router = router

    @property
    def name(self) -> str:
        return "route_to_student"

    @property
    def description(self) -> str:
        return (
            "Route a message to a student agent in the current project. "
            "IMPORTANT: After calling this tool, you MUST NOT perform the delegated "
            "work yourself. Do not run searches, write files, or execute commands "
            "related to the routed task. Do NOT poll for status with "
            "list_background_tool_tasks, list_active_jobs, or exec/sleep. "
            "Wait for the student agent to report back automatically."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        student_ids = sorted(self._students)
        return {
            "type": "object",
            "properties": {
                "student_id": {
                    "type": "string",
                    "description": (
                        "Configured student agent id within the current project. "
                        "Use exactly one of the listed ids."
                    ),
                    "enum": student_ids,
                },
                "message": {
                    "type": "string",
                    "description": "Instruction to send to that student agent",
                },
            },
            "required": ["student_id", "message"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        student_id = str(params["student_id"]).strip()
        message = str(params["message"])
        student = self._students.get(student_id)
        if student is None:
            available = ", ".join(sorted(self._students)) or "(none)"
            return (
                f"Error: student agent {student_id!r} not found in this project. "
                f"Available: {available}"
            )
        if not student.enabled:
            return f"Error: student agent {student_id!r} is disabled."
        return await self._router(student, message)
