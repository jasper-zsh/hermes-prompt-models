"""Prompt Models: profile-local virtual model presets, no prompt/history mutation."""
from copy import deepcopy
from pathlib import Path
import fcntl
import json
import os
import re
import tempfile

PROVIDER = 'prompt-models'


class Conflict(ValueError):
    """An editor saved against an outdated revision."""


def validate_presets(presets):
    if not isinstance(presets, list) or len(presets) > 100:
        raise ValueError('Expected a list of at most 100 presets')
    seen = set()
    fields = {'id', 'name', 'provider', 'model', 'prefix', 'enabled'}
    for row in presets:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError('A preset needs id, name, provider, model, prefix and enabled only')
        for key, limit in [('id', 64), ('name', 120), ('provider', 120), ('model', 512), ('prefix', 64000)]:
            if not isinstance(row[key], str) or len(row[key]) > limit or (key != 'prefix' and not row[key].strip()):
                raise ValueError('Invalid ' + key)
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', row['id']) or row['id'] in seen:
            raise ValueError('Preset ids must be unique lowercase slugs')
        if row['provider'].strip().lower().removeprefix('custom:') in {PROVIDER, 'auto', 'moa', 'custom'}:
            raise ValueError('Select an explicit real provider (named custom routes are supported)')
        if type(row['enabled']) is not bool:
            raise ValueError('enabled must be boolean')
        seen.add(row['id'])
    return deepcopy(presets)


