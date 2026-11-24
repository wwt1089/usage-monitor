import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { copilotManagedRuntime } from "@microsoft/cmr-apps-vite-plugin";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), copilotManagedRuntime()],
});
