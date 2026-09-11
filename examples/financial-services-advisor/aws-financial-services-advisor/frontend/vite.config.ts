import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const rootDir = path.dirname(fileURLToPath(import.meta.url))

// Backend target for the dev proxy. Set BACKEND_URL when the FastAPI app runs
// somewhere other than http://localhost:8000 (a container, a tunnel, ...).
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(rootDir, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // The app talks to a relative `/api` by default, so dev needs no CORS.
      // A deployed build points at an absolute URL via VITE_API_URL instead.
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
