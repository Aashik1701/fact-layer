import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/facts': 'http://127.0.0.1:8000',
      '/relations': 'http://127.0.0.1:8000',
      '/documents': 'http://127.0.0.1:8000',
      '/entities': 'http://127.0.0.1:8000',
      '/graph': 'http://127.0.0.1:8000',
      '/retrieval': 'http://127.0.0.1:8000',
      '/jobs': 'http://127.0.0.1:8000',
      '/ingest': 'http://127.0.0.1:8000',
      '/stats': 'http://127.0.0.1:8000',
      '/clusters': 'http://127.0.0.1:8000',
      '/rejected-facts': 'http://127.0.0.1:8000',
      '/page-image': 'http://127.0.0.1:8000',
    },
  },
})
