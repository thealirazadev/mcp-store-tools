"""Render docs/demo.svg: an animated terminal replay of examples/demo.py's real output.

uv run python scripts/render_demo.py
"""

import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMMAND = "uv run python examples/demo.py"
LINE, PAD, TOP, WIDTH = 20, 18, 44, 780
TYPE_SECONDS, LINE_SECONDS, HOLD_SECONDS = 1.6, 0.18, 6.0


def demo_output() -> list[str]:
    run = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "demo.py")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    text = re.sub(r"\x1b\[[0-9;]*m", "", run.stdout)
    return text.strip("\n").splitlines()


def render(lines: list[str]) -> str:
    height = TOP + LINE * (len(lines) + 2) + PAD
    starts = [TYPE_SECONDS + 0.4 + LINE_SECONDS * i for i in range(len(lines))]
    total = starts[-1] + HOLD_SECONDS

    def pct(seconds: float) -> str:
        return f"{100 * seconds / total:.2f}%"

    styles = [
        "text{font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:#d4d4d8;"
        "white-space:pre}",
        ".call{fill:#67e8f9;font-weight:600}.prompt{fill:#a3e635}",
        f"#cmd{{animation:type {total:.2f}s steps({len(COMMAND)}) infinite}}",
        f"@keyframes type{{0%{{width:0}}{pct(TYPE_SECONDS)}{{width:{len(COMMAND) * 9 + 40}px}}"
        f"100%{{width:{len(COMMAND) * 9 + 40}px}}}}",
    ]
    rows = []
    for i, (line, start) in enumerate(zip(lines, starts, strict=True)):
        styles.append(
            f".l{i}{{opacity:0;animation:l{i} {total:.2f}s infinite}}"
            f"@keyframes l{i}{{0%,{pct(start - 0.01)}{{opacity:0}}{pct(start)},97%{{opacity:1}}"
            f"100%{{opacity:0}}}}"
        )
        css = "call" if line.startswith(">") else ""
        y = TOP + LINE * (i + 2)
        rows.append(f'<text x="{PAD}" y="{y}" class="l{i} {css}">{html.escape(line)}</text>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" \
viewBox="0 0 {WIDTH} {height}" role="img" aria-label="mcp-store-tools demo">
<title>mcp-store-tools answering catalog and policy questions from a live Shopify store</title>
<style>{"".join(styles)}</style>
<rect width="{WIDTH}" height="{height}" rx="10" fill="#18181b"/>
<circle cx="20" cy="18" r="6" fill="#f87171"/><circle cx="40" cy="18" r="6" fill="#fbbf24"/>
<circle cx="60" cy="18" r="6" fill="#4ade80"/>
<text x="{WIDTH / 2}" y="22" text-anchor="middle" style="fill:#71717a;font-size:12px">\
mcp-store-tools --demo (Shopify sample store)</text>
<clipPath id="typing">\
<rect id="cmd" x="0" y="{TOP + LINE // 2 - 16}" height="24" width="0"/></clipPath>
<text x="{PAD}" y="{TOP + LINE // 2}" clip-path="url(#typing)"><tspan class="prompt">$ </tspan>\
{html.escape(COMMAND)}</text>
{chr(10).join(rows)}
</svg>
"""


if __name__ == "__main__":
    out = ROOT / "docs" / "demo.svg"
    out.write_text(render(demo_output()))
    print(f"wrote {out.relative_to(ROOT)}")
