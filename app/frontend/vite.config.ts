import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Local host run defaults to the API on localhost:8000. Set
  // VITE_API_PROXY_TARGET to point the dev proxy at another API.
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: true,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          // Split the two heaviest, rarely-changing libraries into their own
          // chunks so a redeploy of app code doesn't invalidate their cache.
          //
          // This has to be a function, not the equivalent object shorthand.
          // react-leaflet pulls in `react-dom` (the plain entry, for its
          // createPortal calls) while main.tsx pulls in `react-dom/client` (for
          // createRoot) -- two different subpaths of the same package, which
          // Rollup does not always dedupe against each other. With the object
          // form that left a second, private copy of React and ReactDOM bundled
          // inside the `leaflet` chunk: two live reconcilers on any screen that
          // renders a map, which is what was making the inputs on those screens
          // (the merchant form, the branch radius) drop focus after one
          // keystroke while every other screen was fine. Routing react/react-dom
          // to their own chunk first, before the id matches leaflet or recharts,
          // keeps a single shared copy for the whole app.
          manualChunks(id) {
            if (!id.includes('node_modules')) return
            if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react'
            if (id.includes('leaflet')) return 'leaflet'
            if (id.includes('recharts')) return 'recharts'
          },
        },
      },
    },
  }
})
