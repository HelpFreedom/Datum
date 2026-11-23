/**
 * Проигрыватель симуляции: объявление о готовом прогоне, загрузка записи,
 * время и пуск. Своим маленьким стором: это не документ (сервер его не
 * хранит) и не раскладка окна.
 *
 * Прогон запускает агент (`sim_run`); сервер присылает событие `sim.result`
 * со ссылкой на запись, окно показывает «Симуляция готова — Показать». Само
 * не открывается: человек может быть посреди замера.
 */
import { create } from "zustand";

import { endpoint } from "../transport/endpoint";
import { viewport } from "../viewport/control";
import { duration, loadAt, parsePlayback, posesAt, type SimNotice, type SimPlayback } from "../viewport/sim";
import { useUi } from "./ui";

interface SimState {
  notice: SimNotice | null;
  playback: SimPlayback | null;
  t: number;
  playing: boolean;
  /** Множитель скорости проигрывания. */
  speed: number;
  loading: boolean;
  error: string | null;
  /** Карта загрузки звеньев поверх цветов (по умолчанию — включена). */
  heat: boolean;
  /** Идёт ли расчёт прогона, запущенный кнопкой окна. */
  running: string | null;
  /** Звено, на котором показано поле МКЭ (оно верно только в свой момент). */
  stressShown: string | null;
  showStress: (link: string | null) => Promise<void>;
  /** Идёт ли расчёт напряжений (МКЭ) для открытой записи. */
  stressRunning: boolean;
  /** Досчитанные напряжения — к открытой записи, если это всё ещё она. */
  addStress: (playback: SimPlayback, stress: SimPlayback["stress"]) => void;
  announce: (notice: SimNotice) => void;
  setHeat: (on: boolean) => void;
  setRunning: (what: string | null) => void;
  dismiss: () => void;
  open: () => Promise<void>;
  close: () => void;
  seek: (t: number) => void;
  setPlaying: (playing: boolean) => void;
  setSpeed: (speed: number) => void;
}

/** Номер открытия: поздний ответ прежней загрузки не ложится поверх новой. */
let ticket = 0;

export const useSim = create<SimState>((set, get) => ({
  notice: null,
  playback: null,
  t: 0,
  playing: false,
  speed: 1,
  loading: false,
  error: null,
  heat: true,
  running: null,
  stressShown: null,
  stressRunning: false,

  announce: (notice) => set({ notice, error: null }),
  setRunning: (running) => set({ running }),
  setHeat: (heat) => {
    set({ heat });
    const p = get().playback;
    if (p) viewport.loadSim(heat ? loadAt(p, get().t) : null);
  },
  dismiss: () => set({ notice: null }),

  addStress: (playback, stress) => {
    if (get().playback !== playback) return;              // закрыли или открыли другую
    set({ playback: { ...playback, stress } });
  },

  showStress: async (link) => {
    const p = get().playback;
    if (!p || link === null) {
      set({ stressShown: null });
      await viewport.stressSim(null, null);
      return;
    }
    const entry = p.stress.find((s) => s.link === link);
    const index = p.links.findIndex((l) => l.name === link);
    if (!entry || index < 0) return;
    // Поле верно в свой момент: туда и встаём, проигрывание — на паузу.
    set({ playing: false });
    get().seek(entry.t);
    const shown = await viewport.stressSim(index, entry.url);
    if (get().playback !== p) return;
    if (shown) set({ stressShown: link, error: null });
    else set({ stressShown: null, error: "сетка напряжений не загрузилась" });
  },

  open: async () => {
    const notice = get().notice;
    if (!notice) return;
    const mine = ++ticket;
    set({ loading: true, error: null });
    try {
      const { token } = await endpoint();
      const response = await fetch(notice.url, { headers: { "X-Datum-Token": token } });
      if (!response.ok) throw new Error(`сервер ответил ${response.status}`);
      const playback = parsePlayback(await response.json());
      if (!playback) throw new Error("запись прогона не той формы");
      if (mine !== ticket) return;
      // Механизм на экране — не проект: замер, срез и разнесение о деталях.
      const ui = useUi.getState();
      ui.setTool("none");
      ui.setPlane(null);
      ui.setSpread(0);
      ui.resetJoints();
      const shown = await viewport.showSim(playback.links);
      if (mine !== ticket) return;
      if (!shown) throw new Error("сетки звеньев не загрузились");
      viewport.poseSim(posesAt(playback, 0));
      viewport.loadSim(get().heat ? loadAt(playback, 0) : null);
      set({ playback, t: 0, playing: true, loading: false, stressShown: null });
    } catch (e) {
      if (mine === ticket) set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  close: () => {
    ticket++;
    viewport.hideSim();
    set({ playback: null, playing: false, t: 0, loading: false, stressShown: null });
  },

  seek: (t) => {
    const p = get().playback;
    if (!p) return;
    const clamped = Math.min(Math.max(t, 0), duration(p));
    viewport.poseSim(posesAt(p, clamped));
    if (get().heat) viewport.loadSim(loadAt(p, clamped));
    set({ t: clamped });
  },

  setPlaying: (playing) => {
    const p = get().playback;
    if (playing && p && get().t >= duration(p)) get().seek(0);   // с начала, если доиграло
    if (playing && get().stressShown) void get().showStress(null); // поле — про один момент
    set({ playing });
  },
  setSpeed: (speed) => set({ speed }),
}));
