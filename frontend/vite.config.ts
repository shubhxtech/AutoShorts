import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The engine requires its per-install access token on /api and /media. In development the dashboard
// runs on :5173, so the proxy attaches the token read from the data folder.
function dataHome(): string {
  const env = process.env.AUTOSHORTS_HOME || process.env.CLIPFORGE_HOME;
  if (env) return env;
  const base = process.platform === "darwin" ? join(homedir(), "Library", "Application Support")
    : process.platform === "win32" ? (process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"))
    : (process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"));
  const name = process.platform === "darwin" || process.platform === "win32" ? "AutoShorts" : "autoshorts";
  const legacy = process.platform === "darwin" || process.platform === "win32" ? "ClipForge" : "clipforge";
  return existsSync(join(base, name)) || !existsSync(join(base, legacy)) ? join(base, name) : join(base, legacy);
}

function token(): string {
  const p = join(dataHome(), "secrets", "api_token");
  return existsSync(p) ? readFileSync(p, "utf8").trim() : "";
}

const target = "http://127.0.0.1:3000";
const withToken = {
  target,
  configure: (proxy: { on: (e: string, cb: (req: { setHeader: (k: string, v: string) => void }) => void) => void }) => {
    proxy.on("proxyReq", (req) => { const t = token(); if (t) req.setHeader("X-AutoShorts-Token", t); });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": withToken, "/media": withToken },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
