import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, "..", "");
  const kakaoSdkUrl = env.KAKAO_JAVASCRIPT_KEY
    ? `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(env.KAKAO_JAVASCRIPT_KEY)}&autoload=false&libraries=clusterer`
    : "__KAKAO_SDK_URL__";

  return {
    envDir: "..",
    plugins: [
      react(),
      {
        name: "retriever-kakao-sdk",
        apply: "serve",
        transformIndexHtml(html) {
          return html.replace("__KAKAO_SDK_URL__", kakaoSdkUrl);
        },
      },
    ],
    server: {
      proxy: {
        "/api": "http://127.0.0.1:8000",
      },
    },
    build: {
      outDir: "../src/retriever_lost_found/web/static",
      emptyOutDir: true,
      cssCodeSplit: false,
      rollupOptions: {
        output: {
          entryFileNames: "app.js",
          chunkFileNames: "assets/[name]-[hash].js",
          assetFileNames: (assetInfo) => (
            assetInfo.names.some((name) => name.endsWith(".css"))
              ? "styles.css"
              : "assets/[name]-[hash][extname]"
          ),
        },
      },
    },
  };
});
