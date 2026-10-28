"""Dedicated public transport registration preserves native replay until real routing."""
from copy import deepcopy
from agent.transports.base import ProviderTransport


class PromptTransport(ProviderTransport):
    @property
    def api_mode(self):
        return 'prompt_models'

    def convert_messages(self, messages, **kwargs):
        return deepcopy(messages)

    def convert_tools(self, tools):
        return deepcopy(tools)

    def normalize_response(self, response, **kwargs):
        from agent.transports.chat_completions import ChatCompletionsTransport
        from agent.transports.chat_completions import _attr_or_model_extra
        chat = ChatCompletionsTransport()
        normalized = chat.normalize_response(response, **kwargs)
        message = response.choices[0].message
        data = deepcopy(getattr(message, 'provider_data', None) or {})
        data.update(normalized.provider_data or {})
        for key in ('codex_reasoning_items', 'codex_message_items',
                    'anthropic_content_blocks', 'bedrock_content_blocks'):
            value = _attr_or_model_extra(message, key)
            if value is not None:
                data[key] = deepcopy(value)
        normalized.provider_data = data or None
        # Normalize individually: the host filters malformed calls, so zipping
        # the original list against its filtered result can attach wrong IDs.
        calls = []
        for source in getattr(message, 'tool_calls', None) or []:
            call = chat._normalize_tool_call(source)
            if call is None:
                continue
            data = deepcopy(getattr(source, 'provider_data', None) or {})
            data.update(call.provider_data or {})
            for key in ('call_id', 'response_item_id'):
                value = _attr_or_model_extra(source, key)
                if value is not None:
                    data[key] = deepcopy(value)
            call.provider_data = data or None
            calls.append(call)
        normalized.tool_calls = calls or None
        return normalized

    def extract_cache_stats(self, response):
        from agent.transports.chat_completions import ChatCompletionsTransport
        return ChatCompletionsTransport().extract_cache_stats(response)

    def build_kwargs(self, model, messages, tools=None, **params):
        request = {'model': model, 'messages': self.convert_messages(messages),
                   '_prompt_models_reasoning': deepcopy(params.get('reasoning_config'))}
        if tools is not None:
            request['tools'] = deepcopy(tools)
        for name in ('temperature', 'max_tokens', 'timeout'):
            if params.get(name) is not None:
                request[name] = params[name]
        if params.get('extra_body_additions'):
            request['extra_body'] = deepcopy(params['extra_body_additions'])
        for name, value in (params.get('request_overrides') or {}).items():
            if name in {'model', 'messages', 'tools', '_prompt_models_reasoning', 'base_url', 'api_key'}:
                continue  # a request override can never unpin the model or replace its context
            if name == 'extra_body':
                request[name] = {**request.get(name, {}), **deepcopy(value)}
            else:
                request[name] = deepcopy(value)
        return request
