"""DrClaw tool system: base classes, registry, and built-in tools."""

from drclaw.tools.base import Tool
from drclaw.tools.cron import CronTool
from drclaw.tools.equipment_admin_tools import (
    AddEquipmentTool,
    AddLocalHubSkillsToProjectTool,
    AddLocalHubSkillsToProjectStudentTool,
    ListEquipmentsTool,
    RemoveEquipmentTool,
)
from drclaw.tools.external_agent_tools import CallExternalAgentTool
from drclaw.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from drclaw.tools.message import MessageTool
from drclaw.tools.project_tools import (
    CreateProjectTool,
    ListProjectsTool,
    RemoveProjectTool,
    RouteToProjectTool,
)
from drclaw.tools.registry import ToolRegistry
from drclaw.tools.shell import ExecTool
from drclaw.tools.web import WebFetchTool, WebSearchTool

__all__ = [
    "Tool",
    "ToolRegistry",
    "CronTool",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirTool",
    "ExecTool",
    "WebSearchTool",
    "WebFetchTool",
    "MessageTool",
    "AddEquipmentTool",
    "AddLocalHubSkillsToProjectTool",
    "AddLocalHubSkillsToProjectStudentTool",
    "ListEquipmentsTool",
    "RemoveEquipmentTool",
    "RouteToProjectTool",
    "CallExternalAgentTool",
    "CreateProjectTool",
    "ListProjectsTool",
    "RemoveProjectTool",
]
