import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [
    vue(),
    {
      name: "spa-fallback",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (
            req.method === "GET" &&
            req.url &&
            !req.url.startsWith("/api") &&
            !req.url.startsWith("/@") &&
            !req.url.startsWith("/src") &&
            !req.url.includes(".")
          ) {
            req.url = "/";
          }
          next();
        });
      }
    }
  ],
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
    },
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://localhost:8010",
        changeOrigin: true
      }
    }
  }
});
