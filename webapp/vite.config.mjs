// vite.config.mjs

// Vite replaces Vue CLI as the build tool for Vue 3.
// Note: Comments are generated to explain relevant configuration options.

import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const isProduction = mode === "production";

  var target;

  if (command === "serve") {
    if (env.VITE_API_TARGET === "local") {
      target = "http://localhost:5000";
    } else if (env.VITE_API_TARGET === "microscope") {
      target = env.VITE_MICROSCOPE_HOST || "http://microscope.local:5000";
    } else {
      throw new Error(`Invalid VITE_API_TARGET: ${env.VITE_API_TARGET}`);
    }
    // Allow a console log as we were serving for development.
    // eslint-disable-next-line no-console
    console.log(`Proxying /api → ${target}`);
  }

  return {
    plugins: [
      vue({
        // Strip "data-test-id" attributes (used by Vitest) from the production build.
        // This enforces separation of concerns and prevents developers from relying on
        // test IDs for styling or core application logic.
        template: {
          compilerOptions: {
            // Only apply this AST transformation during production builds
            nodeTransforms: isProduction
              ? [
                  (node) => {
                    // Check if the current AST node is an HTML element
                    // Node.type 1 is equal to node.type ELEMENT
                    // https://developer.mozilla.org/es/docs/Web/API/Node/nodeType
                    // https://developer.mozilla.org/es/docs/Web/API/Element
                    if (node.type === 1) {
                      // Filter out any property named "data-test-id"
                      node.props = node.props.filter((prop) => prop.name !== "data-test-id");
                    }
                  },
                ]
              : [],
          },
        },
      }),
    ],

    build: {
      // Output directory for production builds
      outDir: "../src/openflexure_microscope_server/static",
      // Clear output directory before build
      emptyOutDir: true,
      chunkSizeWarningLimit: 400,
      rollupOptions: {
        output: {
          manualChunks(id) {
            // Only split out third-party dependencies from node_modules
            if (id.includes("node_modules")) {
              if (id.includes("vue") || id.includes("pinia")) {
                return "vue-vendor";
              }
              if (id.includes("openseadragon")) {
                return "openseadragon";
              }
              if (id.includes("uikit")) {
                return "uikit";
              }
              if (
                id.includes("axios") ||
                id.includes("mitt") ||
                id.includes("mousetrap") ||
                id.includes("@vueuse")
              ) {
                return "utils";
              }
            }
          },
        },
      },
    },

    resolve: {
      alias: {
        // Setup path alias for src directory.
        // This allows importing modules using '@/path/to/module'.
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
      // Recognize these file extensions for module resolution.
      extensions: [".mjs", ".js", ".ts", ".jsx", ".tsx", ".json", ".vue"],
    },
    server: {
      host: true,
      // Set the development server port to 8080.
      port: 8080,
      strictPort: true,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
        },
      },
    },
    css: {
      preprocessorOptions: {
        less: {
          // Always include math in Less files.
          math: "always",
          // Enable relative URLs in Less files.
          relativeUrls: true,
          // Enable JavaScript in Less files
          javascriptEnabled: true,
        },
      },
    },
  };
});
