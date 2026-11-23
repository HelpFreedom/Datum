/**
 * Окно сообщает серверу, что видит человек: после затишья, без повторов, и
 * дрожь орбиты не считается переменой.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { normalise, readoutOf, type WindowState, WindowReporter } from "../report";

const base: WindowState = {
  selected: "кубик",
  parts: ["кубик"],
  hidden: [],
  ghosts: [],
  tool: "none",
  section: null,
  spread: 0,
  camera: { position: [300.04, -200, 250], target: [128, 128, 10], up: [0, 0, 1], fov: 45 },
  readout: null,
  picked: [],
};

describe("окно сообщает, что показывает", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("шлёт после затишья и только последнее", () => {
    const send = vi.fn(() => Promise.resolve());
    const r = new WindowReporter(send, 400);
    for (let i = 0; i < 10; i += 1) {
      r.update({ ...base, camera: { ...base.camera!, position: [300 + i * 10, -200, 250] } });
      vi.advanceTimersByTime(50);                          // крутят камеру
    }
    expect(send).not.toHaveBeenCalled();
    vi.advanceTimersByTime(400);
    expect(send).toHaveBeenCalledTimes(1);
    expect((send.mock.calls[0] as unknown as [WindowState])[0].camera!.position).toEqual([390, -200, 250]);
  });

  it("то же самое второй раз не шлёт — и дрожь в сотых миллиметра тоже", () => {
    const send = vi.fn(() => Promise.resolve());
    const r = new WindowReporter(send, 400);
    r.update(base);
    vi.advanceTimersByTime(500);
    r.update({ ...base, camera: { ...base.camera!, position: [300.01, -200.02, 250] } });
    vi.advanceTimersByTime(500);
    expect(send).toHaveBeenCalledTimes(1);
    r.update({ ...base, selected: "крышка" });
    vi.advanceTimersByTime(500);
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("не дошло — та же картина уйдёт снова при следующей перемене", async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error("сервер занят")).mockResolvedValue(undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const r = new WindowReporter(send, 400);
    r.update(base);
    vi.advanceTimersByTime(500);
    await Promise.resolve();
    await Promise.resolve();
    r.update(base);
    vi.advanceTimersByTime(500);
    expect(send).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });
});

describe("что уходит на сервер", () => {
  it("числа округлены, минус-ноль — ноль", () => {
    const n = normalise({ ...base, camera: { position: [-0.00001, 1.26, 2], target: [0, 0, 0], up: [0, 0, 1], fov: 45 } });
    expect(n.camera!.position).toEqual([0, 1.3, 2]);
    expect(Object.is(n.camera!.position[0], -0)).toBe(false);
  });

  it("карточку перемещения замером не считает; у штангенциркуля — и выбранное", () => {
    expect(readoutOf({ tool: "move", text: "x", detail: "" })).toEqual({ readout: null, picked: [] });
    const picked = [{ type: "point" as const, kind: "вершина", part: "кубик", point: [1, 2, 3] }];
    expect(readoutOf({ tool: "distance", text: "5 мм", detail: "д", picked })).toEqual({
      readout: { tool: "distance", text: "5 мм", detail: "д" },
      picked,
    });
  });

  it("показанный ход шарнира уходит агенту округлённым", () => {
    const n = normalise({ ...base, joints: { крышка: 37.123456 } });
    expect(n.joints).toEqual({ крышка: 37.12 });
    expect("joints" in normalise(base)).toBe(false);
  });
});
