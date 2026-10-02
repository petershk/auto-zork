"""Compatibility launcher. Run with uv run python server.py."""
from maze_mcp.server import mcp, main  # noqa: F401  (mcp is exposed for `mcp dev server.py`)

if __name__ == "__main__":
    main()
