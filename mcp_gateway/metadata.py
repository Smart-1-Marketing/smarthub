"""Shared metadata for SmartHub's externally exposed MCP tools.

These annotations describe the current tool contract to MCP hosts. They are
advisory metadata, not an authorization boundary; bearer authentication and
server-side role checks remain authoritative.
"""
from __future__ import annotations

from mcp.types import ToolAnnotations


READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
