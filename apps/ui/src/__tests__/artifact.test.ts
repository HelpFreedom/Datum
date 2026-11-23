/**
 * Пул объектных адресов картинок-доказательств: освобождает то, что не
 * показывают (прежде `revokeObjectURL` не вызывался никогда — найдено обзором).
 */
import { describe, expect, it } from "vitest";

import { ObjectUrlPool } from "../transport/artifact";

describe("пул адресов картинок", () => {
  it("непоказываемое сверх запаса освобождается, давнее — первым", () => {
    const revoked: string[] = [];
    const pool = new ObjectUrlPool((href) => revoked.push(href), 2);
    for (const n of [1, 2, 3]) {
      pool.add(`/obj/${n}.png`, `blob:${n}`);
      pool.release(`/obj/${n}.png`);
    }
    expect(revoked).toEqual(["blob:1"]);
    expect(pool.size).toEqual({ live: 0, idle: 2 });
  });

  it("показываемое не освобождается, повторное открытие — из запаса", () => {
    const revoked: string[] = [];
    const pool = new ObjectUrlPool((href) => revoked.push(href), 0);
    pool.add("/obj/a.png", "blob:a");
    expect(pool.acquire("/obj/a.png")).toBe("blob:a");      // второй зритель
    pool.release("/obj/a.png");
    expect(revoked).toEqual([]);                             // первый ещё смотрит
    pool.release("/obj/a.png");
    expect(revoked).toEqual(["blob:a"]);                     // запас 0 — сразу
    expect(pool.acquire("/obj/a.png")).toBeNull();
  });

  it("две загрузки одной картинки: лишний адрес освобождается сразу", () => {
    const revoked: string[] = [];
    const pool = new ObjectUrlPool((href) => revoked.push(href));
    expect(pool.add("/obj/b.png", "blob:b1")).toBe("blob:b1");
    expect(pool.add("/obj/b.png", "blob:b2")).toBe("blob:b1");
    expect(revoked).toEqual(["blob:b2"]);
  });
});
