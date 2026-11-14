import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backendTarget =
  process.env.VITE_API_BASE_URL || (process.argv.includes('5175') ? 'off' : 'http://127.0.0.1:8000')
const proxyDisabled = backendTarget === 'off' || backendTarget === 'none'

export default defineConfig({
  envDir: '..',
  plugins: [react(), tailwindcss()],
  server: proxyDisabled
    ? undefined
    : {
        proxy: {
          '/v0.1': { target: backendTarget, changeOrigin: true },
          '/health': { target: backendTarget, changeOrigin: true },
          '/openapi.json': { target: backendTarget, changeOrigin: true },
          '/docs': { target: backendTarget, changeOrigin: true },
          '/mcp': { target: backendTarget, changeOrigin: true },
        },
      },
})