class Store:
    """Atomic, permission-restricted storage under the currently bound profile home."""
    def __init__(self, home=None):
        if home is None:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
        self.directory = Path(home) / 'plugin-data' / PROVIDER
        self.path = self.directory / 'presets.json'

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'revision': 0, 'presets': []}
        if not isinstance(data, dict) or type(data.get('revision')) is not int or data['revision'] < 0:
            raise ValueError('Invalid preset document; restore a backup before editing')
        return {'revision': data['revision'], 'presets': validate_presets(data.get('presets'))}

    def write(self, presets, revision):
        presets = validate_presets(presets)
        if type(revision) is not int or revision < 0:
            raise ValueError('revision must be a nonnegative integer')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = os.open(self.directory / '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.read()
            if current['revision'] != revision:
                raise Conflict('Presets changed in another editor. Reload before saving.')
            data = {'revision': revision + 1, 'presets': presets}
            fd, name = tempfile.mkstemp(prefix='.presets-', dir=self.directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as target:
                    json.dump(data, target, ensure_ascii=False, indent=2)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return data

    def preset(self, model):
        for row in self.read()['presets']:
            if row['id'] == model and row['enabled']:
                return row
        raise ValueError('Prompt model is missing or disabled; choose an enabled preset')


class PinnedRouteError(RuntimeError):
    """Terminal failure: never hand a prompt-model request to another model."""


class Client:
    """OpenAI-shaped facade. Resolve host credentials, bypass its auxiliary fallback ladder."""
    def __init__(self, *, store=None, resolver=None, builder=None, **kwargs):
        from types import SimpleNamespace
        self.store = store or Store()
        self.resolver = resolver
        self.builder = builder
        self.snapshots = {}
        self.base_url = 'https://prompt-models.invalid/v1'
        self.api_key = 'prompt-models-local-facade'
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def close(self):
        # Underlying clients belong to Hermes's shared resolver cache.
        pass

    def with_options(self, **kwargs):
        # No credentials/options of the virtual facade may override the real route.
        return self

    def create(self, *, model, messages, **kwargs):
        try:
            current = self.store.preset(model)  # deleting/disabling immediately revokes the preset
            preset = self.snapshots.setdefault(model, current)
            resolver, builder = self.resolver, self.builder
            if resolver is None or builder is None:
                from agent.auxiliary_client import resolve_provider_client, _build_call_kwargs
                resolver = resolver or resolve_provider_client
                builder = builder or _build_call_kwargs
            client, resolved_model = resolver(preset['provider'], model=preset['model'])
            if client is None or resolved_model != preset['model']:
                raise PinnedRouteError('Pinned route unavailable or resolver changed model; no fallback permitted')
            outbound = prepend(messages, preset['prefix'])
            if self.resolver is None:
                from agent.auxiliary_client import CodexAuxiliaryClient
                from openai import OpenAI
                if isinstance(client, CodexAuxiliaryClient):
                    outbound = codex_system_messages(outbound)
                elif isinstance(client, OpenAI):
                    from providers import get_provider_profile
                    from agent.reasoning_carriers import shape_wire_carriers
                    from agent.transports.chat_completions import ChatCompletionsTransport
                    profile = get_provider_profile(preset['provider'])
                    outbound = shape_wire_carriers(outbound, model=preset['model'], base_url=str(client.base_url))
                    outbound = ChatCompletionsTransport().convert_messages(
                        outbound, model=preset['model'], provider=preset['provider'],
                        provider_profile=profile, base_url=str(client.base_url))
                    if profile is not None:
                        outbound = profile.prepare_messages(outbound)
            options = deepcopy(kwargs)
            reasoning = options.pop('_prompt_models_reasoning', None)
            # No task name: never inherit auxiliary task overrides or task-level model selection.
            request = builder(preset['provider'], preset['model'], outbound,
                              temperature=options.pop('temperature', None),
                              max_tokens=options.pop('max_tokens', options.pop('max_completion_tokens', None)),
                              tools=options.pop('tools', None), timeout=options.pop('timeout', 600.0),
                              extra_body=options.pop('extra_body', None), reasoning_config=reasoning,
                              base_url=str(getattr(client, 'base_url', '') or ''), task=None)
            stream = options.pop('stream', False)
            options.pop('stream_options', None)
            request.update(options)
            body = request.get('extra_body') or {}
            if any(key in body for key in ('model', 'models', 'fallback_models', 'messages', 'tools', 'instructions', 'input')):
                raise PinnedRouteError('Extra body cannot override the pinned route or prompt; no fallback permitted')
            request['stream'] = False
            # Codex/Anthropic host adapters internally consume their native stream and return a
            # completion. Buffering prevents a mid-stream error from triggering host continuation
            # or fallbacks after visible partial output. No retry/call_llm/recovery ladder here.
            from agent.auxiliary_client import CodexAuxiliaryClient
            if isinstance(client, CodexAuxiliaryClient):
                result = codex_completion_with_replay(client, request)
            else:
                result = client.chat.completions.create(**request)
            choices = getattr(result, 'choices', None)
            if not choices or not getattr(choices[0], 'message', None):
                raise PinnedRouteError('Pinned model returned an invalid completion; no fallback permitted')
            message = choices[0].message
            if getattr(choices[0], 'finish_reason', '') == 'content_filter' or not (
                getattr(message, 'content', None) or getattr(message, 'tool_calls', None)
            ):
                raise PinnedRouteError('Pinned model returned no answer/tool calls; no fallback permitted')
            return completion_stream(result) if stream else result
        except PinnedRouteError:
            raise
        except Exception as exc:
            # Do not expose an upstream body/header that may contain credentials.
            raise PinnedRouteError(f'Pinned model request failed ({type(exc).__name__}); no fallback permitted') from None


def codex_completion_with_replay(client, request):
    """Keep native replay discarded by the host's auxiliary chat-shaped return.

    Per-request proxies leave the shared/authenticated client untouched. The host
    still owns request conversion, streaming, timeout guards and stream closure.
    Re-consuming recorded events is local only, never another inference request.
    """
    from copy import copy
    from types import SimpleNamespace
    from agent.codex_runtime import _consume_codex_event_stream
    from agent.codex_responses_adapter import _normalize_codex_response

    captured, events = [], []

    class RecordingStream:
        def __init__(self, stream):
            self.stream = stream

        def __iter__(self):
            for event in self.stream:
                events.append(event)
                yield event

        def close(self):
            self.stream.close()

    class ClientProxy:
        def __init__(self, real):
            self.real = real
            self.responses = SimpleNamespace(create=self.create)

        def __getattr__(self, name):
            return getattr(self.real, name)

        def create(self, **kwargs):
            result = self.real.responses.create(**kwargs)
            if hasattr(result, 'output'):
                captured.append(result)
                return result
            return RecordingStream(result)

    adapter = copy(client.chat.completions)
    adapter._client = ClientProxy(adapter._client)
    result = adapter.create(**request)
    final = captured[0] if captured else (
        _consume_codex_event_stream(iter(events), model=request['model']) if events else None)
    if final is not None:
        # Preserve the adapter's alias handling, usage and finish-reason policy;
        # restore only the carriers its chat-shaped return omits.
        from agent.codex_responses_adapter import (
            classify_responses_route, _classify_responses_issuer, _wire_model_identity,
        )
        route = classify_responses_route(SimpleNamespace(provider=None, base_url=str(client.base_url)))
        native, _ = _normalize_codex_response(
            final, recover_leaked_tool_call=False,
            issuer_kind=_classify_responses_issuer(base_url=str(client.base_url), **route._asdict()),
            issuer_model=_wire_model_identity(request['model']),
        )
        for key in ('codex_reasoning_items', 'codex_message_items', 'reasoning_details'):
            value = getattr(native, key, None)
            if value is not None:
                setattr(result.choices[0].message, key, deepcopy(value))
    return result


def codex_system_messages(messages):
    """Host Responses adapter otherwise keeps only the LAST system message. Preserve all text."""
    result, texts, index = [], [], None
    for message in deepcopy(messages):
        if message.get('role') != 'system':
            result.append(message)
            continue
        if index is None:
            index = len(result)
        content = message.get('content')
        if isinstance(content, list):
            if any(not isinstance(p, dict) or p.get('type') not in {'text', 'input_text'} for p in content):
                raise ValueError('Unsupported non-text Responses system content')
            content = ''.join(p.get('text', '') for p in content)
        if content is not None and not isinstance(content, str):
            raise ValueError('Unsupported Responses system content')
        texts.append(content or '')
    if index is not None:
        result.insert(index, {'role': 'system', 'content': '\n\n'.join(texts)})
    return result


def completion_stream(result):
    """Expose a buffered native completion as an OpenAI-compatible stream without dropping fields."""
    from types import SimpleNamespace
    message = result.choices[0].message
    data = message.model_dump() if hasattr(message, 'model_dump') else vars(message).copy()
    calls = data.get('tool_calls')
    if calls:
        data['tool_calls'] = [SimpleNamespace(index=i, **(call if isinstance(call, dict) else vars(call)))
                              for i, call in enumerate(calls)]
        for call in data['tool_calls']:
            if isinstance(call.function, dict):
                call.function = SimpleNamespace(**call.function)
    yield SimpleNamespace(id=getattr(result, 'id', ''), model=getattr(result, 'model', ''),
                          choices=[SimpleNamespace(index=0, delta=SimpleNamespace(**data),
                                                   finish_reason=result.choices[0].finish_reason)],
                          usage=getattr(result, 'usage', None))


def prepend(messages, prefix):
    """Return an independent outbound message tree, modifying only its first system message."""
    result = deepcopy(messages)
    if not prefix:
        return result
    for message in result:
        if message.get('role') == 'system':
            content = message.get('content')
            if isinstance(content, str):
                message['content'] = prefix + '\n\n' + content
            elif isinstance(content, list):
                message['content'] = [{'type': 'text', 'text': prefix + '\n\n'}] + content
            elif content is None:
                message['content'] = prefix
            else:
                raise ValueError('Unsupported system content; refusing to replace it')
            return result
    result.insert(0, {'role': 'system', 'content': prefix})
    return result
