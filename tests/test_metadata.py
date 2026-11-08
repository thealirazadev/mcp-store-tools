"""Registry metadata must match the package it describes."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_server_json_matches_the_package() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    server = json.loads((ROOT / "server.json").read_text())
    package = server["packages"][0]

    assert server["version"] == package["version"] == project["version"]
    assert package["identifier"] == project["name"]
    assert f"mcp-name: {server['name']}" in (ROOT / "README.md").read_text()
