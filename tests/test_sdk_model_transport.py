"""Exercise real SDK request builders against a synthetic in-memory HTTP server."""
import json
import unittest
from unittest.mock import patch

import anthropic
import httpx2
import openai

from provider.adapters import ModelError
from provider.adapters.base import MAX_DETAIL, error_detail
from provider.sdk_transport import create_sdk_client, send


class SDKTransportTests(unittest.TestCase):
    def test_protocol_paths_headers_and_bodies(self):
        for provider, sdk, path, payload in (
            ('deepseek', openai, '/v1/chat/completions', {'model':'test','messages':[],'thinking':{'type':'enabled'},'reasoning_effort':'high'}),
            ('openai_compatible', openai, '/v1/chat/completions', {'model':'test','messages':[],'max_tokens':12}),
            ('openai', openai, '/v1/chat/completions', {'model':'test','messages':[],'max_tokens':12}),
            ('openai_responses', openai, '/v1/responses', {'model':'test','input':[],'store':False}),
            ('anthropic', anthropic, '/v1/messages', {'model':'test','messages':[],'max_tokens':12,'temperature':0.5}),
        ):
            with self.subTest(provider=provider):
                calls=[]
                def respond(request):
                    calls.append(request)
                    return httpx2.Response(200, json={'ok':True})
                client=sdk.DefaultHttpxClient(transport=httpx2.MockTransport(respond))
                with patch.object(sdk, 'DefaultHttpxClient', return_value=client) as factory:
                    module, sdk_client = create_sdk_client(provider, 'https://example.test/v1', 'synthetic-key',
                                                          {'proxy_url':'http://127.0.0.1:10808'}, 60)
                    with sdk_client:
                        result = send(module, sdk_client, provider, payload, 4)
                self.assertEqual(result,{'ok':True})
                factory.assert_called_once_with(proxy='http://127.0.0.1:10808')
                self.assertEqual(calls[0].url.path,path)
                self.assertEqual(json.loads(calls[0].content),payload)
                if provider=='anthropic':
                    self.assertEqual(calls[0].headers['authorization'],'Bearer synthetic-key')
                    self.assertNotIn('x-api-key', calls[0].headers)
                    self.assertEqual(calls[0].headers['anthropic-version'],'2026-10-21')
                else:self.assertEqual(calls[0].headers['authorization'],'Bearer synthetic-key')

    def test_http_errors_are_safe_and_sdk_does_not_retry(self):
        for provider,sdk in [('openai_responses',openai),('anthropic',anthropic)]:
            calls=[]
            def respond(request):
                calls.append(request)
                return httpx2.Response(429,json={'error':'PRIVATE'})
            client=sdk.DefaultHttpxClient(transport=httpx2.MockTransport(respond))
            payload={'model':'test','messages':[],'max_tokens':12} if provider=='anthropic' else {'model':'test','input':[]}
            with patch.object(sdk,'DefaultHttpxClient',return_value=client):
                module, sdk_client = create_sdk_client(provider, 'https://example.test/v1', 'key', {'proxy_url':'http://127.0.0.1:1'}, 60)
                with sdk_client:
                    with self.assertRaises(ModelError) as caught:
                        send(module, sdk_client, provider, payload, 4)
            self.assertEqual(caught.exception.code,'http_429')
            self.assertTrue(caught.exception.retryable)
            self.assertNotIn('PRIVATE',str(caught.exception))
            self.assertEqual(len(calls),1)

    def test_errors_carry_the_provider_text_as_detail_but_never_in_the_message(self):
        body={'error':{'message':'max_tokens is too large','type':'invalid_request_error'}}
        payloads={'openai_responses':{'model':'test','input':[]},'deepseek':{'model':'test','messages':[]},
                  'anthropic':{'model':'test','messages':[],'max_tokens':12}}
        for provider,sdk in [('openai_responses',openai),('deepseek',openai),('anthropic',anthropic)]:
            with self.subTest(provider=provider):
                client=sdk.DefaultHttpxClient(transport=httpx2.MockTransport(lambda request:httpx2.Response(400,json=body)))
                with patch.object(sdk,'DefaultHttpxClient',return_value=client):
                    module, sdk_client = create_sdk_client(provider, 'https://example.test/v1', 'key', {'proxy_url':'http://127.0.0.1:1'}, 60)
                    with sdk_client:
                        with self.assertRaises(ModelError) as caught:
                            send(module, sdk_client, provider, payloads[provider], 4)
                error=caught.exception
                self.assertEqual((error.code,error.status,error.retryable),('http_400',400,False))
                self.assertIn('max_tokens is too large',error.detail)
                self.assertEqual(str(error),'http_400')

    def test_connection_failures_name_the_underlying_error(self):
        def refuse(request):
            raise httpx2.ConnectError('refused by the proxy')
        for provider,sdk,payload in [('openai',openai,{'model':'test','messages':[]}),
                                     ('anthropic',anthropic,{'model':'test','messages':[],'max_tokens':12})]:
            with self.subTest(provider=provider):
                client=sdk.DefaultHttpxClient(transport=httpx2.MockTransport(refuse))
                with patch.object(sdk,'DefaultHttpxClient',return_value=client):
                    module, sdk_client = create_sdk_client(provider, 'https://example.test/v1', 'key', {'proxy_url':'http://127.0.0.1:1'}, 60)
                    with sdk_client:
                        with self.assertRaises(ModelError) as caught:
                            send(module, sdk_client, provider, payload, 4)
                self.assertEqual((caught.exception.code,caught.exception.retryable),('connection_failed',True))
                self.assertIn('ConnectError',caught.exception.detail)
                self.assertIn('refused by the proxy',caught.exception.detail)
                self.assertEqual(str(caught.exception),'connection_failed')

    def test_gemini_errors_keep_status_and_message_as_detail(self):
        from google.genai import errors
        from provider.gemini_transport import map_gemini_error
        mapped=map_gemini_error(errors.APIError(429,{'error':{'message':'Quota exceeded','status':'RESOURCE_EXHAUSTED'}}))
        self.assertEqual((mapped.code,mapped.status,mapped.retryable,mapped.detail),
                         ('http_429',429,True,'RESOURCE_EXHAUSTED Quota exceeded'))
        self.assertEqual(str(mapped),'http_429')
        self.assertIn('ConnectError',map_gemini_error(httpx2.ConnectError('down')).detail)
        self.assertEqual(map_gemini_error(KeyError('x')).detail,"KeyError 'x'")

    def test_detail_is_one_short_line_and_defaults_to_empty(self):
        self.assertEqual(error_detail('a'+chr(10)+'  b','',None,'c'),'a b c')
        long=error_detail('x'*(MAX_DETAIL+50))
        self.assertEqual((len(long),long[-1]),(MAX_DETAIL+1,'…'))
        self.assertEqual(ModelError('x').detail,'')

    def test_anthropic_bearer_uses_explicit_key(self):
        calls=[]
        def respond(request):
            calls.append(request)
            return httpx2.Response(200,json={'ok':True})
        client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(respond))
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'unrelated'}), patch.object(anthropic,'DefaultHttpxClient',return_value=client):
            module, sdk_client = create_sdk_client('anthropic', 'https://example.test/v1', 'desired',
                                                   {'proxy_url':'http://127.0.0.1:1','anthropic_auth':'bearer'}, 60)
            with sdk_client:
                send(module, sdk_client, 'anthropic', {'model':'test','messages':[],'max_tokens':12}, 4)
        self.assertEqual(calls[0].headers['authorization'],'Bearer desired')
        self.assertNotIn('x-api-key',calls[0].headers)


if __name__ == '__main__': unittest.main()
