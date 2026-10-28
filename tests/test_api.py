import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class ApiTests(unittest.TestCase):
    def test_api_crud_persists_and_rejects_unconfigured_routes(self):
        self.assertTrue((ROOT / 'dashboard/plugin_api.py').exists(), 'backend API missing')
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        spec = importlib.util.spec_from_file_location('api_under_test', ROOT / 'dashboard/plugin_api.py')
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/prompt-models')
        client = TestClient(app)
        prefix = '/api/plugins/prompt-models'
        with patch.object(api, 'configured_models', return_value=[dict(provider='openai-codex', model='gpt-test', label='Codex / gpt-test')]):
            document = client.get(prefix + '/presets').json()
            p = dict(id='api-preset', name='API preset', provider='openai-codex', model='gpt-test', prefix='', enabled=True)
            r = client.put(prefix + '/presets', json=dict(revision=document['revision'], presets=[p]))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(client.get(prefix + '/presets').json(), r.json())
            self.assertEqual(client.get(prefix + '/models').json()['models'][0]['model'], 'gpt-test')
            stale = client.put(prefix + '/presets', json=document)
            self.assertEqual(stale.status_code, 409)
            invalid = client.put(prefix + '/presets', json=dict(revision=r.json()['revision'], presets=[dict(p, model='not-configured')]))
            self.assertEqual(invalid.status_code, 422)
            removed = client.put(prefix + '/presets', json=dict(revision=r.json()['revision'], presets=[]))
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(client.get(prefix + '/presets').json()['presets'], [])
