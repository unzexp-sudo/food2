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
        // Put ALL of node_modules into a single vendor chunk.
        //
        // We previously tried to split heavy vendors (antd, @ant-design, rc-*
        // and react/scheduler) into a "react-vendor" chunk and everything
        // else into a "vendor" chunk so they could be cached independently.
        // That split produced CIRCULAR chunk imports: antd's transitive deps
        // (e.g. dayjs, scroll-into-view-if-needed, compute-scroll-into-view)
        // ended up in `vendor`, while antd itself lived in `react-vendor`,
        // and a peer in `vendor` referenced back into `react-vendor`. The
        // result was ES-module live-binding / TDZ errors at boot:
        //   • "Cannot read properties of undefined (reading 'version')" in
        //     the antd chunk (React binding was uninitialized)
        //   • "Cannot access 'fo' before initialization" in the vendor chunk
        //     (a let/const was read before the chunk's body had run)
        // Merging everything into ONE chunk eliminates cross-vendor-chunk
        // imports entirely, so cycles are impossible. The chunk is larger
        // but only downloaded once and cached long-term — an acceptable
        // trade-off for an internal ERP.
        manualChunks(id) {
          if (id.includes("node_modules")) return "vendor";
        },
      },
    },
  },
})
