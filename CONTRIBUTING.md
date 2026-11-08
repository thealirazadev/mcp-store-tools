# Contributing

Issues and pull requests are welcome. For a new tool or a behaviour change, open an issue first so
the design can be agreed before you write code; the tool surface is kept small on purpose
([docs/decisions.md](docs/decisions.md), decision 3).

## Setup

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Try your change against a real store without credentials:

```bash
uv run mcp-store-tools --demo                                  # stdio
MCP_STORE_TOOLS_LIVE=1 uv run pytest tests/test_live.py        # end-to-end, stdio and HTTP
```

## Pull requests

- One change per pull request, with tests. The default test run must not call the network.
- If you add, rename, or remove a tool, update `skill/shopify-store-assistant/SKILL.md`, the
  README tool table, and `CHANGELOG.md`; `tests/test_skill.py` fails until the skill matches.
- Commit messages are short and imperative (`fix: handle empty policy body`).
