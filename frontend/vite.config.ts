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
        //
        // IMPORTANT: keep `antd` / `@ant-design` / `rc-*` and `react` /
        // `scheduler` in the SAME chunk. Splitting them produces a circular
        // chunk graph (antd ↔ react-vendor ↔ vendor) whose ES-module
        // live-bindings resolve to `undefined` at evaluation time, so the
        // antd chunk crashes with "Cannot read properties of undefined
        // (reading 'version')" before React ever renders. One shared chunk
        // removes the cycle entirely.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (
            id.includes("antd") ||
            id.includes("@ant-design") ||
            id.includes("/rc-") ||
            id.includes("react") ||
            id.includes("scheduler")
          ) {
            return "react-vendor";
          }
          return "vendor";
        },
      },
    },
  },
})
