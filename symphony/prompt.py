"""Prompt rendering with strict variable/filter checking (Section 5.4 / 12)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from jinja2 import (
    Environment,
    StrictUndefined,
    TemplateAssertionError,
    TemplateSyntaxError,
    UndefinedError,
)

from .config import DEFAULT_FALLBACK_PROMPT
from .domain import Issue
from .errors import TemplateParseError, TemplateRenderError


_ENV = Environment(
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def render_prompt(
    template_text: str,
    issue: Issue,
    *,
    attempt: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Render the per-issue prompt.

    Per Section 5.4: empty body falls back to a minimal default prompt; parse
    or render errors raise typed exceptions.
    """
    if not template_text or not template_text.strip():
        return DEFAULT_FALLBACK_PROMPT

    try:
        template = _ENV.from_string(template_text)
    except TemplateAssertionError as e:
        # Compile-time semantic errors (e.g. unknown filters/tests). Per
        # Section 5.5 these surface as `template_render_error`.
        raise TemplateRenderError(f"prompt template render error: {e}") from e
    except TemplateSyntaxError as e:
        raise TemplateParseError(f"prompt template parse error: {e}") from e

    context: Dict[str, Any] = {
        "issue": issue.for_template(),
        "attempt": attempt,
    }
    if extra:
        context.update(extra)

    try:
        return template.render(**context)
    except UndefinedError as e:
        raise TemplateRenderError(f"undefined variable in prompt: {e}") from e
    except Exception as e:
        raise TemplateRenderError(f"prompt render error: {e}") from e
