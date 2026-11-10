"""Request-local views over conversation-owned provider clients."""
from contextvars import ContextVar
from provider import ModelError, ModelRequest, capture_metrics
from wxbridge.ai.roles import model_settings
from wxbridge.app.status.trace import current as current_trace
from wxbridge.config import json_value
_scope = ContextVar('conversation_model_scope')

def client_for(role):
    lane, cfg, job, metrics = _scope.get()
    return RoleRequest(lane, cfg, role, job, metrics)

class RoleRequest:

    def __init__(self, lane, cfg, role, job, metrics):
        self.lane, self.role, self.job, self.metrics = (lane, role, job, metrics)
        self.cfg = model_settings(cfg, role, provider_config=lane.config)

    @property
    def adapter(self):
        return self.lane.get_client().adapter

    def request(self, messages, *, tools=None, tool_choice=None, native_search=None, deadline=None, require_complete=False, json_output=False):
        self.job.check()
        before = len(self.metrics)
        options = json_value(self.cfg.options)
        request = ModelRequest(messages, tools, tool_choice, native_search, model=self.cfg.model, options=options, json_output=json_output)
        trace = current_trace()
        call = trace.begin(request, role=self.role, channel=self.lane.name) if trace else None
        try:
            result = self.lane.get_client().generate(request, deadline=min(deadline, self.job.deadline) if deadline else self.job.deadline, require_complete=require_complete)
            if call is not None:
                trace.response(call, result)
            self.job.check()
            return result
        except BaseException as exc:
            if call is not None:
                trace.failure(call, exc)
            raise
        finally:
            for entry in self.metrics[before:]:
                entry.update(role=self.role, channel=self.lane.name)
            if call is not None:
                # The same dict objects: check_complete() may still rewrite their error after we return.
                call['_metrics'] = self.metrics[before:]

    def complete(self, messages, *, require_complete=False, **kwargs):
        return self.request(messages, require_complete=require_complete, **kwargs).message

    def chat(self, messages):
        return (self.complete(messages, require_complete=True).get('content') or '').strip()

    def assistant_message(self, message):
        return self.lane.get_client().assistant_message(message)

    @staticmethod
    def check_complete(result):
        from provider import Client
        Client.check_complete(result)
