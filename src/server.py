"""Entry point for the `mcp_f1_strategy` MCP server.

Registers the 9 F1 strategy tools and serves them over stdio, so it can be
launched as a subprocess by `host_mcp_redes` (or any other MCP host).
"""

from __future__ import annotations

from pathlib import Path

from jsonrpc_mcp import MCPServer

from tools.f1_tools import register_tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "cache"

server = MCPServer(
    name="f1-strategy",
    instructions=(
        "Tools for an F1 race strategy engineer: pit windows, undercut/overcut "
        "simulation, tire degradation modeling, and finish-position projection, "
        "computed from real FastF1 session data. Note: FastF1 only exposes "
        "completed sessions, not live timing — a running race is simulated by "
        "replaying a real session lap by lap via the `current_lap`/`lap` parameters."
    ),
)

register_tools(server, str(CACHE_DIR))


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
