"""The packaged skill must match the tools the server actually serves."""

import re
from pathlib import Path

from mcp import Client

from mcp_store_tools.server import build_server
from mcp_store_tools.storefront import Config, Storefront

SKILL_DIR = Path(__file__).parent.parent / "skill" / "shopify-store-assistant"
SKILL = (SKILL_DIR / "SKILL.md").read_text()


def frontmatter() -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", SKILL, re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    return dict(line.split(": ", 1) for line in match.group(1).splitlines())


def test_frontmatter_follows_the_skill_spec() -> None:
    meta = frontmatter()
    assert set(meta) == {"name", "description"}
    assert meta["name"] == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", meta["name"])
    assert 0 < len(meta["description"]) <= 1024


async def test_skill_documents_exactly_the_served_tools() -> None:
    server = build_server(Storefront(Config("demo.myshopify.com", "t")))
    async with Client(server) as client:
        served = {tool.name for tool in (await client.list_tools()).tools}
    mentioned = set(re.findall(r"`([a-z_]+)\(", SKILL))
    assert mentioned == served


def test_reference_links_resolve() -> None:
    for path in SKILL_DIR.rglob("*.md"):
        for link in re.findall(r"\]\(((?!https?:)[^)]+)\)", path.read_text()):
            assert (SKILL_DIR / link).exists(), f"{path.name} links to missing {link}"
