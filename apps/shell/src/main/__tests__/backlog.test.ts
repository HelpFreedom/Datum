/** Хвост вывода claude: переживает перезагрузку окна, не растёт без предела. */
import { describe, expect, it } from "vitest";

import { Backlog } from "../backlog";

describe("хвост вывода терминала", () => {
  it("копит вывод как есть", () => {
    const b = new Backlog(100);
    b.push("\x1b[2Jпривет\r\n");
    b.push("мир");
    expect(b.text()).toBe("\x1b[2Jпривет\r\nмир");
  });

  it("сверх предела режет старое по началу строки", () => {
    const b = new Backlog(20);
    b.push("строка-1\n\x1b[31mстрока-2\n");
    b.push("строка-3\n");
    const text = b.text();
    expect(text.length).toBeLessThanOrEqual(20);
    expect(text.endsWith("строка-3\n")).toBe(true);
    // Начало — целая строка, а не обрывок последовательности.
    expect(text.startsWith("строка") || text.startsWith("\x1b[")).toBe(true);
  });

  it("новый запуск claude — с чистого листа", () => {
    const b = new Backlog();
    b.push("старое");
    b.clear();
    expect(b.text()).toBe("");
  });
});
