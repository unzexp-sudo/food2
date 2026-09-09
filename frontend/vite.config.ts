import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split heavy vendors into their own long-cacheable chunks so they load
        // in parallel and aren't re-downloaded when a feature chunk changes.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("antd") || id.includes("@ant-design") || id.includes("/rc-")) {
            return "antd";
          }
          if (id.includes("react") || id.includes("scheduler")) return "react-vendor";
          return "vendor";
        },
      },
    },
  },
})
