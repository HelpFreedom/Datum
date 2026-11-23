/**
 * Правила окна, общие для кнопок, меню и клавиш. Держатся в сторе, а не в
 * каждом из них, — и проверяются здесь одним местом.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { useUi } from "../ui";

const initial = useUi.getState();
beforeEach(() => useUi.setState(initial, true));

describe("разнесение гасит замер и срез", () => {
  it("включение разнесения снимает инструмент и срез", () => {
    const ui = useUi.getState();
    ui.setTool("distance");
    ui.setPlane("xy", 5);
    ui.setSpread(0.5);
    const s = useUi.getState();
    expect(s.tool).toBe("none");
    expect(s.plane).toBeNull();
  });

  it("при разнесённом виде инструмент и срез не включаются", () => {
    const ui = useUi.getState();
    ui.setSpread(1);
    ui.setTool("probe");
    ui.setPlane("xz", 3);
    const s = useUi.getState();
    expect(s.tool).toBe("none");
    expect(s.plane).toBeNull();
  });

  it("вернули в сборку — инструменты снова доступны", () => {
    const ui = useUi.getState();
    ui.setSpread(1);
    ui.setSpread(0);
    ui.setTool("distance");
    expect(useUi.getState().tool).toBe("distance");
  });
});

describe("ход шарнира — как разнесение: гасит замер и срез", () => {
  it("сдвинутое звено снимает инструмент, срез и разнесение", () => {
    const ui = useUi.getState();
    ui.setTool("distance");
    ui.setPlane("xy", 5);
    ui.setSpread(0.4);
    ui.setJoint("крышка", 37);
    const s = useUi.getState();
    expect([s.tool, s.plane, s.spread]).toEqual(["none", null, 0]);
    expect(s.joints).toEqual({ крышка: 37 });
    ui.setTool("probe");
    expect(useUi.getState().tool).toBe("none");
  });

  it("разнесение возвращает звенья на места, и ноль — это «на месте»", () => {
    const ui = useUi.getState();
    ui.setJoint("крышка", 20);
    ui.setSpread(1);
    expect(useUi.getState().joints).toEqual({});
    ui.setSpread(0);
    ui.setJoint("крышка", 15);
    ui.setJoint("крышка", 0);
    expect(useUi.getState().joints).toEqual({});
    ui.setTool("distance");
    expect(useUi.getState().tool).toBe("distance");
  });
});

describe("только модель", () => {
  it("возвращает ровно то, что было открыто", () => {
    const ui = useUi.getState();
    ui.toggleRight();                          // правая закрыта ДО фокуса
    ui.toggleFocus();
    let s = useUi.getState();
    expect([s.leftOpen, s.rightOpen, s.dockOpen]).toEqual([false, false, false]);
    ui.toggleFocus();
    s = useUi.getState();
    expect([s.leftOpen, s.rightOpen, s.dockOpen]).toEqual([true, false, true]);
  });
});

describe("рейка", () => {
  it("повторное нажатие открытой вкладки сворачивает панель, другая — открывает", () => {
    const ui = useUi.getState();
    ui.showLeft("scene");
    expect(useUi.getState().leftOpen).toBe(false);
    ui.showLeft("models");
    expect(useUi.getState()).toMatchObject({ left: "models", leftOpen: true });
  });
});

describe("скрытые детали", () => {
  it("глаз переключает туда и обратно", () => {
    const ui = useUi.getState();
    ui.toggleHidden("крышка");
    expect(useUi.getState().hidden).toEqual(["крышка"]);
    ui.toggleHidden("крышка");
    expect(useUi.getState().hidden).toEqual([]);
  });
});

describe("размеры панелей", () => {
  it("тянутся сколько угодно: предел только снизу, чтобы было за что ухватить", async () => {
    const { DOCK_MIN, PANEL_MIN } = await import("../../chrome/layout");
    const ui = useUi.getState();
    ui.setLeftWidth(5000);
    ui.setRightWidth(10);
    ui.setDockHeight(3000);
    let s = useUi.getState();
    expect([s.leftWidth, s.rightWidth, s.dockHeight]).toEqual([5000, PANEL_MIN, 3000]);
    ui.setDockHeight(1);
    s = useUi.getState();
    expect(s.dockHeight).toBe(DOCK_MIN);
  });

  it("запоминается только заданное человеком, и запомненное ставится обратно", () => {
    const ui = useUi.getState();
    expect(ui.savedLayout()).toEqual({ dock: 250 });       // боковые — умолчание CSS
    ui.applyLayout({ left: 420, right: 610, dock: 333 });
    expect(useUi.getState().savedLayout()).toEqual({ left: 420, right: 610, dock: 333 });
    ui.setLeftWidth(null);
    expect(useUi.getState().savedLayout()).toEqual({ right: 610, dock: 333 });
  });

  it("испорченное в файле не ставится", () => {
    useUi.getState().applyLayout({ left: Number.NaN, right: -3, dock: "x" as unknown as number });
    const s = useUi.getState();
    expect([s.leftWidth, s.rightWidth, s.dockHeight]).toEqual([null, null, 250]);
  });
});
