"""Host integration tests. Run only via run_tests.py (isolated HOME/HERMES_HOME)."""
import copy
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class HostTests(unittest.TestCase):
    def test_discovery_transport_picker_and_terminal_classification(self):
        self.assertTrue((ROOT / 'plugin.yaml').exists(), 'provider package missing')
        from providers import get_provider_profile
        from agent.transports import get_transport
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_cli.models import list_available_providers, provider_model_ids
        from agent.error_classifier import classify_api_error
        import prompt_models as pm
        store = pm.Store()
        doc = store.read()
        store.write([dict(id='review', name='Review', provider='openai-codex', model='gpt-test', prefix='Prefix', enabled=True)], doc['revision'])
        profile = get_provider_profile('prompt-models')
        self.assertIsNotNone(profile)
        self.assertFalse(profile.supports_streaming, 'Host chat reassembly drops native Codex replay; keep buffered completions')
        self.assertIn('prompt-models', [p['id'] for p in list_available_providers()])
        self.assertIn('review', provider_model_ids('prompt-models'))
        runtime = resolve_runtime_provider(requested='prompt-models', target_model='review')
        self.assertEqual(runtime['provider'], 'prompt-models')
        self.assertEqual(runtime['api_mode'], 'prompt_models')
        transport = get_transport('prompt_models')
        messages = [{'role':'system','content':'Hermes'}, {'role':'assistant','content':'hello',
                     'reasoning_content':'think','codex_message_items':[{'type':'reasoning','encrypted_content':'opaque'}]}]
        old = copy.deepcopy(messages)
        request = transport.build_kwargs('review', messages, tools=[{'type':'function','function':{'name':'tool'}}],
                                         reasoning_config={'effort':'high'}, temperature=0.4)
        self.assertEqual(request['messages'], old)
        self.assertIsNot(request['messages'], messages)
        self.assertEqual(request['_prompt_models_reasoning'], {'effort':'high'})
        forwarded = transport.build_kwargs('review', messages, timeout=37,
            request_overrides={'temperature':0.2, 'extra_body':{'service_tier':'priority'}})
        self.assertEqual(forwarded.get('timeout'), 37)
        self.assertEqual(forwarded.get('temperature'), 0.2)
        self.assertEqual(forwarded.get('extra_body'), {'service_tier':'priority'})
        for error in [ConnectionError('outage'), ValueError('bad'), RuntimeError('429'), pm.PinnedRouteError('failed')]:
            verdict = classify_api_error(error, provider='prompt-models', model='review')
            self.assertFalse(verdict.retryable)
            self.assertFalse(verdict.should_fallback)
            self.assertFalse(verdict.should_rotate_credential)

    def test_desktop_inventory_and_codex_wire_with_tool_history(self):
        from types import SimpleNamespace as NS
        from providers import get_provider_profile
        from hermes_cli.inventory import build_models_payload, load_picker_context
        from agent.auxiliary_client import CodexAuxiliaryClient
        import prompt_models as pm
        store = pm.Store()
        doc = store.read()
        store.write([dict(id='codex-wire', name='Codex wire', provider='openai-codex', model='gpt-5.4', prefix='Custom', enabled=True)], doc['revision'])
        # The exact inventory substrate behind Desktop, not merely list_providers().
        payload = build_models_payload(load_picker_context(), explicit_only=True, probe_custom_providers=False)
        virtual = next(p for p in payload['providers'] if p['slug'] == 'prompt-models')
        self.assertIn('codex-wire', virtual['models'])
        real = NS(api_key='test-fixture-only', base_url='https://chatgpt.com/backend-api/codex')
        adapter = CodexAuxiliaryClient(real, 'gpt-5.4')
        wire = []
        def send(**request):
            body, _, _ = adapter.chat.completions._build_responses_kwargs(request)
            wire.append(body)
            return NS(model='gpt-5.4', choices=[NS(finish_reason='stop', message=NS(content='OK', tool_calls=None, reasoning_content='summary'))])
        history = [{'role':'system','content':'Hermes'}, {'role':'system','content':'Extra'},
                   {'role':'assistant','content':None,'tool_calls':[{'id':'call_a','type':'function','function':{'name':'lookup','arguments':'{}'}}]},
                   {'role':'tool','tool_call_id':'call_a','content':'result'}]
        original = copy.deepcopy(history)
        with patch('agent.auxiliary_client.resolve_provider_client', return_value=(adapter, 'gpt-5.4')) as resolver, \
             patch.object(adapter.chat.completions, 'create', side_effect=send):
            result = pm.Client().create(model='codex-wire', messages=history,
                                       tools=[{'type':'function','function':{'name':'lookup','parameters':{'type':'object'}}}],
                                       _prompt_models_reasoning={'effort':'high'}, stream=True)
            self.assertEqual(list(result)[0].choices[0].delta.content, 'OK')
        resolver.assert_called_once_with('openai-codex', model='gpt-5.4')
        self.assertEqual(wire[0]['instructions'], 'Custom\n\nHermes\n\nExtra')
        self.assertEqual(wire[0]['reasoning']['effort'], 'high')
        self.assertIn('function_call', [i['type'] for i in wire[0]['input']])
        self.assertIn('function_call_output', [i['type'] for i in wire[0]['input']])
        self.assertEqual(history, original)
        # Host create_client seam returns our facade rather than an HTTP client.
        from agent.agent_runtime_helpers import create_openai_client
        owner = NS(provider='prompt-models', _client_log_context=lambda:'', _build_keepalive_http_client=lambda *a,**k:None)
        facade = create_openai_client(owner, {'api_key':'local', 'base_url':'https://prompt-models.invalid/v1'}, reason='test', shared=False)
        self.assertEqual(type(facade).__name__, 'Client')

    def test_native_codex_stream_two_turn_recording_and_replay(self):
        self.test_native_codex_two_turn_recording_and_replay(streaming=True)

    def test_native_codex_two_turn_recording_and_replay(self, streaming=False):
        """Native wire -> facade -> host normalization/storage -> next native wire."""
        import json
        from types import SimpleNamespace as NS
        from run_agent import AIAgent
        from agent.auxiliary_client import CodexAuxiliaryClient
        from agent.turn_truncation import normalize_response_for_agent
        from agent.chat_completion_helpers import build_assistant_message
        import prompt_models as pm
        store = pm.Store()
        doc = store.read()
        store.write([dict(id='native-loop', name='Native', provider='openai-codex',
                          model='gpt-5.4', prefix='Prefix', enabled=True)], doc['revision'])
        reasoning = {'type':'reasoning', 'id':'rs_native', 'summary':[], 'encrypted_content':'opaque-fixture'}
        message = {'type':'message', 'id':'msg_native', 'role':'assistant', 'status':'completed',
                   'phase':'commentary', 'content':[{'type':'output_text','text':'Checking'}]}
        function = {'type':'function_call', 'id':'fc_native_exact', 'call_id':'call_native',
                    'name':'lookup', 'arguments':'{}', 'status':'completed'}
        native = NS(status='completed', output=[NS(**v) for v in [reasoning, message, function]], usage=None)
        wires = []
        def send(**request):
            wires.append({**request, **request.get('extra_body', {})})
            if streaming:
                def events():
                    for item in native.output:
                        yield NS(type='response.output_item.done', item=item)
                    yield NS(type='response.completed', response=native)
                return events()
            return native
        real = NS(api_key='fixture', base_url='https://chatgpt.com/backend-api/codex',
                  responses=NS(create=send))
        adapter = CodexAuxiliaryClient(real, 'gpt-5.4')
        agent = AIAgent(model='native-loop', provider='prompt-models', api_mode='prompt_models',
                        base_url='https://prompt-models.invalid/v1', api_key='local',
                        enabled_toolsets=[], skip_memory=True, skip_context_files=True,
                        skip_background_review=True, quiet_mode=True, max_iterations=1)
        history = [{'role':'system','content':'Hermes'}, {'role':'user','content':'Check'}]
        before = copy.deepcopy(history)
        try:
            with patch('agent.auxiliary_client.resolve_provider_client', return_value=(adapter,'gpt-5.4')):
                response = agent.client.chat.completions.create(**agent._build_api_kwargs(history, tools_for_api=[]))
                normalized = normalize_response_for_agent(agent, response)
                persisted = json.loads(json.dumps(build_assistant_message(agent, normalized, normalized.finish_reason)))
                self.assertEqual(persisted.get('codex_reasoning_items'),
                                 [{**reasoning, '_issuer_kind':'codex_backend', '_issuer_model':'gpt-5.4'}])
                self.assertEqual(persisted.get('codex_message_items'), [message])
                self.assertEqual(normalized.tool_calls[0].provider_data['call_id'], 'call_native')
                self.assertEqual(persisted['tool_calls'][0]['response_item_id'], 'fc_native_exact')
                history.extend([persisted, {'role':'tool','tool_call_id':'call_native','content':'found'}])
                snapshot = copy.deepcopy(history)
                agent.client.chat.completions.create(**agent._build_api_kwargs(history, tools_for_api=[]))
            self.assertEqual(history, snapshot)
            self.assertEqual(history[:2], before)
            self.assertEqual(wires[1]['instructions'], 'Prefix\n\nHermes')
            replay = wires[1]['input']
            # The destination's store=False converter deliberately removes item
            # IDs on the wire; durable history above must retain their exact IDs.
            self.assertIn({k:v for k,v in reasoning.items() if k != 'id'}, replay)
            self.assertIn({k:v for k,v in message.items() if k != 'id'}, replay)
            call = next(v for v in replay if v['type'] == 'function_call')
            self.assertEqual(call['name'], 'lookup')
            self.assertEqual(call['call_id'], 'call_native')
            self.assertTrue(any(v['type']=='function_call_output' and v['call_id']=='call_native' for v in replay))
        finally:
            agent.close()

    def test_real_openai_wire_uses_destination_sanitization(self):
        import json
        import httpx
        from openai import OpenAI
        import prompt_models as pm
        store = pm.Store()
        doc = store.read()
        store.write([dict(id='openai-wire', name='OpenAI', provider='openai', model='gpt-4.1', prefix='Prefix', enabled=True)], doc['revision'])
        bodies = []
        def handle(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={'id':'fixture','object':'chat.completion','created':0,'model':'gpt-4.1',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'OK'}}]})
        real = OpenAI(api_key='test-fixture', base_url='https://api.openai.com/v1', http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        messages = [{'role':'system','content':'Hermes'}, {'role':'assistant','content':'old','codex_message_items':[{'type':'reasoning'}]}, {'role':'user','content':'Hello'}]
        with patch('agent.auxiliary_client.resolve_provider_client', return_value=(real, 'gpt-4.1')):
            self.assertEqual(pm.Client().create(model='openai-wire', messages=messages).choices[0].message.content, 'OK')
        self.assertNotIn('codex_message_items', bodies[0]['messages'][1])
        self.assertIn('codex_message_items', messages[1])
        self.assertEqual(bodies[0]['messages'][0]['content'], 'Prefix\n\nHermes')
        self.assertEqual(bodies[0]['model'], 'gpt-4.1')
        real.close()

    def test_real_agent_outbound_boundary_without_network(self):
        from types import SimpleNamespace as NS
        from run_agent import AIAgent
        from hermes_cli.runtime_provider import resolve_runtime_provider
        import prompt_models as pm
        store = pm.Store()
        doc = store.read()
        store.write([dict(id='agent-check',name='Agent',provider='openai',model='gpt-4.1',prefix='Custom',enabled=True)],doc['revision'])
        runtime = resolve_runtime_provider(requested='prompt-models',target_model='agent-check')
        agent = AIAgent(model='agent-check', provider='prompt-models', api_mode=runtime['api_mode'],
                        base_url=runtime['base_url'], api_key=runtime['api_key'],
                        enabled_toolsets=[], skip_memory=True, skip_context_files=True,
                        skip_background_review=True, quiet_mode=True, max_iterations=1,
                        reasoning_config={'effort':'high'})
        self.assertEqual(agent.api_mode, 'prompt_models')
        original = [{'role':'system','content':'Hermes instructions'}, {'role':'user','content':'Hello'}]
        request = agent._build_api_kwargs(original, tools_for_api=[])
        self.assertEqual(request['messages'], original)
        seen = []
        def create(**kw):
            seen.append(kw)
            return NS(model='gpt-4.1',choices=[NS(finish_reason='stop',message=NS(content='fixture',tool_calls=None))])
        upstream = NS(base_url='https://api.openai.com/v1',chat=NS(completions=NS(create=create)))
        with patch('agent.auxiliary_client.resolve_provider_client',return_value=(upstream,'gpt-4.1')):
            answer = agent.client.chat.completions.create(**request)
        self.assertEqual(answer.choices[0].message.content, 'fixture')
        self.assertEqual(seen[0]['messages'][0]['content'], 'Custom\n\nHermes instructions')
        self.assertEqual(original[0]['content'], 'Hermes instructions')
        agent._fallback_chain = [{'provider':'openai', 'model':'must-not-run'}]
        with patch('agent.auxiliary_client.resolve_provider_client', side_effect=ConnectionError('fixture failure')), \
             patch.object(agent, '_try_activate_fallback') as fallback:
            failed = agent.run_conversation('Hello')
        fallback.assert_not_called()
        self.assertTrue(failed.get('error') or not failed.get('completed', True))
        agent.close()

if __name__ == '__main__':
    unittest.main()
