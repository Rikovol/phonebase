import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    /** Не переключаться на 5174 молча — иначе открывают старый URL и «не работает». */
    strictPort: true,
    // Иначе на Windows Vite может слушать только [::1], и 127.0.0.1:5173 не открывается
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/media": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
