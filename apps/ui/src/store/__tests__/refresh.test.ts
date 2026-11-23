/**
 * Правка из окна прогоняет проверки ОДИН раз, а не два.
 *
 * Найдено обзором: окно обновлялось само после своей правки, а следом сервер
 * присылал `doc.changed` про ту же правку — второй полный прогон проверок.
 * Теперь `doc.changed` сверяет дешёвый отпечаток файлов (`project_info.stamp`):
 * тот же — обновлять нечего. Изменились правда (кто-то снаружи) — обновляет.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];
let stamp = "s1";

vi.mock("../../transport/client", () => ({
  listJobs: async () => [],
  runOp: async (name: string) => {
    calls.push(name);
    await new Promise((r) => setTimeout(r, name === "part_build" ? 20 : 5));
    const results: Record<string, unknown> = {
      project_info: { name: "п", parts: [], checks: 1, stamp },
      params_list: { summary: {}, params: {} },
      part_build: { ok: true, parts: {}, report: "" },
      check_run: { ok: true, n_pass: 0, n_fail: 0, n_warn: 0, results: [], report: "" },
    };
    return { job: "j", status: "done", result: results[name] ?? {} };
  },
}));
vi.mock("../../transport/endpoint", () => ({ chooseArchivePath: async () => null }));

const settle = () => new Promise((r) => setTimeout(r, 450));

describe("обновление окна", () => {
  beforeEach(() => {
    calls.length = 0;
    stamp = "s1";
  });

  it("своя правка и её doc.changed — один прогон проверок", async () => {
    const { useDoc } = await import("../doc");
    expect(await useDoc.getState().setParam("W", 3)).toBe(true);
    useDoc.getState().applyEvent({ seq: 7, t: "doc.changed", paths: ["params/base.yaml"] });
    await settle();
    expect(calls.filter((c) => c === "check_run")).toHaveLength(1);
  });

  it("файлы изменил кто-то снаружи — окно обновляется и перепроверяет", async () => {
    const { useDoc } = await import("../doc");
    await useDoc.getState().refreshAll();
    calls.length = 0;
    stamp = "s2";                                  // агент правил файл своим Edit
    useDoc.getState().applyEvent({ seq: 8, t: "doc.changed", paths: ["parts/p.py"] });
    await settle();
    expect(calls.filter((c) => c === "check_run")).toHaveLength(1);
  });

  it("вызовы во время идущего обновления сливаются в один дополнительный круг", async () => {
    const { useDoc } = await import("../doc");
    const doc = useDoc.getState();
    await Promise.all([doc.refreshAll(), doc.refreshAll(), doc.refreshAll()]);
    expect(calls.filter((c) => c === "check_run")).toHaveLength(2);
  });
});
