# Maze MCP

The stdio MCP server exposes the running maze game's HTTP commands as discoverable tools. It does not own game state or start Flask. Tool discovery works without Flask; executing a game tool requires Flask to be running.

The game is exposed as 14 tools (look, move, take, drop, examine, open and close containers, put, inventory,
score, wait, hint, reset and interact). The dashboard, providers, costs and saving are described in
[../maze/USAGE.md](../maze/USAGE.md); the top-level README has the quick start.

## Try it

Start the game first (`uv run python web_app.py` in `maze/`, see the top-level README). The web agent launches this
package itself with the same Python interpreter, so no separate MCP environment is needed.

For a standalone client, in another terminal in `maze_mcp/`:

```sh
uv sync --locked
uv run maze-mcp-client
```

The client prints discovered tools without changing the game. `uv run maze-mcp-client --demo` resets the game and executes a few example commands. `uv run maze-mcp` starts the stdio server; it waits for an MCP client rather than opening a web page. `python -m maze_mcp` works in any environment where the package is installed.

## Connect another MCP host

Configure a stdio server with `command` set to the absolute path of the Python interpreter where maze-mcp is installed, and `args` set to `["-m", "maze_mcp"]`. There is no working-directory requirement. Alternatively use the installed `maze-mcp` executable. Keep stdout reserved for MCP messages.

Set `MAZE_GAME_URL` in the server's environment to change the game endpoint (default `http://127.0.0.1:5000`). For a web app on port 5001, set both `MAZE_PORT=5001` and `MAZE_GAME_URL=http://127.0.0.1:5001` before starting it. The subprocess inherits this environment. Keep the local game bound to loopback; the game HTTP API has no authentication.

## Distribution checks

`uv build` creates a wheel containing the server, client, and launchers. The root `server.py` and `client.py` are compatibility wrappers for checkout users. The SDK is constrained to MCP 2.x; use the committed lockfiles for reproducible installations. A published standalone MCP package still needs a separately running game service.

Do not include `.env`, API keys, `.venv`, cache files, or local logs in a release. Preserve `maze/ZORK_LICENSE.txt` when distributing the Zork-derived game data.

The default Flask launcher disables the development debugger and reloader to avoid Windows reload/socket failures. Restart it after source changes. Flask's built-in server is intended for local play; hosted deployment requires a production server and access controls.
