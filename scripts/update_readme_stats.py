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
GRAPHQL_URL = f"{API_ROOT}/graphql"


@dataclass(frozen=True)
class FeaturedRepo:
    name: str
    description: str
    language: str


@dataclass(frozen=True)
class CommitStats:
    additions: int = 0
    deletions: int = 0
    commits: int = 0
    repositories: int = 0


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


def auth_token() -> str | None:
    return (
        os.getenv("PROFILE_STATS_TOKEN")
        or os.getenv("GH_TOKEN")
        or os.getenv("GITHUB_TOKEN")
    )


def request_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kanishkpaul-readme-stats",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_request(path: str, token: str | None = None) -> dict | list:
    request = Request(f"{API_ROOT}{path}", headers=request_headers(token or auth_token()))
    with urlopen(request) as response:
        return json.load(response)


def graphql_request(query: str, variables: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = request_headers(token)
    headers["Content-Type"] = "application/json"
    request = Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    with urlopen(request) as response:
        result = json.load(response)

    if not isinstance(result, dict) or result.get("errors"):
        raise RuntimeError("GitHub GraphQL query failed.")
    data = result.get("data")
    if not isinstance(data, dict):
        raise TypeError("Expected a data object from GitHub GraphQL.")
    return data


def fetch_user() -> dict:
    data = github_request(f"/users/{REPO_OWNER}")
    if not isinstance(data, dict):
        raise TypeError("Expected a user object from GitHub.")
    return data


def token_belongs_to_owner(token: str | None) -> bool:
    if not token:
        return False
    try:
        data = github_request("/user", token)
    except HTTPError:
        return False
    return isinstance(data, dict) and str(data.get("login", "")).lower() == REPO_OWNER


def fetch_owned_repos(token: str | None) -> list[dict]:
    owner_token = token_belongs_to_owner(token)
    page = 1
    repos: list[dict] = []
    while True:
        if owner_token:
            path = (
                "/user/repos?per_page=100&affiliation=owner&visibility=all"
                f"&sort=updated&page={page}"
            )
        else:
            path = (
                f"/users/{REPO_OWNER}/repos?per_page=100&type=owner"
                f"&sort=updated&page={page}"
            )
        data = github_request(path, token)
        if not isinstance(data, list):
            raise TypeError("Expected a list of repositories from GitHub.")
        if not data:
            break
        repos.extend(data)
        page += 1

    return [
        repo
        for repo in repos
        if not repo.get("fork")
        and str(repo.get("owner", {}).get("login", "")).lower() == REPO_OWNER
    ]


def fetch_author_id(token: str) -> str:
    query = """
    query($login: String!) {
      user(login: $login) { id }
    }
    """
    data = graphql_request(query, {"login": REPO_OWNER}, token)
    user = data.get("user")
    if not isinstance(user, dict) or not user.get("id"):
        raise RuntimeError("Could not resolve the GitHub user for commit attribution.")
    return str(user["id"])


def fetch_repo_commit_stats(repo: dict, author_id: str, token: str) -> CommitStats:
    query = """
    query(
      $owner: String!
      $name: String!
      $cursor: String
      $author: CommitAuthor!
    ) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor, author: $author) {
                nodes {
                  additions
                  deletions
                  parents(first: 1) { totalCount }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
      }
    }
    """
    cursor: str | None = None
    additions = 0
    deletions = 0
    commits = 0

    while True:
        data = graphql_request(
            query,
            {
                "owner": REPO_OWNER,
                "name": str(repo["name"]),
                "cursor": cursor,
                "author": {"id": author_id},
            },
            token,
        )
        repository = data.get("repository")
        if not isinstance(repository, dict):
            raise RuntimeError("A repository could not be read while counting commits.")
        default_ref = repository.get("defaultBranchRef")
        if not isinstance(default_ref, dict):
            return CommitStats()
        target = default_ref.get("target")
        if not isinstance(target, dict):
            return CommitStats()
        history = target.get("history")
        if not isinstance(history, dict):
            return CommitStats()

        nodes = history.get("nodes") or []
        for commit in nodes:
            if not isinstance(commit, dict):
                continue
            parents = commit.get("parents") or {}
            if int(parents.get("totalCount", 0)) > 1:
                continue
            additions += int(commit.get("additions", 0))
            deletions += int(commit.get("deletions", 0))
            commits += 1

        page_info = history.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError("GitHub returned an invalid commit-history cursor.")

    return CommitStats(additions=additions, deletions=deletions, commits=commits)


def fetch_commit_stats(repos: list[dict], token: str | None) -> CommitStats:
    if not token:
        return CommitStats()

    author_id = fetch_author_id(token)
    additions = 0
    deletions = 0
    commits = 0
    repositories = 0

    for repo in repos:
        if not repo.get("default_branch"):
            continue
        stats = fetch_repo_commit_stats(repo, author_id, token)
        additions += stats.additions
        deletions += stats.deletions
        commits += stats.commits
        if stats.commits:
            repositories += 1

    return CommitStats(
        additions=additions,
        deletions=deletions,
        commits=commits,
        repositories=repositories,
    )


def build_badge(label: str, value: int | str, color: str, alt_text: str) -> str:
    encoded_label = quote(label, safe="")
    encoded_value = quote(str(value), safe="")
    return (
        f'<img src="https://img.shields.io/badge/{encoded_label}-{encoded_value}-{color}'
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


def render_stats_badges(
    user: dict,
    total_stars: int,
    commit_stats: CommitStats,
    includes_private: bool,
) -> str:
    followers = int(user["followers"])
    following = int(user["following"])
    public_repos = int(user["public_repos"])
    additions = f"{commit_stats.additions:,}"
    deletions = f"{commit_stats.deletions:,}"
    commits = f"{commit_stats.commits:,}"
    scope = "" if includes_private else "Public "
    return "\n".join(
        [
            f'  {build_badge(f"{scope}Lines Committed", additions, "16a34a", f"{additions} cumulative lines added")}',
            f'  {build_badge(f"{scope}Lines Removed", deletions, "991b1b", f"{deletions} cumulative lines removed")}',
            f'  {build_badge(f"{scope}Commits Counted", commits, "1d4ed8", f"{commits} authored non-merge commits counted")}',
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
    token = auth_token()
    includes_private = token_belongs_to_owner(token)
    try:
        user = fetch_user()
        repos = fetch_owned_repos(token)
        commit_stats = fetch_commit_stats(repos, token)
    except HTTPError as error:
        sys.stderr.write(f"GitHub API request failed: {error.code} {error.reason}\n")
        return 1
    except (KeyError, TypeError, RuntimeError, ValueError) as error:
        sys.stderr.write(f"README stats refresh failed: {error}\n")
        return 1

    repo_lookup = {repo["name"]: repo for repo in repos}
    total_stars = sum(int(repo["stargazers_count"]) for repo in repos)

    content = README_PATH.read_text(encoding="utf-8")
    content = replace_section(content, "profile-badges", render_profile_badges(user))
    content = replace_section(
        content,
        "stats-badges",
        render_stats_badges(user, total_stars, commit_stats, includes_private),
    )
    content = replace_section(content, "pinned-projects", render_featured_projects(repo_lookup))
    README_PATH.write_text(content, encoding="utf-8")

    visibility = "public and private" if includes_private else "public"
    print(
        f"Counted {commit_stats.commits:,} commits and "
        f"{commit_stats.additions:,} added lines across "
        f"{commit_stats.repositories} {visibility} repositories."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
