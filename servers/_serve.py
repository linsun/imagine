"""Run an MCP server over stdio (laptop) or streamable HTTP (Kubernetes).

On the laptop agentgateway spawns each server as a stdio child. In the cluster
each server is its own Deployment and the gateway reaches it over HTTP, so the
same module serves both:  MCP_TRANSPORT=http MCP_PORT=8000 python -m servers.x
"""

import os


def serve(mcp) -> None:
    if os.environ.get("MCP_TRANSPORT", "stdio") == "http":
        mcp.run(transport="http", host="0.0.0.0",
                port=int(os.environ.get("MCP_PORT", "8000")), path="/mcp")
    else:
        mcp.run()
