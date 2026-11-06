"""Application configuration and queue-facing submission API records."""
from adapter_wc.native import (
    CHAT_ID, MAX_FRAME, REQUEST_ID, NativeError, decode, encode, receive, request,
    text_parts, validate_send,
)


from dataclasses import dataclass
from wxbridge.config.models import SubmissionAPIConfig
from wxbridge.native.settings import settings as helper_settings


@dataclass(frozen=True)
class SubmissionSettings:
    enabled: bool
    socket: str
    max_pending: int
    max_requests: int
    allowed_chat_ids: tuple[str, ...]
    max_text_bytes: int


def settings(cfg):
    data = cfg.account.submission_api
    helper = helper_settings(cfg)
    if not isinstance(data, SubmissionAPIConfig) or data.extensions:
        raise NativeError('invalid_submission_config')
    if not data.socket.startswith('/') or len(data.socket.encode()) > 100:
        raise NativeError('invalid_native_socket')
    if data.enabled and data.socket == helper.socket:raise NativeError('invalid_native_socket')
    for key, minimum, maximum in [('max_pending',1,32),('max_requests',1,4096)]:
        if not minimum <= getattr(data,key) <= maximum:raise NativeError('invalid_native_limit')
    return SubmissionSettings(data.enabled,data.socket,data.max_pending,data.max_requests,
                              helper.allowed_chat_ids,helper.max_text_bytes)
