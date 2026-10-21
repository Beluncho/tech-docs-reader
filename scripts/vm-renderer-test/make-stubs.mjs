// Обновить тестовые копии модулей из frontend/js/visual-mapper (заменить импорт fabric на заглушку).
// Запуск: node make-stubs.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "frontend", "js", "visual-mapper");
const FABRIC = '"../../vendor/fabric-6.7.1.min.mjs"';
const STUB = '"./fabric-stub.mjs"';
// модули без импорта fabric (roles, table-ops, preview) копируются как есть
const NO_FABRIC = new Set(["roles.js", "table-ops.js", "preview.js"]);
for (const f of ["table-renderer.js", "canvas.js", "roles.js", "table-ops.js", "preview.js"]) {
  const code = readFileSync(join(src, f), "utf8");
  if (!code.includes(FABRIC) && !NO_FABRIC.has(f)) {
    console.error(`WARN: ${f}: импорт fabric не найден — проверь путь`);
  }
  writeFileSync(join(here, f), code.split(FABRIC).join(STUB));
  console.log("ok:", f);
}
