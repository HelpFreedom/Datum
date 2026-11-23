import { defineConfig } from "vitest/config";

// Тесты берутся ТОЛЬКО из исходников. Без этого vitest подбирал ещё и копию,
// скомпилированную в dist/, и падал на ней: dist — CommonJS, а vitest из
// require() не импортируется. Заодно это значит, что тест всегда проверяет
// исходник, а не вчерашнюю сборку.
export default defineConfig({
  test: {
    root: __dirname,
    include: ["src/**/*.test.ts"],
  },
});
