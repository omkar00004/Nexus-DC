import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Interfaces are HTTP clients of the FastAPI backend only — the /api proxy is
// the single doorway (no agent imports, no direct file reads).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // honor the port assigned by the launcher (PORT env); 5173 when run manually
    port: Number(process.env.PORT) || 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
