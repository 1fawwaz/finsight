import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Streamlit components are served as a single static bundle from `build/`,
// embedded in an iframe -- relative asset paths (base: "./") are required
// since the iframe's origin/path won't match the app's own.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 3001,
  },
  build: {
    outDir: "build",
  },
});
