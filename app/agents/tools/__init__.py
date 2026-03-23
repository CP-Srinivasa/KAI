"""MCP tool group modules.

Submodules:
- canonical_read: Read-only MCP tool definitions and inventory
- guarded_write: Write-guarded MCP tool definitions (paper/shadow only, audit-logged)

The actual tool implementations remain registered in app.agents.mcp_server.
These modules export the tool inventories and can serve as migration targets
for incrmental tool extraction in future sprints.
"""
