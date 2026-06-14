import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    watch: { usePolling: true }, // reliable HMR inside Docker
    // Container serves on 5173 but it's published to the host as 5273.
    // Point the browser-side HMR socket at the host-mapped port, otherwise
    // Vite advertises the internal Docker IP (ws://172.19.x.x:5173) which
    // the browser can't reach.
    hmr: { host: "localhost", clientPort: 5273 },
  },
});
