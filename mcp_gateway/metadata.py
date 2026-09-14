"""Shared metadata for SmartHub's externally exposed MCP tools.

These annotations describe the current tool contract to MCP hosts. They are
advisory metadata, not an authorization boundary; bearer authentication and
server-side role checks remain authoritative.

Keep this module dependency-free: v2_tools is also imported by the main Hub,
whose runtime intentionally does not install the separately deployed MCP SDK.
The MCP server validates this protocol-shaped mapping when registering tools.
"""
from __future__ import annotations


READ_ONLY_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
