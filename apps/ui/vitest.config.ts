import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Эти проверки — про арифметику, а не про DOM: браузерное окружение им не
    // нужно и только замедлило бы прогон.
    environment: "node",
    include: ["src/**/__tests__/**/*.test.ts"],
  },
});
