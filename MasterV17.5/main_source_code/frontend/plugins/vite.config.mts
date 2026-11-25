import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv, type UserConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const appRoot = fileURLToPath(new URL("..", import.meta.url));
const envDir = appRoot;
const hotReloadDisabled = process.env.DISABLE_HOT_RELOAD === "true";
const usePolling = process.env.VITE_USE_POLLING === "true";

// https://vite.dev/config/
export default defineConfig(async ({ mode }) => {
  const env = loadEnv(mode, envDir, "");
  // Default dev proxy follows BACKEND_PORT from main_source_code/backend/.env.
  const backendEnv = loadEnv(mode, path.resolve(appRoot, "../backend"), "");
  const localBackend = `http://127.0.0.1:${backendEnv.BACKEND_PORT || "7007"}`;
  const codespaceName = process.env.CODESPACE_NAME;
  const forwardingDomain = process.env.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN;
  const isCodespaces = Boolean(codespaceName && forwardingDomain);
  const configuredBackendUrl = process.env.VITE_BACKEND_URL
    || process.env.REACT_APP_BACKEND_URL
    || env.VITE_BACKEND_URL
    || env.REACT_APP_BACKEND_URL;
  const sameOriginApi = isCodespaces
    || process.env.VITE_API_SAME_ORIGIN === "true"
    || !configuredBackendUrl;
  const backendUrl = sameOriginApi ? "" : configuredBackendUrl;
  process.env.VITE_BACKEND_URL = backendUrl;
  return {
    root: appRoot,
    envDir,
    define: {
      "import.meta.env.VITE_BACKEND_URL": JSON.stringify(backendUrl),
    },
    plugins: [
      react(),
      tailwindcss(),
    ],
    resolve: {
      alias: [
        { find: "@", replacement: path.resolve(appRoot, "./src") },
        // lucide 1.x dropped brand logos; src/lib/lucide-react.tsx restores them on top of the real package.
        { find: /^lucide-react$/, replacement: path.resolve(appRoot, "./src/lib/lucide-react.tsx") },
        { find: "lucide-react-upstream", replacement: path.resolve(appRoot, "./node_modules/lucide-react") },
        // recharts 3's Tooltip callback types reject the annotations agents write; src/lib/recharts.tsx adapts them.
        { find: /^recharts$/, replacement: path.resolve(appRoot, "./src/lib/recharts.tsx") },
        { find: "recharts-upstream", replacement: path.resolve(appRoot, "./node_modules/recharts") },
      ],
    },
    // Every shipped dep, pre-bundled up front. Vite discovers deps lazily, so the first
    // import outside the initial graph would trigger a re-optimize + reload mid-session.
    optimizeDeps: {
      include: [
        "@base-ui/react/button",
        "@base-ui/react/checkbox",
        "@base-ui/react/dialog",
        "@base-ui/react/input",
        "@base-ui/react/menu",
        "@base-ui/react/merge-props",
        "@base-ui/react/popover",
        "@base-ui/react/select",
        "@base-ui/react/tabs",
        "@base-ui/react/use-render",
        "@tanstack/react-query",
        "class-variance-authority",
        "clsx",
        "date-fns",
        "@icons-pack/react-simple-icons",
        "lucide-react-upstream",
        "motion/react",
        "next-themes",
        "react",
        "react-day-picker",
        "react-dom/client",
        "react-is",
        "react-router-dom",
        "recharts-upstream",
        "sonner",
        "lightweight-charts",
        "tailwind-merge",
      ],
    },
    server: {
      host: true,
      port: Number(process.env.PORT || env.DEV_PORT || 5173),
      strictPort: true,
      allowedHosts: true,
      // node_modules is a symlink into dependencies/frontend; allow serving its font files.
      fs: { allow: [appRoot, path.resolve(appRoot, "../../dependencies/frontend")] },
      cors: true,
      proxy: sameOriginApi ? {
        "/api": {
          target: isCodespaces
            ? process.env.CODESPACE_BACKEND_PROXY_TARGET || localBackend
            : process.env.VITE_BACKEND_PROXY_TARGET || env.VITE_BACKEND_PROXY_TARGET || localBackend,
          changeOrigin: true,
        },
      } : undefined,
      hmr: hotReloadDisabled ? false : {
        overlay: true,
        ...(process.env.VITE_HMR_CLIENT_PORT ? { clientPort: Number(process.env.VITE_HMR_CLIENT_PORT) } : {}),
        ...(process.env.VITE_HMR_PROTOCOL ? { protocol: process.env.VITE_HMR_PROTOCOL as "ws" | "wss" } : {}),
      },
      watch: hotReloadDisabled ? null : usePolling ? { usePolling: true, interval: 300 } : undefined,
    },
  } satisfies UserConfig;
});
