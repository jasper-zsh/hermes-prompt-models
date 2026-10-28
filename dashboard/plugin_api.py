"""Profile-scoped Desktop REST namespace. No secrets or credential endpoints."""
import importlib.util
from pathlib import Path
from fastapi import APIRouter, HTTPException

# The dashboard loader imports this as a standalone module, not as our Python package.
_spec = importlib.util.spec_from_file_location('_prompt_models_api_core', Path(__file__).resolve().parents[1] / 'prompt_models.py')
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)
router = APIRouter()


def configured_models():
    """Use the same configured-provider inventory as Desktop; expose IDs/labels only."""
    from hermes_cli.inventory import load_picker_context, build_models_payload
    payload = build_models_payload(load_picker_context(), explicit_only=True,
                                   probe_custom_providers=False, non_blocking_catalogs=True)
    result = {}
    for provider in payload['providers']:
        slug = provider.get('slug', '')
        if not slug or slug in {core.PROVIDER, 'auto', 'moa', 'custom'}:
            continue
        for model in provider.get('models', []):
            if not isinstance(model, str) or not model:
                continue
            result[(slug, model)] = {'provider': slug, 'model': model,
                                     'label': f"{provider.get('name', slug)} / {model}"}
    return sorted(result.values(), key=lambda row: (row['provider'], row['model']))


@router.get('/models')
def models():
    return {'models': configured_models()}


@router.get('/presets')
def presets():
    try:
        return core.Store().read()
    except (ValueError, OSError):
        raise HTTPException(500, 'Cannot read preset storage. Restore a valid backup or check permissions.') from None


@router.put('/presets')
def save_presets(body: dict):
    try:
        if set(body) != {'presets', 'revision'}:
            raise ValueError('Expected presets and revision')
        rows = core.validate_presets(body['presets'])
        store = core.Store()
        previous = store.read()
        if body['revision'] != previous['revision']:
            raise core.Conflict('Presets changed in another editor. Reload before saving.')
        allowed = {(p['provider'], p['model']) for p in configured_models()}
        # A disconnected provider must not prevent deleting/disabling or editing a saved prefix.
        prior_routes = {(p['id'], p['provider'], p['model']) for p in previous['presets']}
        for row in rows:
            route = (row['provider'], row['model'])
            if route not in allowed and (row['id'], *route) not in prior_routes:
                raise ValueError('Choose an existing configured model. Refresh the model list if needed.')
        result = store.write(rows, body['revision'])
        # The host's normal picker cache can otherwise retain a deleted/disabled virtual ID.
        from hermes_cli.models import clear_provider_models_cache
        clear_provider_models_cache(core.PROVIDER)
        return result
    except core.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(500, 'Cannot save presets; check profile storage permissions.') from None
