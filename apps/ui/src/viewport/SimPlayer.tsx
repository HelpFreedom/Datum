/**
 * Проигрыватель симуляции поверх вьюпорта: уведомление о готовом прогоне,
 * пуск, время, моменты суставов против предела привода и вердикт проверок.
 *
 * Сцена двигает звенья по кадрам (`store/sim` → `viewport.poseSim`); здесь —
 * только управление и графики. Графики рисуются на canvas сами: тянуть ради
 * трёх кривых библиотеку графиков незачем.
 */
import { Pause, Play, X } from "lucide-react";
import { useEffect, useRef } from "react";

import { useSim } from "../store/sim";
import { duration, heatColor, loadAt, valueAt, type SimPlayback } from "./sim";

const SPEEDS = [0.25, 0.5, 1, 2];

function Chart({ p, k, t }: { p: SimPlayback; k: number; t: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const ts = p.series.t;
    const req = p.series.required[k] ?? [];
    const app = p.series.applied?.[k] ?? null;
    const lim = p.series.limits[k];
    // Циклом, а не разворотом массива в аргументы: `Math.max(...ряд)` на
    // длинной записи (десятки тысяч отсчётов) бросает RangeError.
    let peak = Math.max(1e-9, lim ?? 0);
    for (const v of req) peak = Math.max(peak, Math.abs(v));
    for (const v of app ?? []) peak = Math.max(peak, Math.abs(v));
    const end = duration(p) || 1;
    const x = (tt: number) => (tt / end) * w;
    const y = (v: number) => h / 2 - (v / peak) * (h / 2 - 4);
    ctx.strokeStyle = "rgba(125,138,158,0.35)";
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    if (lim != null) {
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "#ff5d6c";
      for (const v of [lim, -lim]) {
        ctx.beginPath();
        ctx.moveTo(0, y(v));
        ctx.lineTo(w, y(v));
        ctx.stroke();
      }
      ctx.setLineDash([]);
    }
    const line = (vals: number[], colour: string, width: number) => {
      ctx.strokeStyle = colour;
      ctx.lineWidth = width;
      ctx.beginPath();
      vals.forEach((v, i) => (i ? ctx.lineTo(x(ts[i]), y(v)) : ctx.moveTo(x(ts[i]), y(v))));
      ctx.stroke();
      ctx.lineWidth = 1;
    };
    line(req, "#7aa2f7", 2);
    if (app) line(app, "#9ece6a", 1);
    ctx.strokeStyle = "#f3f6fb";
    ctx.beginPath();
    ctx.moveTo(x(t), 0);
    ctx.lineTo(x(t), h);
    ctx.stroke();
  }, [p, k, t]);
  const now = valueAt(p.series.t, p.series.required[k] ?? [0], t);
  const lim = p.series.limits[k];
  const unit = p.series.units[k] ?? "";
  return (
    <div className="sim-chart">
      <div className="sim-chart-label">
        <span>{p.series.joints[k] ?? p.joints[k]}</span>
        <span className="mono">
          {now.toFixed(3)} {unit}
          {lim != null && <span className="dim"> / предел {lim} {unit}</span>}
        </span>
      </div>
      <canvas ref={ref} />
    </div>
  );
}

