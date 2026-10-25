"""Run PHOS as the single runtime owner with its trusted-local MCP stdio adapter."""
from __future__ import annotations

import sys

from robot.main import main


if __name__ == "__main__":
    if "--mcp-stdio" not in sys.argv:
        sys.argv.append("--mcp-stdio")
    main()
