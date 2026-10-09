"""Typed outputs of the agents (ADR-006, ADR-012)."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class PageVerdict(BaseModel):
    """The verdict of the fact-checker for one page that it read."""

    url: str = Field(description="The URL that you gave to fetch_page.")
    verdict: Literal["SUPPORTED", "CONTRADICTED", "OUTDATED", "NOT_FOUND", "UNCLEAR"]
    quote: str = Field(default="", description="A short quote from the page.")
    corrected_value: str | None = Field(default=None,
                                        description="The correct or newer value, for CONTRADICTED or OUTDATED.")


class ClaimCheck(BaseModel):
    """The result of one fact check. Code applies rule F2 to it."""

    claim: str = Field(description="The claim that you checked.")
    bookmark_ref: str | None = Field(default=None, description="The bookmark label of the claim, for example B2.")
    pages: list[PageVerdict] = Field(default_factory=list, description="One entry for each page that you read.")


_REF = re.compile(r"\[?(B\d+)\]?")


def bookmark_ref(*texts: str | None) -> str | None:
    for text in texts:
        if text and (m := _REF.search(text)):
            return m.group(1)
    return None


def parse_claim_check(content: str) -> ClaimCheck | None:
    """Parse the JSON that the `task` tool returns. Return None if it is not a ClaimCheck."""
    text = content.strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        return ClaimCheck.model_validate(json.loads(text))
    except (ValueError, ValidationError):
        return None
