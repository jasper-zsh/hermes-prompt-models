import { defineConfig } from 'vitest/config'
import path from 'node:path'
import os from 'node:os'
const host = process.env.HERMES_SOURCE || path.join(os.homedir(), '.hermes/hermes-agent')
export default defineConfig({
  resolve: { alias: {
    '@hermes/plugin-sdk': path.resolve('tests/sdk.ts'),
    '@': path.join(host, 'apps/desktop/src'),
  } },
  test: { environment: 'jsdom', include: ['tests/*.test.jsx'], setupFiles: ['tests/setup.js'] },
})
