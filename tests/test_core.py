import copy
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class PrefixTests(unittest.TestCase):
    def test_prefix_only_outbound_system_deep_copy(self):
        self.assertIsNotNone(importlib.util.find_spec('prompt_models'), 'prompt_models implementation missing')
        from prompt_models import prepend
        original = [{'role': 'system', 'content': 'Hermes instructions'},
                    {'role': 'assistant', 'content': None, 'reasoning_content': 'trace',
                     'tool_calls': [{'id': 'a', 'function': {'name': 'x', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 'a', 'content': 'result'}]
        saved = copy.deepcopy(original)
        outbound = prepend(original, 'Custom prefix')
        self.assertEqual(outbound[0]['content'], 'Custom prefix\n\nHermes instructions')
        self.assertEqual(outbound[1:], original[1:])
        outbound[1]['tool_calls'][0]['function']['name'] = 'changed'
        self.assertEqual(original, saved)
        self.assertEqual(prepend(original, 'Custom prefix')[0]['content'], 'Custom prefix\n\nHermes instructions')

    def test_stream_keeps_tools_and_reasoning(self):
        from types import SimpleNamespace as NS
        from prompt_models import completion_stream
        reply = NS(model='gpt-test', choices=[NS(finish_reason='tool_calls', message=NS(
            role='assistant', content=None, reasoning_content='reason',
            reasoning_details=[{'type':'reasoning.summary','text':'summary'}],
            codex_message_items=[{'type':'reasoning','encrypted_content':'opaque'}],
            tool_calls=[NS(id='call-1', type='function', function=NS(name='test', arguments='{}'))]))],
            usage=NS(prompt_tokens=5, completion_tokens=2))
        chunks = list(completion_stream(reply))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].choices[0].delta.reasoning_content, 'reason')
        self.assertEqual(chunks[0].choices[0].delta.tool_calls[0].function.name, 'test')
        self.assertEqual(chunks[0].usage.prompt_tokens, 5)
        self.assertEqual(chunks[0].choices[0].delta.codex_message_items, reply.choices[0].message.codex_message_items)

    def test_system_blocks_empty_and_multiple_codex_system_messages(self):
        import prompt_models as pm
        self.assertTrue(hasattr(pm, 'codex_system_messages'), 'Codex multi-system preservation missing')
        source = [{'role':'system','content':[{'type':'text','text':'Hermes','cache_control':{'type':'ephemeral'}}]},
                  {'role':'system','content':'Extra instructions'}, {'role':'user','content':'Hello'}]
        saved = copy.deepcopy(source)
        self.assertEqual(pm.prepend(source, ''), source)
        result = pm.codex_system_messages(pm.prepend(source, 'Prefix'))
        self.assertEqual(result[0]['content'], 'Prefix\n\nHermes\n\nExtra instructions')
        self.assertEqual(source, saved)
        self.assertEqual(result[1:], [source[-1]])

class StoreTests(unittest.TestCase):
    def test_profile_store_crud_revision_and_validation(self):
        import tempfile
        import prompt_models as pm
        self.assertTrue(hasattr(pm, 'Store'), 'persistent Store missing')
        (ROOT / '.test-tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-tmp') as one, tempfile.TemporaryDirectory(dir=ROOT / '.test-tmp') as two:
            store, other = pm.Store(pathlib.Path(one)), pm.Store(pathlib.Path(two))
            self.assertEqual(store.read(), {'revision': 0, 'presets': []})
            preset = dict(id='review', name='Review', provider='openai-codex', model='gpt-test', prefix='Review carefully', enabled=True)
            saved = store.write([preset], 0)
            self.assertEqual(saved['revision'], 1)
            self.assertEqual(pm.Store(pathlib.Path(one)).read(), saved)
            self.assertEqual(other.read()['presets'], [])
            with self.assertRaises(pm.Conflict):
                store.write([], 0)
            for provider in ('prompt-models', 'custom:prompt-models', 'auto', 'moa'):
                with self.assertRaises(ValueError):
                    store.write([dict(preset, provider=provider)], 1)
            with self.assertRaises(ValueError):
                store.write([dict(preset, enabled='false')], 1)
            with self.assertRaises(ValueError):
                store.write([preset, preset], 1)
            self.assertEqual(store.read(), saved)
            self.assertEqual(store.write([], 1), {'revision': 2, 'presets': []})

class ClientTests(unittest.TestCase):
    def test_pinned_route_tools_reasoning_and_error_is_terminal(self):
        from types import SimpleNamespace as NS
        import tempfile
        import prompt_models as pm
        self.assertTrue(hasattr(pm, 'Client'), 'provider client missing')
        (ROOT / '.test-tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-tmp') as home:
            store = pm.Store(home)
            preset = dict(id='review', name='Review', provider='openai-codex', model='gpt-test', prefix='Review', enabled=True)
            store.write([preset], 0)
            calls = []
            reply = NS(choices=[NS(message=NS(content='OK', tool_calls=None), finish_reason='stop')], model='gpt-test')
            def create(**kwargs):
                calls.append(kwargs)
                return reply
            upstream = NS(chat=NS(completions=NS(create=create)), base_url='https://example.invalid')
            routes = []
            def resolve(provider, **kwargs):
                routes.append((provider, kwargs['model']))
                return upstream, kwargs['model']
            def build(provider, model, messages, **kwargs):
                return dict(model=model, messages=messages, **kwargs)
            client = pm.Client(store=store, resolver=resolve, builder=build)
            messages = [{'role': 'system', 'content': 'Hermes'}, {'role': 'user', 'content': 'Hello'}]
            tools = [{'type': 'function', 'function': {'name': 'test', 'parameters': {'type': 'object'}}}]
            self.assertIs(client.chat.completions.create(model='review', messages=messages, tools=tools,
                          _prompt_models_reasoning={'effort': 'high'}, stream=False), reply)
            self.assertEqual(routes, [('openai-codex', 'gpt-test')])
            self.assertEqual(calls[0]['messages'][0]['content'], 'Review\n\nHermes')
            self.assertEqual(calls[0]['tools'], tools)
            self.assertEqual(calls[0]['reasoning_config'], {'effort': 'high'})
            self.assertEqual(messages[0]['content'], 'Hermes')
            with self.assertRaises(pm.PinnedRouteError):
                client.chat.completions.create(model='review', messages=messages, extra_body={'model':'wrong-model'})
            self.assertEqual(len(calls), 1)
            store.write([dict(preset, model='different', prefix='Changed')], 1)
            client.chat.completions.create(model='review', messages=messages)
            self.assertEqual(routes[-1], ('openai-codex', 'gpt-test'))
            self.assertEqual(calls[-1]['messages'][0]['content'], 'Review\n\nHermes')
            def fail(**kwargs):
                raise ConnectionError('secret must not leak')
            upstream.chat.completions.create = fail
            with self.assertRaisesRegex(pm.PinnedRouteError, 'no fallback') as caught:
                client.chat.completions.create(model='review', messages=messages)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(len(routes), 4)
            store.write([dict(preset, enabled=False)], 2)
            with self.assertRaises(pm.PinnedRouteError):
                client.chat.completions.create(model='review', messages=messages)
            self.assertEqual(len(routes), 4)

if __name__ == '__main__':
    unittest.main()
