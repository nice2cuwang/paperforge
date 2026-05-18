import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "vue-router": "vue-router/dist/vue-router.mjs"
    }
  },
  optimizeDeps: {
    exclude: ["vue-router"]
  },
  server: {
    host: "0.0.0.0",
    port: 5174,
    strictPort: true,
    hmr: {
      host: "localhost",
      port: 5174,
      clientPort: 5174
    }
  }
});
