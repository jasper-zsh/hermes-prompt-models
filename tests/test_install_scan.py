"""Exercise the real Hermes installer gate, not a substitute secret regex."""
from pathlib import Path
import unittest


class InstallScanTests(unittest.TestCase):
    def test_installer_allows_package_without_force(self):
        from tools.plugin_guard import scan_plugin, should_allow_plugin_install
        result = scan_plugin(Path(__file__).resolve().parents[1])
        allowed, reason = should_allow_plugin_install(result, force=False)
        self.assertEqual(result.verdict, 'safe', result.summary)
        self.assertIs(allowed, True, reason)
        self.assertFalse([f for f in result.findings
                          if f.severity in {'critical', 'high'}], result.findings)

    def test_facade_has_no_credential_but_supports_host_routed_client(self):
        from agent.agent_init import _client_kwargs_from_routed
        from prompt_models import Client
        client = Client()
        self.assertIsNone(client.api_key)
        kwargs = _client_kwargs_from_routed(client, 30)
        self.assertIsNone(kwargs['api_key'])
        self.assertEqual(kwargs['base_url'], 'https://prompt-models.invalid/v1')
        self.assertEqual(kwargs['timeout'], 30)
