"""MCP tools for the maze game HTTP service."""

def main() -> None:
    from .server import main as serve
    serve()
