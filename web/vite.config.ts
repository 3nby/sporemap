import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// builds straight into the python package so `sporemap serve` can hand it out
export default defineConfig({
  plugins: [react()],
  // elk alone is ~1.5mb, fine for a page served from your own box
  build: { outDir: '../src/sporemap/ui', emptyOutDir: true, chunkSizeWarningLimit: 2500 },
  server: {
    // npm run dev talks to a running `sporemap serve`
    proxy: { '/api': 'http://127.0.0.1:8420' },
  },
})
