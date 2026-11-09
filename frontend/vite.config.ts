import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

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
        // PRD 4.4 — 오늘 UI 변경 전 스냅샷은 개발 환경에서만 별도 문서로 비교한다.
        name: "retriever-ui-baseline",
        apply: "serve",
        configureServer(server) {
          const directory = fileURLToPath(new URL("./.tmp/ui-baseline/", import.meta.url));
          server.middlewares.use((request, response, next) => {
            const url = new URL(request.url || "/", "http://localhost");
            const isIndex = url.pathname === "/" && url.searchParams.get("ui-version") === "baseline";
            const filename = isIndex ? "index.html" : url.pathname.replace("/__ui-baseline__/", "");
            if (!isIndex && !url.pathname.startsWith("/__ui-baseline__/")) return next();
            if (!["index.html", "app.js", "styles.css"].includes(filename)) {
              response.statusCode = 404;
              return response.end();
            }
            const path = `${directory}${filename}`;
            if (!existsSync(path)) {
              response.statusCode = 503;
              response.setHeader("Content-Type", "text/plain; charset=utf-8");
              return response.end("기존 화면 스냅샷이 준비되지 않았습니다.");
            }
            let content = readFileSync(path, "utf8");
            if (isIndex) content = content
              .replace('<html lang="ko">', '<html lang="ko" data-theme="retriever-modern">')
              .replace("__KAKAO_SDK_URL__", kakaoSdkUrl)
              .replace('src="/app.js"', 'src="/__ui-baseline__/app.js"')
              .replace('href="/styles.css"', 'href="/__ui-baseline__/styles.css"');
            response.setHeader("Cache-Control", "no-store");
            response.setHeader("Content-Type", filename.endsWith(".js") ? "text/javascript; charset=utf-8"
              : filename.endsWith(".css") ? "text/css; charset=utf-8" : "text/html; charset=utf-8");
            response.end(content);
          });
        },
      },
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
