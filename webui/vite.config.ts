import { resolve } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// 后端 WebAPI 地址。可用环境变量 NAHIDA_API_URL 覆盖，
// start-all.bat 会自动把 config.yaml 里 webapi.port 对应的地址传进来。
const apiTarget = process.env.NAHIDA_API_URL || "http://127.0.0.1:6186";

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: resolve(__dirname, "dist"),
    emptyOutDir: true,
  },
});
