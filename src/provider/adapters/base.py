"""Small provider contract; no bot configuration, credentials, I/O or retries."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


MAX_DETAIL = 600


class ModelError(RuntimeError):
    """A failed model call. str(error) is the bare code and never contains provider text.

    ``detail`` holds a short excerpt of what the provider said, for diagnostics only. It is not masked here;
    whoever stores or shows it must (the bot's trace recorder does).
    """

    def __init__(self, code, *, retryable=False, status=None, detail=''):
        super().__init__(code)
        self.code, self.retryable, self.status, self.detail = code, retryable, status, detail


def error_detail(*parts, limit=MAX_DETAIL):
    """One short line out of provider or exception text."""
    text = ' '.join(' '.join(str(part).split()) for part in parts if part)
    return text if len(text) <= limit else text[:limit]+'…'


@dataclass(frozen=True)
class ModelRequest:
    # Keep the existing internal message/tool shape. Other wire protocols must
    # translate it inside their adapter, not in prompts or the tool executor.
    messages: list[dict] = field(repr=False)
    tools: list[dict] | None = field(default=None, repr=False)
    tool_choice: str | dict | None = field(default=None, repr=False)
    native_search: dict | None = field(default=None, repr=False)
    model: str | None = None
    options: dict = field(default_factory=dict, repr=False)
    # Best-effort hint: adapters whose official API has a JSON-object mode enable it;
    # others ignore it, so callers must still parse and validate the reply themselves.
    json_output: bool = False


@dataclass(frozen=True)
class ModelResult:
    message: dict = field(repr=False)
    finish_reason: str | None
    usage: dict
    search: dict = field(default_factory=dict, repr=False)
    native_finish_reason: str | None = field(default=None, repr=False)


class ProtocolAdapter(Protocol):
    native_search_supported: bool
    def build_payload(self, settings: dict, request: ModelRequest) -> dict: ...
    def parse_response(self, response: object) -> ModelResult: ...
    def assistant_message(self, message: dict) -> dict: ...
