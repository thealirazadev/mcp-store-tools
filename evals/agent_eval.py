"""Ask a real agent (Claude Code, headless) shopper and merchant questions through this server
and check it picks the expected tools. Costs a few cents per case on your Claude plan.

    uv run python evals/agent_eval.py                 # mock.shop cases only
    SHOPIFY_STORE_DOMAIN=... SHOPIFY_STOREFRONT_TOKEN=... uv run python evals/agent_eval.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = (ROOT / "skill" / "shopify-store-assistant" / "SKILL.md").read_text()
DENY = ["Bash", "Write", "Edit", "WebFetch", "WebSearch", "Task", "Agent"]


def server_config(store: str) -> dict[str, object]:
    args = ["run", "--directory", str(ROOT), "mcp-store-tools"]
    if store == "mock":
        return {"command": "uv", "args": [*args, "--demo"]}
    env = {k: os.environ[k] for k in ("SHOPIFY_STORE_DOMAIN", "SHOPIFY_STOREFRONT_TOKEN")}
    return {"command": "uv", "args": args, "env": env}


def run(case: dict[str, object], model: str) -> tuple[list[str], str, float]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as config:
        json.dump({"mcpServers": {"store": server_config(str(case["store"]))}}, config)
    try:
        output = subprocess.run(
            [
                "claude",
                "-p",
                str(case["question"]),
                "--model",
                model,
                "--mcp-config",
                config.name,
                "--strict-mcp-config",
                "--allowedTools",
                "mcp__store",
                "--disallowedTools",
                *DENY,
                "--append-system-prompt",
                SKILL,
                "--max-budget-usd",
                "0.6",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
        ).stdout
    finally:
        os.unlink(config.name)
    tools, answer, cost = [], "", 0.0
    for line in output.splitlines():
        event = json.loads(line)
        if event.get("type") == "assistant":
            for block in event["message"]["content"]:
                if block.get("type") == "tool_use" and block["name"].startswith("mcp__store__"):
                    tools.append(block["name"].removeprefix("mcp__store__"))
        if event.get("type") == "result":
            answer, cost = event.get("result") or "", event.get("total_cost_usd") or 0.0
    return tools, answer, cost


def main() -> int:
    cases = json.loads((Path(__file__).parent / "cases.json").read_text())
    if not os.environ.get("SHOPIFY_STOREFRONT_TOKEN"):
        cases = [c for c in cases if c["store"] == "mock"]
    model = os.environ.get("EVAL_MODEL", "sonnet")
    passed, total_cost = 0, 0.0
    for case in cases:
        tools, answer, cost = run(case, model)
        ok = all(any(t in tools for t in alt.split("|")) for alt in case["expect_tools"])
        passed += ok
        total_cost += cost
        print(f"{'PASS' if ok else 'FAIL'}  {case['question']}\n      tools: {tools}  ${cost:.2f}")
        print(f"      {' '.join(answer.split())[:200]}\n")
    print(f"{passed}/{len(cases)} passed, ${total_cost:.2f}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
