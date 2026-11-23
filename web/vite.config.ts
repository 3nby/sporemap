import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// no @types/node in here, so reach process.env the long way
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env || {}

// builds straight into the python package so `sporemap serve` can hand it out
export default defineConfig({
  plugins: [react()],
  // elk runs in a worker (its own file), so the main bundle stays small. the warning
  // only knows about chunks, so the 1.6mb worker asset doesnt trip it
  build: { outDir: '../src/sporemap/ui', emptyOutDir: true },
  server: {
    // npm run dev talks to a running `sporemap serve`. point it at the CT for real data:
    //   $env:SPOREMAP_API = 'http://10.17.48.147:8420'; npm run dev
    proxy: { '/api': env.SPOREMAP_API || 'http://127.0.0.1:8420' },
  },
})
