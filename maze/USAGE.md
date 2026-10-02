# Using the dashboard

How the web app behaves while you play or watch an agent. For setup, see the top-level README; for the
game's rules, see PUZZLES.md and ZORK_NOTES.txt.

## Playing, saving and auto play

Typing sounds begin after a browser interaction. Volume controls both typing and score sounds; Mute silences both. The first screen welcomes the player without issuing a LOOK command. During an agent run, Stop Agent, Give Hint, Save game (Ctrl+S) and the sound controls remain available; other game and configuration controls are disabled. Restoring a save needs the agent stopped. An `autosave` slot is written whenever an agent run ends.

Press **Auto play** to watch a scripted perfect run (see the top-level README). It starts a new game, locks the
manual controls, and leaves Stop and Save available. The first time a run finishes, the status line shows the
final score; use Reset to play again.

## Agents and providers

The agent supports OpenAI, xAI (Grok), Anthropic (Claude), Google (Gemini) and OpenRouter, plus any OpenAI-compatible endpoint through "Other" with a custom Base URL. Providers with a Responses API (OpenAI, xAI) use it directly; Anthropic, Google and OpenRouter go through a Chat Completions adapter (`chat_adapter.py`). Anthropic and Google describe their OpenAI-compatible endpoints as intended for testing, so behaviour can differ from their native APIs. Model names change over time: use Load Models to see what your key can use. MCP itself is independent of the model provider.

## API keys and settings

Manual play and MCP do not need an AI provider key. Saved games go to `maze/saves/` (ignored by git). If no key is configured, the page opens Configure agent and explains how to enter one. Run Agent and Load Models prompt for it before making provider requests. A typed key applies to that session; Save API key locally writes the key to `maze/.env` under the selected provider's own variable (`OPENAI_API_KEY` or `MAZE_AGENT_API_KEY` for OpenAI and Other, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`), preserving other settings. A key is only ever sent to its own provider. The last provider and model you used are remembered (`MAZE_AGENT_PROVIDER` and `MAZE_AGENT_MODEL_<PROVIDER>`); with none remembered, the provider is guessed from whichever provider-specific key is saved. The textbox is cleared after saving, and neither the page nor status responses return the stored key. The file stores plain text locally and is excluded by `.gitignore`; do not bundle it in a release. `maze/.env.example` contains only placeholders.

The default maximum agent steps is 2000, overridable in the UI or by `MAZE_AGENT_MAX_STEPS`. A model decision can include several tool actions, so agent steps and game turns are separate counters.

## Costs and token counts

The agent dashboard counts API-reported input, cached input, output, and reasoning tokens per request, including the connection check and final completion message. Reasoning tokens are part of output; cached tokens are part of input. Counters apply to new runs; earlier runs cannot be reconstructed. Unknown prices or missing usage show an unavailable full estimate, with the known subtotal retained. Optional rates override automatic estimates for another provider.

Built-in standard text estimates use the official model pages for [GPT-4o mini](https://developers.openai.com/api/docs/models/gpt-4o-mini), [GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o), [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1), and [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), checked 2026-09-30. Estimates exclude taxes, nonstandard service tiers, and billing adjustments.

GPT-3.5 Turbo and gpt-3.5-turbo-0125 estimates use the [official standard rates](https://developers.openai.com/api/docs/models/gpt-3.5-turbo), checked 2026-09-30. No cached-input discount is assumed where no rate is published. Unpriced request details explain which pricing information is missing.

## Long runs and agent memory

Long agent runs refresh their model context every 12 steps (configurable). Only observed game facts, passages, and recent actions are carried into a fresh response chain. A context-length error triggers one recovery retry without replaying game commands. This bounded memory can omit older details; it does not reset the world or reveal puzzle solutions. Models whose context cannot fit the tools and memory may still need a shorter interval or a larger context window.

## Hints and notes

Viewers can send advice with the Give Hint button while a run is active. Hints are queued for the next model request, recorded in the reasoning display when received, and retained in recent context memory. These viewer messages do not spend game turns or increment the built-in game hint counter. The status API reports their separate `viewer_hints` count. Pending hints are cleared when a run ends or a new one begins. The Hints & agent notes panel lists every hint given (yours to the agent, yours to the game, and the agent's own requests), what the agent has in memory, and its recent actions. The game's hint command follows the agent's progress, not its location.

## Trophy case progress

The inventory panel also shows trophy-case progress: stored treasure names, count, and deposit points. Removing a treasure reduces this progress, including when its containing bag is removed. This passive viewer summary does not reveal uncollected treasure names to the model.
