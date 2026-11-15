import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'

const runnerVersion = readFileSync(new URL('../runner/pyproject.toml', import.meta.url), 'utf8').match(/^version = "([0-9.]+)"$/m)?.[1]
if (!runnerVersion) throw new Error('Runner version is missing')

export default defineConfig({
  plugins: [vue()],
  define: { 'import.meta.env.VITE_RUNNER_VERSION': JSON.stringify(runnerVersion) },
  server: {
    host: '127.0.0.1',
    port: 5188,
    strictPort: true,
    headers: { 'Cache-Control': 'no-store' },
    proxy: {
      '^/oauth/(authorize|token|revoke|device/code)$': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        ws: true,
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        cookieDomainRewrite: '',
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4188,
    strictPort: true,
  },
})
