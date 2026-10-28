"""Provider discovery entry point; dashboard API is loaded independently by Hermes."""
import os
from .prompt_models import Client, PROVIDER, Store
from .transport import PromptTransport
from providers import register_provider
from providers.base import ProviderProfile
from agent.transports import register_transport

# A non-secret in-process sentinel admits this credentialless virtual provider to the host's
# API-key provider picker/runtime. It is NOT an upstream credential, is never persisted, and
# is never forwarded to a real endpoint. The .invalid hostname cannot be an inference route.
os.environ['HERMES_PROMPT_MODELS_LOCAL_FACADE'] = 'prompt-models-local-facade'


def terminal_error(error, **kwargs):
    return {'reason': 'format_error', 'retryable': False, 'should_fallback': False,
            'should_rotate_credential': False, 'should_compress': False}


class PromptProfile(ProviderProfile):
    def fetch_models(self, **kwargs):
        return [p['id'] for p in Store().read()['presets'] if p['enabled']]

    def discover_models(self, **kwargs):
        return [{'id': p['id'], 'label': p['name'], 'note': p['provider'] + ' / ' + p['model']}
                for p in Store().read()['presets'] if p['enabled']]

    def create_client(self, **kwargs):
        # Always return a facade, even with an empty/malformed configuration. Validation is
        # delayed until create(), where our terminal error policy prevents core fallback.
        return Client(**kwargs)

    def get_model_context_length(self, model):
        from agent.model_metadata import get_model_context_length
        row = Store().preset(model)
        return get_model_context_length(row['model'], provider=row['provider'])


register_transport('prompt_models', PromptTransport)
register_provider(PromptProfile(
    name=PROVIDER, display_name='Prompt Models', api_mode='prompt_models',
    description='Fixed model routes with outbound-only prompt prefixes',
    env_vars=('HERMES_PROMPT_MODELS_LOCAL_FACADE',),
    base_url='https://prompt-models.invalid/v1',
    supports_health_check=False, supports_streaming=False,
    classify_api_error=terminal_error,
))
