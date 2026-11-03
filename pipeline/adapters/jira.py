"""Jira Cloud REST API v3: read the ticket, comment on it, move it."""

import re

import httpx

from pipeline.models import Ticket

CRITERIA_HEADING = re.compile(r"^\s*acceptance criteria\s*:?\s*$", re.IGNORECASE)


def adf_to_text(node: dict | list | None) -> str:
    """Atlassian Document Format to plain text, one line per block."""
    if node is None:
        return ""
    if isinstance(node, list):
        return "".join(adf_to_text(child) for child in node)
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content"))
    if node.get("type") == "listItem":
        return f"- {inner.strip()}\n"
    if node.get("type") in {"paragraph", "heading", "codeBlock"}:
        return f"{inner}\n"
    return inner


INLINE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*)")


def inline(text: str) -> list[dict]:
    """`code` and **bold** spans as ADF marks; everything else plain text."""
    nodes = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`"):
            nodes.append({"type": "text", "text": part[1:-1], "marks": [{"type": "code"}]})
        elif part.startswith("**") and part.endswith("**"):
            nodes.append({"type": "text", "text": part[2:-2], "marks": [{"type": "strong"}]})
        else:
            nodes.append({"type": "text", "text": part})
    return nodes


def text_to_adf(text: str) -> dict:
    """Simple markdown to ADF: `#` headings, `-` bullet lists, paragraphs,
    with `code` and **bold** inside them."""
    content: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        bullet = re.match(r"^\s*[-*]\s+(.*)", line)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)  # the comment's own title stays smaller than the page's
            content.append({"type": "heading", "attrs": {"level": level}, "content": inline(heading.group(2))})
        elif bullet:
            item = {"type": "listItem", "content": [{"type": "paragraph", "content": inline(bullet.group(1))}]}
            if content and content[-1]["type"] == "bulletList":
                content[-1]["content"].append(item)
            else:
                content.append({"type": "bulletList", "content": [item]})
        else:
            content.append({"type": "paragraph", "content": inline(line)})
    return {"type": "doc", "version": 1, "content": content}


def acceptance_criteria(description: str) -> list[str]:
    """The bullets under an "Acceptance criteria" line, if there is one."""
    criteria, inside = [], False
    for line in description.splitlines():
        if CRITERIA_HEADING.match(line):
            inside = True
        elif inside and line.strip().startswith(("-", "*")):
            criteria.append(line.strip().lstrip("-* ").strip())
        elif inside and line.strip():
            break
    return criteria


class JiraTracker:
    def __init__(self, base_url: str, email: str, token: str):
        self.http = httpx.AsyncClient(base_url=f"{base_url}/rest/api/3", auth=(email, token), timeout=20)

    async def get_ticket(self, key: str) -> Ticket:
        issue = (await self.http.get(f"/issue/{key}", params={"fields": "summary,description"})).raise_for_status()
        comments = (await self.http.get(f"/issue/{key}/comment")).raise_for_status()
        fields = issue.json()["fields"]
        description = adf_to_text(fields.get("description")).strip()
        return Ticket(
            key=key,
            title=fields["summary"],
            description=description,
            acceptance_criteria=acceptance_criteria(description),
            comments=[adf_to_text(comment["body"]).strip() for comment in comments.json()["comments"]],
        )

    async def comment(self, key: str, body: str) -> None:
        (await self.http.post(f"/issue/{key}/comment", json={"body": text_to_adf(body)})).raise_for_status()

    async def upsert_progress(self, key: str, body: str) -> None:
        """One progress comment per ticket, edited in place: found by its
        heading, which is the body's first line."""
        marker = body.splitlines()[0].lstrip("# ").strip()
        comments = (await self.http.get(f"/issue/{key}/comment")).raise_for_status().json()["comments"]
        existing = next((c for c in comments if adf_to_text(c["body"]).strip().startswith(marker)), None)
        if existing:
            (
                await self.http.put(f"/issue/{key}/comment/{existing['id']}", json={"body": text_to_adf(body)})
            ).raise_for_status()
        else:
            await self.comment(key, body)

    async def move(self, key: str, status: str) -> None:
        """Idempotent: already in `status` is fine, so a retry doesn't fail."""
        issue = (await self.http.get(f"/issue/{key}", params={"fields": "status"})).raise_for_status()
        if issue.json()["fields"]["status"]["name"] == status:
            return
        transitions = (await self.http.get(f"/issue/{key}/transitions")).raise_for_status().json()["transitions"]
        match = next((t for t in transitions if t["to"]["name"] == status), None)
        if match is None:
            raise ValueError(f"{key}: no transition to {status!r}")
        (await self.http.post(f"/issue/{key}/transitions", json={"transition": {"id": match["id"]}})).raise_for_status()
