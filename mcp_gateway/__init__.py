"""SmartHub MCP gateway.

Kept as a separate process from the Flask monolith so MCP can evolve and be
rolled back without putting the production Hub web app at risk.
"""
