import os
import httpx
from typing import Literal
from mcp.server import MCPServer


mcp = MCPServer("maze-tools")
BASE_URL = os.environ.get("MAZE_GAME_URL", "http://127.0.0.1:5000").rstrip("/")

@mcp.tool()
def look() -> dict:
    """Look around the current room: description, exits, visible items, inventory, and player stats. Costs one turn."""
    response = httpx.post(f"{BASE_URL}/api/look", timeout=5.0)
    response.raise_for_status()
    state = response.json()
    state.pop("grid", None)
    return state

@mcp.tool()
def move(direction: Literal["north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest", "up", "down", "in", "out", "land", "launch"]) -> dict:
    """Follow an available room exit. Puzzle gates can block passages; use the allowed_actions vocabulary and interact. Passages may be one-way."""
    response = httpx.post(f"{BASE_URL}/api/move",
    json={"direction": direction},
    timeout=5.0,
    )
    response.raise_for_status()
    return response.json()

@mcp.tool()
def reset() -> dict:
    """Request the maze to its initial state."""
    response = httpx.post(f"{BASE_URL}/api/reset", timeout=5.0)
    response.raise_for_status()
    return response.json()


def _item_request(action: str, item: str, container: str | None = None) -> dict:
    body = {"item": item}
    if container is not None:
        body["container"] = container
    response = httpx.post(f"{BASE_URL}/api/items/{action}", json=body, timeout=5.0)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def inventory() -> dict:
    """List carried items and container contents. Costs one turn, like the original INVENTORY command."""
    response = httpx.post(f"{BASE_URL}/api/inventory", timeout=5.0)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def take(item: str) -> dict:
    """Take a reachable portable item. Load limit: 100 units including contents. Above 7 loose items, pickup can fumble. Open containers first. Costs one turn."""
    return _item_request("take", item)


@mcp.tool()
def drop(item: str) -> dict:
    """Drop a carried item in the current room. Dropping a container also moves its contents."""
    return _item_request("drop", item)


@mcp.tool()
def examine(item: str) -> dict:
    """Inspect a visible or carried item. Return its description, readable text, and any visible contents."""
    return _item_request("examine", item)


@mcp.tool()
def open_container(item: str) -> dict:
    """Open a reachable container to reveal and access its contents. This does not change room exits."""
    return _item_request("open_container", item)


@mcp.tool()
def close_container(item: str) -> dict:
    """Close a reachable container. Items inside remain inside it."""
    return _item_request("close_container", item)


@mcp.tool()
def put(item: str, container: str) -> dict:
    """Put a carried item inside an open container or on a surface, respecting capacity. Depositing treasure in TROPHY-CASE adds storage points; removing it subtracts them. Costs one turn."""
    return _item_request("put", item, container)


@mcp.tool()
def score() -> dict:
    """Report points, rank, turns, and carrying limits without consuming a turn. Includes room/item discovery and current trophy-case treasure points."""
    response = httpx.get(f"{BASE_URL}/api/score", timeout=5.0)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def wait() -> dict:
    """Wait for one turn to pass without moving or changing items."""
    response = httpx.post(f"{BASE_URL}/api/wait", timeout=5.0)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def interact(action: str, target: str, item: str = "") -> dict:
    """Attempt a command from look.allowed_actions on an object or nearby feature name. The vocabulary does not tell you which action works. item is a phrase for say or a carried item ID for give; other actions automatically check required equipment. Costs one turn; returns success, clues, changed exits and state."""
    response = httpx.post(f"{BASE_URL}/api/interact", json={"action": action, "target": target, "item": item}, timeout=5.0)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def hint() -> dict:
    """Request an optional hint for the current room. Every request increases hints_used, including repeats. No turn or point cost. Normal look contains no solution hints."""
    response = httpx.post(f"{BASE_URL}/api/hint", timeout=5.0)
    response.raise_for_status()
    return response.json()


def main() -> None:
    """Run the stdio MCP server; protocol output owns stdout."""
    mcp.run(transport="stdio")
