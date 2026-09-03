from pydantic import BaseModel, ConfigDict


class GitHubModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class User(GitHubModel):
    login: str


class Repository(GitHubModel):
    name: str
    owner: User


class Ref(GitHubModel):
    sha: str


class PullRequest(GitHubModel):
    number: int
    draft: bool = False
    base: Ref
    head: Ref


class Installation(GitHubModel):
    id: int


class PullRequestEvent(GitHubModel):
    action: str
    pull_request: PullRequest
    repository: Repository
    installation: Installation
    sender: User


class Issue(GitHubModel):
    number: int
    pull_request: dict[str, object] | None = None


class Comment(GitHubModel):
    id: int
    body: str
    user: User


class IssueCommentEvent(GitHubModel):
    action: str
    issue: Issue
    comment: Comment
    repository: Repository
    installation: Installation
    sender: User


def parse_review_command(body: str) -> str | None:
    """Return the V1 command when the first actionable Markdown line is exactly /review."""
    in_fence = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith(">"):
            continue
        if raw_line.startswith(("    ", "\t")) or (
            stripped.startswith("`") and stripped.endswith("`")
        ):
            continue
        return "review" if stripped.casefold() == "/review" else None
    return None


def is_review_command(body: str, bot_login: str = "") -> bool:
    """Compatibility wrapper; bot login is intentionally irrelevant to slash commands."""
    return parse_review_command(body) == "review"
