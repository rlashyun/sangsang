import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// PRD 4.4 — 오늘 UI 변경 전 버전. .tmp 스냅샷은 로컬 비교 전용이며 커밋하지 않는다.
const revision = "8f701b0";
const repository = fileURLToPath(new URL("../../", import.meta.url));
const destination = new URL("../.tmp/ui-baseline/", import.meta.url);
mkdirSync(destination, { recursive: true });
for (const name of ["index.html", "app.js", "styles.css"]) {
  const content = execFileSync("git", ["show", `${revision}:src/retriever_lost_found/web/static/${name}`], {
    cwd: repository,
    maxBuffer: 10 * 1024 * 1024,
  });
  writeFileSync(new URL(name, destination), content);
}
writeFileSync(new URL("revision.txt", destination), `${revision}\n`);
console.log(`기존 UI 비교 화면 준비 완료: ${revision}`);
