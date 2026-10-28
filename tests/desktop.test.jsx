import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import fs from 'node:fs'

async function load() {
  expect(fs.existsSync('desktop/plugin.js'), 'Desktop implementation missing').toBe(true)
  const modulePath = '../desktop/plugin.js'
  return import(/* @vite-ignore */ modulePath)
}
function backend() {
  let doc = { revision: 0, presets: [] }
  const rest = vi.fn(async (path, options) => {
    if (path === '/models') return {models:[{provider:'openai-codex',model:'gpt-test',label:'Codex / gpt-test'}]}
    if (options?.method === 'PUT') doc = { ...structuredClone(options.body), revision: doc.revision + 1 }
    return structuredClone(doc)
  })
  return {rest}
}
describe('native settings page', () => {
  it('registers only Settings and supports persisted create/edit/delete', async () => {
    const {default: plugin, SettingsPage} = await load()
    const ctx = backend()
    ctx.registerSettingsPage = vi.fn()
    ctx.register = vi.fn()
    plugin.register(ctx)
    expect(ctx.registerSettingsPage).toHaveBeenCalledOnce()
    expect(ctx.register).not.toHaveBeenCalled()
    render(<SettingsPage ctx={ctx} />)
    await screen.findByText('No prompt models yet')
    fireEvent.click(screen.getByRole('button', {name:'Add preset'}))
    fireEvent.change(screen.getByLabelText('Preset name'), {target:{value:'Review'}})
    fireEvent.change(screen.getByLabelText('System prefix'), {target:{value:'Review carefully'}})
    fireEvent.keyDown(screen.getByRole('combobox'), {key:'ArrowDown'})
    fireEvent.click(await screen.findByRole('option', {name:'Codex / gpt-test'}))
    fireEvent.click(screen.getByRole('button', {name:'Save preset'}))
    await screen.findByText('Saved and verified')
    expect(ctx.rest.mock.calls.some(([p,o]) => p === '/presets' && o?.body?.presets?.[0]?.prefix === 'Review carefully')).toBe(true)
    fireEvent.click(screen.getByRole('button', {name:'Edit Review'}))
    fireEvent.change(screen.getByLabelText('System prefix'), {target:{value:'New prefix'}})
    fireEvent.click(screen.getByRole('button', {name:'Save preset'}))
    await waitFor(() => expect(screen.queryByLabelText('System prefix')).toBeNull())
    fireEvent.click(screen.getByRole('button', {name:'Delete Review'}))
    fireEvent.click(screen.getByRole('button', {name:'Confirm delete'}))
    await screen.findByText('No prompt models yet')
  })
  it('renders backend errors without discarding the edit', async () => {
    const {SettingsPage} = await load()
    const ctx = {rest:vi.fn().mockRejectedValue(new Error('Backend disabled'))}
    render(<SettingsPage ctx={ctx} />)
    await screen.findByRole('alert')
    expect(screen.getByRole('alert').textContent).toContain('Backend disabled')
    expect(screen.getByRole('button', {name:'Reload'})).toBeTruthy()
  })
})
