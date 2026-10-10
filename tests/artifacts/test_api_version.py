"""The web app and the API agree on the API version, so a stale server is detected, not guessed."""

from __future__ import annotations

import re

from pac.interfaces.api import API_VERSION
from pac.settings import REPO_ROOT


def test_web_app_expects_the_api_version_the_server_reports() -> None:
    source = (REPO_ROOT / "web" / "src" / "api.ts").read_text()
    match = re.search(r"export const API_VERSION = (\d+);", source)
    assert match is not None, "web/src/api.ts must declare API_VERSION"
    assert int(match.group(1)) == API_VERSION
