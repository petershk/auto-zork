"""Discover and exercise maze tools through a separate MCP server process."""

import asyncio
import json
import sys
import argparse
import os

from mcp import Client, StdioServerParameters


async def run(demo: bool = False) -> None:
    # Launch the server with the same Python environment as this client.
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "maze_mcp"],
        env={"MAZE_GAME_URL": os.environ.get("MAZE_GAME_URL", "http://127.0.0.1:5000")},
    )

    # Entering opens the MCP connection; leaving closes the server process.
    async with Client(server) as client:
        discovered = await client.list_tools()
        print("Discovered tools:")
        for tool in discovered.tools:
            print(f"  {tool.name}: {tool.description}")
            print("  Arguments:", json.dumps(tool.input_schema))

        # GPTAgent accepts this format and prepares it for the model itself.
        # Copy the descriptions and schemas from MCP rather than maintaining
        # a second, handwritten list of tools.
        agent_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
            for tool in discovered.tools
        ]
        print("\nTools in the format GPTAgent accepts:")
        print(json.dumps(agent_tools, indent=2))

        if not demo:
            return

        calls = [
            ("reset", {}),
            ("look", {}),
            ("move", {"direction": "north"}),
            ("look", {}),
        ]
        for name, arguments in calls:
            result = await client.call_tool(name, arguments)
            print(f"\n{name}({json.dumps(arguments)}):")
            if result.is_error:
                raise RuntimeError(f"Tool failed: {result.content}")
            if result.structured_content is not None:
                print(json.dumps(result.structured_content, indent=2))
            else:
                # Some servers return JSON in text content instead.
                for content in result.content:
                    if content.type == "text":
                        print(content.text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover maze MCP tools without changing the game.")
    parser.add_argument("--demo", action="store_true", help="Reset the game and exercise look/move tools.")
    options = parser.parse_args()
    asyncio.run(run(options.demo))


if __name__ == "__main__":
    main()
