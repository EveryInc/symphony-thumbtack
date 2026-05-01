"""Issue tracker integration (Section 11). Linear adapter."""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import httpx

from .config import TrackerConfig
from .domain import BlockerRef, Issue
from .errors import (
    LinearApiRequest,
    LinearApiStatus,
    LinearGraphqlErrors,
    LinearMissingEndCursor,
    LinearUnknownPayload,
    UnsupportedTrackerKind,
)
from .logger import get_logger

_log = get_logger("tracker")

LINEAR_PAGE_SIZE = 50
LINEAR_NETWORK_TIMEOUT = 30.0


_CANDIDATE_QUERY = """
query SymphonyCandidates(
  $projectSlug: String!,
  $stateNames: [String!]!,
  $first: Int!,
  $after: String
) {
  issues(
    first: $first,
    after: $after,
    filter: {
      project: { slugId: { eq: $projectSlug } },
      state: { name: { in: $stateNames } }
    },
    orderBy: createdAt
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      inverseRelations {
        nodes {
          type
          issue {
            id
            identifier
            state { name }
          }
        }
      }
    }
  }
}
""".strip()


_BY_STATES_QUERY = """
query SymphonyByStates(
  $projectSlug: String!,
  $stateNames: [String!]!,
  $first: Int!,
  $after: String
) {
  issues(
    first: $first,
    after: $after,
    filter: {
      project: { slugId: { eq: $projectSlug } },
      state: { name: { in: $stateNames } }
    }
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      state { name }
    }
  }
}
""".strip()


_BY_IDS_QUERY = """
query SymphonyByIds($ids: [ID!]!) {
  issues(filter: { id: { in: $ids } }, first: 250) {
    nodes {
      id
      identifier
      title
      state { name }
    }
  }
}
""".strip()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        # Linear uses ISO-8601 with `Z` suffix.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _coerce_int_or_none(v: Any) -> Optional[int]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    return None


def _normalize_issue(node: Dict[str, Any]) -> Issue:
    state = ((node.get("state") or {}).get("name")) or ""
    labels = [
        n.get("name", "").lower()
        for n in ((node.get("labels") or {}).get("nodes") or [])
        if isinstance(n, dict) and isinstance(n.get("name"), str)
    ]

    blocked_by: List[BlockerRef] = []
    inverse = (node.get("inverseRelations") or {}).get("nodes") or []
    for rel in inverse:
        if not isinstance(rel, dict):
            continue
        if (rel.get("type") or "").lower() != "blocks":
            continue
        related = rel.get("issue") or rel.get("relatedIssue") or {}
        if not isinstance(related, dict):
            continue
        blocked_by.append(
            BlockerRef(
                id=related.get("id"),
                identifier=related.get("identifier"),
                state=((related.get("state") or {}).get("name")),
            )
        )

    return Issue(
        id=node.get("id") or "",
        identifier=node.get("identifier") or "",
        title=node.get("title") or "",
        state=state,
        description=node.get("description"),
        priority=_coerce_int_or_none(node.get("priority")),
        branch_name=node.get("branchName"),
        url=node.get("url"),
        labels=labels,
        blocked_by=blocked_by,
        created_at=_parse_iso(node.get("createdAt")),
        updated_at=_parse_iso(node.get("updatedAt")),
    )


class IssueTracker(abc.ABC):
    """Section 11.1 required operations."""

    @abc.abstractmethod
    async def fetch_candidate_issues(self) -> List[Issue]: ...

    @abc.abstractmethod
    async def fetch_issues_by_states(self, state_names: Sequence[str]) -> List[Issue]: ...

    @abc.abstractmethod
    async def fetch_issue_states_by_ids(self, issue_ids: Sequence[str]) -> List[Issue]: ...

    async def aclose(self) -> None:
        return None


class LinearTracker(IssueTracker):
    def __init__(self, *, config: TrackerConfig) -> None:
        if config.kind != "linear":
            raise UnsupportedTrackerKind(f"LinearTracker requires kind=linear, got {config.kind!r}")
        self._config = config
        self._client = httpx.AsyncClient(timeout=LINEAR_NETWORK_TIMEOUT)

    def update_config(self, config: TrackerConfig) -> None:
        self._config = config

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = self._config.endpoint
        api_key = self._config.api_key or ""
        headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
        payload = {"query": query, "variables": variables}
        try:
            resp = await self._client.post(endpoint, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise LinearApiRequest(f"Linear API transport error: {e}") from e

        if resp.status_code != 200:
            # 4xx bodies typically contain the GraphQL parse / validation
            # message; surface a truncated copy so operators can fix the
            # query. The request body (which carries the auth header) is not
            # echoed back, so this is safe.
            body_preview = resp.text[:600] if resp.text else ""
            raise LinearApiStatus(
                f"Linear API returned {resp.status_code}: {body_preview}",
                status=resp.status_code,
                body=body_preview,
            )

        try:
            body = resp.json()
        except Exception as e:
            raise LinearUnknownPayload(f"Linear API returned non-JSON: {e}") from e

        if not isinstance(body, dict):
            raise LinearUnknownPayload(f"Linear API returned non-object: {type(body).__name__}")

        if body.get("errors"):
            raise LinearGraphqlErrors(
                "Linear GraphQL errors",
                errors=body.get("errors"),
            )

        data = body.get("data")
        if not isinstance(data, dict):
            raise LinearUnknownPayload("Linear API response missing 'data' map")
        return data

    async def _paginate(
        self, query: str, variables: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        nodes: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            v = dict(variables)
            v["first"] = LINEAR_PAGE_SIZE
            v["after"] = cursor
            data = await self._post(query, v)
            issues = data.get("issues") or {}
            nodes.extend(issues.get("nodes") or [])
            page = issues.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                raise LinearMissingEndCursor(
                    "Linear pagination claims hasNextPage but endCursor is empty"
                )
        return nodes

    async def fetch_candidate_issues(self) -> List[Issue]:
        if not self._config.project_slug:
            raise LinearApiRequest("project_slug not configured")
        nodes = await self._paginate(
            _CANDIDATE_QUERY,
            {
                "projectSlug": self._config.project_slug,
                "stateNames": list(self._config.active_states),
            },
        )
        return [_normalize_issue(n) for n in nodes]

    async def fetch_issues_by_states(self, state_names: Sequence[str]) -> List[Issue]:
        if not state_names:
            return []
        if not self._config.project_slug:
            raise LinearApiRequest("project_slug not configured")
        nodes = await self._paginate(
            _BY_STATES_QUERY,
            {
                "projectSlug": self._config.project_slug,
                "stateNames": list(state_names),
            },
        )
        return [_normalize_issue(n) for n in nodes]

    async def fetch_issue_states_by_ids(self, issue_ids: Sequence[str]) -> List[Issue]:
        if not issue_ids:
            return []
        data = await self._post(_BY_IDS_QUERY, {"ids": list(issue_ids)})
        nodes = (data.get("issues") or {}).get("nodes") or []
        return [_normalize_issue(n) for n in nodes]


def build_tracker(config: TrackerConfig) -> IssueTracker:
    if config.kind == "linear":
        return LinearTracker(config=config)
    raise UnsupportedTrackerKind(f"unsupported tracker kind: {config.kind!r}")
