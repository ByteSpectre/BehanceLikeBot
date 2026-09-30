from __future__ import annotations

import re

LIKE_TASK = "like"
COMMENT_TASK = "comment"

_COMMENT_TASK_RE = re.compile(
    r"напишите\s+уникальн\w*\s+комментар|"
    r"уникальн\w*\s+комментар|"
    r"комментар\w*\s+к\s+этому\s+проекту|"
    r"напишите\s+комментар|"
    r"leave\s+a\s+comment|"
    r"write\s+a\s+(?:unique\s+)?comment|"
    r"unique\s+comment",
    re.IGNORECASE,
)


def classify_task(text: str) -> str:
    """Return whether a bot message asks for a comment or a like."""
    if _COMMENT_TASK_RE.search(text or ""):
        return COMMENT_TASK
    return LIKE_TASK
