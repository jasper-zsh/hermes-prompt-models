import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import { Button, Input, Textarea, ListRow, ToggleRow, Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue, host, useValue, gatewayActivationEpoch } from '@hermes/plugin-sdk'

export function SettingsPage({ ctx }) {
  const profile = useValue(host.state.profile)
  const gateway = useValue(host.state.gateway)
  const [document, setDocument] = useState(null)
  const [models, setModels] = useState([])
  const [draft, setDraft] = useState(null)
  const [remove, setRemove] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const scope = useRef(null)
  const sequence = useRef(0)
  const mounted = useRef(true)
  const sameScope = () => mounted.current && scope.current === gatewayActivationEpoch()
  const message = err => err?.message || 'Request failed. Enable the backend plugin for this profile and reload.'

  async function reload() {
    const ticket = ++sequence.current
    const epoch = gatewayActivationEpoch()
    scope.current = epoch
    setBusy(true); setError(''); setStatus(''); setDraft(null); setRemove(null); setDocument(null)
    try {
      const [saved, available] = await Promise.all([ctx.rest('/presets'), ctx.rest('/models')])
      if (!mounted.current || ticket !== sequence.current || epoch !== gatewayActivationEpoch()) return
      setDocument(saved); setModels(available.models)
    } catch (err) {
      if (mounted.current && ticket === sequence.current && epoch === gatewayActivationEpoch()) setError(message(err))
    } finally {
      if (mounted.current && ticket === sequence.current) setBusy(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    reload()
    const timer = setInterval(() => {
      if (scope.current !== gatewayActivationEpoch()) reload()
    }, 500)
    return () => { mounted.current = false; sequence.current++; clearInterval(timer) }
  }, [profile, gateway])

  async function persist(presets) {
    if (!sameScope()) { await reload(); return }
    const epoch = scope.current
    setBusy(true); setError(''); setStatus('')
    try {
      const saved = await ctx.rest('/presets', {method:'PUT', body:{revision:document.revision, presets}})
      if (!sameScope() || epoch !== scope.current) return
      const verified = await ctx.rest('/presets')
      if (!sameScope() || epoch !== scope.current) return
      if (JSON.stringify(verified) !== JSON.stringify(saved)) throw new Error('Save could not be verified. Reload before editing again.')
      setDocument(verified); setDraft(null); setRemove(null); setStatus('Saved and verified')
    } catch (err) {
      if (sameScope() && epoch === scope.current) setError(message(err))
    } finally {
      if (sameScope() && epoch === scope.current) setBusy(false)
    }
  }
  const update = change => setDraft(previous => ({...previous, ...change}))
  const route = draft ? JSON.stringify([draft.provider, draft.model]) : ''
  const knownRoute = draft && models.some(row => row.provider === draft.provider && row.model === draft.model)
  const options = draft?.model && !knownRoute
    ? [...models, {provider:draft.provider, model:draft.model, label:`Unavailable: ${draft.provider} / ${draft.model}`}]
    : models
  const canSave = draft && draft.name.trim() && draft.provider && draft.model && draft.prefix.length <= 64000

  return jsxs('div', {className:'mx-auto max-w-4xl space-y-4 px-5 pb-12 text-foreground', children:[
    jsx(ListRow, {title:'Prompt Models', description:`Profile: ${profile}. Each preset is one fixed provider/model, with no cross-model fallback.`,
      action:jsx(Button, {variant:'outline', disabled:busy, onClick:reload, children:'Reload'})}),
    jsx('p', {className:'text-sm text-(--ui-text-tertiary)', children:'Prefixes are prepended only to a copied outbound system prompt. Hermes instructions and saved history remain intact. Responses are buffered, including Codex streaming, before delivery.'}),
    error && jsx('div', {role:'alert', className:'rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive', children:error}),
    status && jsx('p', {role:'status', className:'text-sm text-(--ui-text-secondary)', children:status}),
    busy && jsx('p', {role:'status', className:'text-sm text-(--ui-text-tertiary)', children:'Working…'}),
    document && !draft && jsxs('div', {children:[
      jsx(Button, {disabled:busy, onClick:() => {setStatus(''); setRemove(null); setDraft({id:'pm-'+crypto.randomUUID(), name:'', provider:'', model:'', prefix:'', enabled:true})}, children:'Add preset'}),
      !document.presets.length && jsx(ListRow, {title:'No prompt models yet', description:'Add a preset, choose a configured model, and write your own prefix. No default prompt is inserted.'}),
      ...document.presets.map(p => jsx(ListRow, {title:p.name, hint:`prompt-models / ${p.id}`,
        description:`${p.enabled ? 'Enabled' : 'Disabled'} · ${p.provider} / ${p.model}`,
        action:jsxs('div', {className:'flex gap-2', children:[
          jsx(Button, {variant:'outline', disabled:busy, 'aria-label':`Edit ${p.name}`, onClick:() => {setStatus(''); setRemove(null); setDraft({...p})}, children:'Edit'}),
          jsx(Button, {variant:'ghost', disabled:busy, 'aria-label':`Delete ${p.name}`, onClick:() => setRemove(p.id), children:'Delete'})
        ]})}, p.id)),
      remove && jsxs('div', {className:'rounded-md border border-(--ui-border) p-3 space-y-2', children:[
        jsx('p', {className:'text-sm', children:'Delete this preset? Sessions using it will fail closed on their next request.'}),
        jsx(Button, {variant:'destructive', disabled:busy, onClick:() => persist(document.presets.filter(p => p.id !== remove)), children:'Confirm delete'}),
        jsx(Button, {variant:'ghost', disabled:busy, onClick:() => setRemove(null), children:'Cancel'})
      ]})
    ]}),
    draft && jsxs('form', {onSubmit:event => {event.preventDefault(); if (canSave && !busy) persist([...document.presets.filter(p => p.id !== draft.id), draft])}, children:[
      jsx(ListRow, {title:'Preset name', description:'A recognizable label for these settings.', action:jsx(Input, {'aria-label':'Preset name', value:draft.name, maxLength:120, disabled:busy, onChange:e => update({name:e.target.value}), autoFocus:true})}),
      jsx(ListRow, {title:'Base model', description:'Only existing configured model routes are offered. Missing models can be configured in Hermes provider settings.', action:
        jsx(Select, {value:draft.model ? route : '', disabled:busy, onValueChange:value => {const [provider,model] = JSON.parse(value); update({provider, model})}, children:jsxs('div', {className:'w-full min-w-64', children:[
          jsx(SelectTrigger, {'aria-label':'Base model', className:'w-full', children:jsx(SelectValue, {placeholder:'Choose configured model'})}),
          jsx(SelectContent, {children:options.map(row => jsx(SelectItem, {value:JSON.stringify([row.provider,row.model]), children:row.label}, JSON.stringify([row.provider,row.model])))})
        ]})})}),
      !models.length && jsx('p', {className:'text-sm text-(--ui-text-tertiary)', children:'No configured models found. Configure a provider, then Reload. Catalog warm-up may require another reload.'}),
      jsx(ToggleRow, {label:'Enabled', checked:draft.enabled, disabled:busy, onChange:enabled => update({enabled}), description:'Disabled presets disappear from the picker and reject requests.'}),
      jsx(ListRow, {title:'System prefix', wide:true, description:'Your text is followed by the original Hermes system instructions. Leave empty for an unchanged prompt. Never put credentials here.', below:
        jsx(Textarea, {'aria-label':'System prefix', className:'mt-3 min-h-52 font-mono text-sm', value:draft.prefix, maxLength:64000, disabled:busy, onChange:e => update({prefix:e.target.value})})}),
      jsx('p', {className:'pb-3 text-xs text-(--ui-text-tertiary)', children:`${draft.prefix.length.toLocaleString()} / 64,000 characters. Changes to an active preset take effect in a new session; disable/delete revokes immediately.`}),
      jsxs('div', {className:'flex gap-2', children:[
        jsx(Button, {type:'submit', disabled:busy || !canSave, children:'Save preset'}),
        jsx(Button, {type:'button', variant:'outline', disabled:busy, onClick:() => setDraft(null), children:'Cancel'})
      ]})
    ]})
  ]})
}

export default {
  id:'prompt-models', name:'Prompt Models', defaultEnabled:false,
  register(ctx) {
    ctx.registerSettingsPage({id:'settings', title:'Prompt Models', icon:'symbol-keyword',
      render:() => jsx(SettingsPage, {ctx})})
  }
}
