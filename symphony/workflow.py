"""WORKFLOW.md loader (Section 5)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Tuple

import yaml

from .errors import (
    MissingWorkflowFile,
    WorkflowFrontMatterNotAMap,
    WorkflowParseError,
)


_FRONT_MATTER_DELIM = "---"


@dataclass
class WorkflowDefinition:
    """Section 4.1.2: parsed payload from WORKFLOW.md."""

    config: Dict[str, Any]
    prompt_template: str
    source_path: str  # absolute, normalized path of the loaded file


def _split_front_matter(text: str) -> Tuple[str, str]:
    """Return `(front_matter_text, body_text)`. Either may be empty.

    A YAML front matter block is recognized only when the file begins with
    `---` on its own line. Everything up to the next `---` line is treated as
    YAML; the rest is the body. If front matter is absent, the whole file is
    body and YAML is empty.
    """
    # The spec says "If file starts with ---". We accept either CRLF or LF line
    # endings, but the leading delimiter must be the first thing in the file
    # (allowing for an optional UTF-8 BOM, which is invisible to most users).
    stripped_text = text.lstrip("﻿")
    lines = stripped_text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIM:
        return "", stripped_text

    # Find the closing delimiter.
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONT_MATTER_DELIM:
            front = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            return front, body
    # No closing delimiter -> spec is silent; treat as parse error rather than
    # silently consuming everything as YAML.
    raise WorkflowParseError("front matter block has no closing '---' delimiter")


def load_workflow(path: str) -> WorkflowDefinition:
    """Read and parse WORKFLOW.md.

    Raises:
        MissingWorkflowFile: file cannot be read.
        WorkflowParseError: YAML cannot be parsed.
        WorkflowFrontMatterNotAMap: top-level YAML is not a mapping.
    """
    abs_path = os.path.abspath(os.path.expanduser(path))
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError as e:
        raise MissingWorkflowFile(f"workflow file not found: {abs_path}", path=abs_path) from e
    except OSError as e:
        raise MissingWorkflowFile(
            f"cannot read workflow file: {abs_path}: {e}", path=abs_path
        ) from e

    front, body = _split_front_matter(text)

    if front.strip():
        try:
            config = yaml.safe_load(front)
        except yaml.YAMLError as e:
            raise WorkflowParseError(
                f"invalid YAML front matter in {abs_path}: {e}"
            ) from e
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise WorkflowFrontMatterNotAMap(
                f"workflow front matter must decode to a map, got {type(config).__name__}"
            )
    else:
        config = {}

    return WorkflowDefinition(
        config=config,
        prompt_template=body.strip(),
        source_path=abs_path,
    )
