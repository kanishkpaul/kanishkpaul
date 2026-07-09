from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


REPO_OWNER = "kanishkpaul"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
API_ROOT = "https://api.github.com"


@dataclass(frozen=True)
class FeaturedRepo:
    name: str
    description: str
    language: str


FEATURED_REPOS: tuple[FeaturedRepo, ...] = (
    FeaturedRepo(
        name="sefai",
        description="a safe environment for AI — Rust CLI for running local GGUF models via llama.cpp bindings",
        language="Rust",
    ),
    FeaturedRepo(
        name="chromeclaw",
        description="terminal-first browser agent with visible traces, explicit safety gates, and a small eval loop",
        language="TypeScript",
    ),
    FeaturedRepo(
        name="reasontrace",
        description="visual debugger for reasoning traces — turns agent logs into an inspectable graph with replay",
        language="TypeScript",
    ),
    FeaturedRepo(
        name="casca",
        description="screenshot-grounded desktop agent scaffold for visual grounding and action-reliability research",
        language="Python",
    ),
    FeaturedRepo(
        name="stockfih",
        description="chess analysis pairing browser-side Stockfish with short natural-language coaching",
        language="TypeScript",
    ),
)


def github_request(path: str) -> dict | list:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "kanishkpaul-readme-stats",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{API_ROOT}{path}", headers=headers)
    with urlopen(request) as response:
        return json.load(response)


def fetch_user() -> dict:
    data = github_request(f"/users/{REPO_OWNER}")
    if not isinstance(data, dict):
        raise TypeError("Expected a user object from GitHub.")
    return data


def fetch_owned_repos() -> list[dict]:
    page = 1
    repos: list[dict] = []
    while True:
        data = github_request(
            f"/users/{REPO_OWNER}/repos?per_page=100&type=owner&sort=updated&page={page}"
        )
        if not isinstance(data, list):
            raise TypeError("Expected a list of repositories from GitHub.")
        if not data:
            return repos
        repos.extend(data)
        page += 1


def build_badge(label: str, value: int, color: str, alt_text: str) -> str:
    encoded_label = quote(label, safe="")
    return (
        f'<img src="https://img.shields.io/badge/{encoded_label}-{value}-{color}'
        f'?style=for-the-badge&logo=github" alt="{alt_text}" />'
    )


def pluralize_stars(stars: int) -> str:
    return "star" if stars == 1 else "stars"


def replace_section(content: str, marker: str, replacement: str) -> str:
    pattern = re.compile(
        rf"(<!-- {re.escape(marker)}:start -->)(.*?)(<!-- {re.escape(marker)}:end -->)",
        re.DOTALL,
    )
    if pattern.search(content) is None:
        raise ValueError(f"Could not find section markers for '{marker}'.")
    return pattern.sub(
        lambda found: f"{found.group(1)}\n{replacement}\n{found.group(3)}",
        content,
        count=1,
    )


def render_profile_badges(user: dict) -> str:
    followers = int(user["followers"])
    public_repos = int(user["public_repos"])
    return "\n".join(
        [
            '  <a href="https://github.com/kanishkpaul">',
            f'    {build_badge("Followers", followers, "111827", f"{followers} GitHub followers")}',
            "  </a>",
            '  <a href="https://github.com/kanishkpaul?tab=repositories">',
            f'    {build_badge("Public Repos", public_repos, "0f172a", f"{public_repos} public repositories")}',
            "  </a>",
        ]
    )


def render_stats_badges(user: dict, total_stars: int) -> str:
    followers = int(user["followers"])
    following = int(user["following"])
    public_repos = int(user["public_repos"])
    return "\n".join(
        [
            f'  {build_badge("Stars Received", total_stars, "111827", f"{total_stars} stars received")}',
            f'  {build_badge("Followers", followers, "0f172a", f"{followers} GitHub followers")}',
            f'  {build_badge("Following", following, "1f2937", f"{following} following")}',
            f'  {build_badge("Public Repos", public_repos, "020617", f"{public_repos} public repositories")}',
        ]
    )


def render_featured_projects(repo_lookup: dict[str, dict]) -> str:
    lines = [
        "| project | why it is cool | signals |",
        "| --- | --- | --- |",
    ]
    for featured_repo in FEATURED_REPOS:
        repo = repo_lookup.get(featured_repo.name)
        if repo is None:
            raise ValueError(f"Featured repo '{featured_repo.name}' was not returned by GitHub.")

        stars = int(repo["stargazers_count"])
        lines.append(
            "| "
            f"[{featured_repo.name}](https://github.com/{REPO_OWNER}/{featured_repo.name}) | "
            f"{featured_repo.description} | "
            f"`{featured_repo.language}` &middot; `{stars} {pluralize_stars(stars)}` |"
        )

    return "\n".join(lines)


def main() -> int:
    try:
        user = fetch_user()
        repos = fetch_owned_repos()
    except HTTPError as error:
        sys.stderr.write(f"GitHub API request failed: {error.code} {error.reason}\n")
        return 1

    repo_lookup = {repo["name"]: repo for repo in repos}
    total_stars = sum(int(repo["stargazers_count"]) for repo in repos if not repo.get("fork"))

    content = README_PATH.read_text(encoding="utf-8")
    content = replace_section(content, "profile-badges", render_profile_badges(user))
    content = replace_section(content, "stats-badges", render_stats_badges(user, total_stars))
    content = replace_section(content, "pinned-projects", render_featured_projects(repo_lookup))
    README_PATH.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
