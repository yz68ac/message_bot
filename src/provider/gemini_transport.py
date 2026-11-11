"""Caller-owned Google Gen AI SDK transport; no retries or automatic tool execution."""
from copy import deepcopy

from .adapters.base import ModelError, error_detail


def create_gemini_client(base_url, api_key, *, proxy_url=None):
    try:
        from google import genai
    except ImportError:
        raise ModelError('sdk_not_installed') from None
    options = dict(base_url=base_url.rstrip('/'), api_version='v1beta',
                   retry_options=dict(attempts=1), client_args={'trust_env': False})
    if proxy_url:options['client_args']['proxy'] = proxy_url
    return genai, genai.Client(vertexai=False, api_key=api_key, http_options=options)


def map_gemini_error(exc):
    from google.genai import errors
    import httpx
    if isinstance(exc, errors.APIError):
        status = exc.code
        if type(status) is int:
            return ModelError('http_'+str(status), retryable=status in (408,429) or status >= 500, status=status,
                              detail=error_detail(getattr(exc, 'status', ''), getattr(exc, 'message', '')))
        return ModelError('invalid_response', detail=error_detail(type(exc).__name__, getattr(exc, 'message', '')))
    if isinstance(exc, (httpx.TransportError, TimeoutError, OSError)):
        return ModelError('connection_failed', retryable=True, detail=error_detail(type(exc).__name__, exc))
    return ModelError('invalid_response', detail=error_detail(type(exc).__name__, exc))


def send_gemini_content(client, payload, timeout):
    request = deepcopy(payload)
    request.setdefault('config', {})['http_options'] = dict(timeout=max(1, int(timeout*1000)))
    request['config']['automatic_function_calling'] = dict(disable=True)
    try:
        response = client.models.generate_content(**request)
        return response.model_dump(mode='json', by_alias=True, exclude_none=True)
    except Exception as exc:
        raise map_gemini_error(exc) from None