export function SimPlayer() {
  const notice = useSim((s) => s.notice);
  const p = useSim((s) => s.playback);
  const t = useSim((s) => s.t);
  const playing = useSim((s) => s.playing);
  const speed = useSim((s) => s.speed);
  const loading = useSim((s) => s.loading);
  const error = useSim((s) => s.error);
  const heat = useSim((s) => s.heat);
  const running = useSim((s) => s.running);
  const stressShown = useSim((s) => s.stressShown);
  const stressRunning = useSim((s) => s.stressRunning);
  const { open, close, dismiss, seek, setPlaying, setSpeed, setHeat, showStress } = useSim.getState();

  useEffect(() => {
    if (!playing || !p) return;
    let last = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const state = useSim.getState();
      const next = state.t + ((now - last) / 1000) * state.speed;
      last = now;
      if (next >= duration(p)) {
        state.seek(duration(p));
        state.setPlaying(false);
        return;
      }
      state.seek(next);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, p]);

  if (!p) {
    if (running) {
      return (
        <div className="vp-bar sim-notice" data-sim="running">
          <span className="dim">Считаю симуляцию {running}…</span>
        </div>
      );
    }
    if (!notice && !error) return null;
    return (
      <div className="vp-bar sim-notice" data-sim="notice">
        {notice && (
          <>
            <span className={notice.ok ? "good" : "bad"}>●</span>
            <span>
              Симуляция «{notice.scenario}» механизма {notice.mechanism} готова
            </span>
            <button data-action="sim-open" disabled={loading} onClick={() => void open()}>
              {loading ? "Загружаю…" : "Показать"}
            </button>
          </>
        )}
        {error && <span className="bad">не показать: {error}</span>}
        <button className="icon-button" aria-label="Скрыть" title="Скрыть" onClick={dismiss}>
          <X size={14} />
        </button>
      </div>
    );
  }

  const end = duration(p);
  const failed = p.verdict.filter((v) => v.status === "fail" || v.status === "error");
  const now = loadAt(p, t);
  let hot = -1;
  if (now) now.forEach((u, k) => (hot = hot < 0 || u > now[hot] ? k : hot));
  return (
    <div className="vp-bar sim-player" data-sim="player">
      <div className="sim-head">
        <strong>
          {p.mechanism} «{p.scenario}»
        </strong>
        <span className={failed.length ? "bad" : "good"}>
          {failed.length ? `не прошло: ${failed.length}` : "все проверки прошли"}
        </span>
        <button className="icon-button" data-action="sim-close" aria-label="Закрыть симуляцию"
          title="Закрыть симуляцию — вернуть детали" onClick={close}>
          <X size={16} />
        </button>
      </div>
      <div className="sim-transport">
        <button className="icon-button" data-action="sim-play" aria-label={playing ? "Пауза" : "Пуск"}
          onClick={() => setPlaying(!playing)}>
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <input className="slider sim-slider" type="range" min={0} max={end} step={end / 1000 || 0.001}
          value={t} aria-label="Время" onChange={(e) => seek(Number(e.target.value))} />
        <span className="mono sim-time">
          {t.toFixed(2)} / {end.toFixed(2)} с
        </span>
        <button className="sim-heat-toggle" data-action="sim-heat" aria-pressed={heat}
          disabled={!p.load} title={p.load ? "Карта загрузки звеньев: доля предела привода и опоры"
            : "в записи нет карты загрузки"} onClick={() => setHeat(!heat)}>
          Карта загрузки
        </button>
        <div className="segmented" role="group" aria-label="Скорость">
          {SPEEDS.map((s) => (
            <button key={s} aria-pressed={speed === s} onClick={() => setSpeed(s)}>
              {s}×
            </button>
          ))}
        </div>
      </div>
      {heat && now && (
        <div className="sim-legend" data-sim="legend">
          <span className="sim-scale" />
          <span className="dim">0 %</span>
          <span className="dim">50 %</span>
          <span className="dim">80 %</span>
          <span className="dim">предел</span>
          {hot >= 0 && (
            <span className="sim-hot">
              сейчас больше всех: {p.links[hot].name} —{" "}
              <b style={{ color: `rgb(${heatColor(now[hot]).join(",")})` }}>
                {Math.round(now[hot] * 100)} %
              </b>
            </span>
          )}
        </div>
      )}
      {error && (
        <div className="sim-stress bad" data-sim="error">
          {error}
        </div>
      )}
      {stressRunning && p.stress.length === 0 && (
        <div className="sim-stress" data-sim="stress-running">
          <span className="dim">Считаю напряжения звеньев (МКЭ) в их худший момент — это минуты;
            движение и карта загрузки уже здесь…</span>
        </div>
      )}
      {p.stress.length > 0 && (
        <div className="sim-stress" data-sim="stress">
          <span className="dim">Напряжения (МКЭ) в худший момент:</span>
          {p.stress.map((st) => (
            <button key={st.link} data-sim-stress={st.link} aria-pressed={stressShown === st.link}
              title={`${st.link}: Мизес ${st.valueMpa.toFixed(2)} МПа (${st.basis}) в t = ${st.t.toFixed(2)} с`
                + (st.strengthMpa ? ` — предел между слоями ${st.strengthMpa} МПа` : " — предела материала нет")}
              onClick={() => void showStress(stressShown === st.link ? null : st.link)}>
              {st.link}{" "}
              <b style={{ color: st.u === null ? undefined : `rgb(${heatColor(st.u).join(",")})` }}>
                {st.u === null ? `${st.valueMpa.toFixed(1)} МПа` : `${Math.round(st.u * 100)} %`}
              </b>
            </button>
          ))}
          {stressShown && (() => {
            const st = p.stress.find((s) => s.link === stressShown)!;
            return (
              <span className="dim" data-sim="stress-note">
                цвет — от свободного (зелёный) до самого нагруженного места звена (красный):{" "}
                {st.valueMpa.toFixed(2)} МПа
                {st.strengthMpa && st.u !== null
                  ? ` = ${Math.round(st.u * 100)} % предела между слоями ${st.strengthMpa} МПа`
                  : " (предела материала нет)"}
                ; поле верно в t = {st.t.toFixed(2)} с
              </span>
            );
          })()}
        </div>
      )}
      <div className="sim-charts">
        {p.series.required.map((_, k) => (
          <Chart key={k} p={p} k={k} t={t} />
        ))}
      </div>
      {failed.length > 0 && (
        <ul className="sim-verdict">
          {failed.map((v) => (
            <li key={v.id} className="bad">
              {v.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
