"""SmartHub MCP V2 entry point.

The tested V1 server owns transport, authentication, health checks and the ASGI
app. V2 only registers additional read-only tools onto the same MCP registry.
Keeping this as a separate entry point lets staging exercise V2 without changing
the known-good V1 deployment command.
"""
from mcp_gateway.server import SERVER_NAME, app, mcp  # noqa: F401
from mcp_gateway.v2_tools import register

register(mcp)
