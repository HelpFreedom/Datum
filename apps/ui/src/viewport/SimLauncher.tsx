/**
 * Кнопка «Симуляция» во вьюпорте: человек сам запускает механизм в работе.
 *
 * Список механизмов и сценариев — операцией `sim_list`, прогон — `sim_run` на
 * сервере (тот же, что зовёт агент); готово — проигрыватель открывается сразу,
 * с картой загрузки. Считает сервер: окно лишь показывает (правило проекта —
 * числа окна и проверок одни).
 */
import { Activity } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useDoc } from "../store/doc";
import { useSim } from "../store/sim";
import { runOp } from "../transport/client";
import { parseStress, stressProblems } from "./sim";

interface MechInfo {
  name: string;
  scenarios: { name: string }[];
}

export function SimLauncher() {
  const project = useDoc((s) => s.project);
  const running = useSim((s) => s.running);
  // МКЭ звеньев прошлого прогона ещё идёт — новый прогон не начинаем: его
  // индикатор погасил бы `finally` первого, и два расчёта шли бы разом.
  const stressRunning = useSim((s) => s.stressRunning);
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<MechInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const answer = await runOp<{ mechanisms?: MechInfo[]; error?: string }>("sim_list", { project });
      if (answer.result.error) throw new Error(answer.result.error);
      setList(answer.result.mechanisms ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const run = async (mechanism: string, scenario: string) => {
    setOpen(false);
    const sim = useSim.getState();
    sim.setRunning(`${mechanism} «${scenario}»`);
    try {
      const answer = await runOp<{ error?: string; ok?: boolean; playback?: { url: string } }>(
        "sim_run", { project, mechanism, scenario });
      const r = answer.result;
      if (r.error || !r.playback) throw new Error(r.error ?? "прогон не дал записи");
      sim.announce({ url: r.playback.url, mechanism, scenario, ok: Boolean(r.ok) });
      await useSim.getState().open();
    } catch (e) {
      useSim.setState({ error: e instanceof Error ? e.message : String(e) });
      return;
    } finally {
      useSim.getState().setRunning(null);
    }
    // Второй шаг — МКЭ звеньев в их худший момент: минуты, поэтому движение и карта
    // загрузки показаны сразу, а кнопки напряжений появляются, когда досчитано.
    const shown = useSim.getState().playback;
    if (!shown) return;
    useSim.setState({ stressRunning: true });
    try {
      const answer = await runOp<{
        error?: string;
        playback_stress?: unknown[];
        checks?: { status?: string; message?: string }[];
      }>("sim_stress", { project, mechanism, scenario, links: "all" });
      const r = answer.result;
      if (r.error) throw new Error(r.error);
      // те же правила, что у записи: свои звенья, сетки только с нашего сервера
      const stress = parseStress(r.playback_stress, shown.links.map((l) => l.name));
      useSim.getState().addStress(shown, stress);
      // Причину говорит сервер (нет материала, нет печатных звеньев, отказ звена) —
      // окно её показывает, а не угадывает одной фразой на все случаи.
      const why = stressProblems(r.checks);
      if (why) useSim.setState({ error: `напряжения: ${why}` });
      else if (!stress.length) useSim.setState({ error: "напряжения не посчитались, а причины сервер не назвал" });
    } catch (e) {
      if (useSim.getState().playback === shown)
        useSim.setState({ error: `напряжения: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      useSim.setState({ stressRunning: false });
    }
  };

  return (
    <div className="sim-launch" ref={box}>
      <button
        className="icon-button"
        data-action="sim-launch"
        title={stressRunning
          ? "Считаются напряжения звеньев прошлого прогона — дождитесь или отмените в ленте"
          : "Симуляция: механизм в работе с картой загрузки"}
        aria-label="Симуляция"
        aria-pressed={open}
        disabled={Boolean(running) || Boolean(stressRunning)}
        onClick={() => {
          setOpen(!open);
          if (!open) void load();
        }}
      >
        <Activity size={16} />
      </button>
      {open && (
        <div className="sim-menu" role="menu" data-sim="menu">
          {loading && <div className="dim">Читаю механизмы проекта…</div>}
          {error && <div className="bad">{error}</div>}
          {list && list.length === 0 && (
            <div className="dim">
              В проекте нет механизма. Попросите агента: «объяви механизм и сценарии» — он сделает
              @mechanism по справочнику навыка.
            </div>
          )}
          {list?.map((m) => (
            <div key={m.name} className="sim-menu-group">
              <div className="sim-menu-title">{m.name}</div>
              {m.scenarios.map((s) => (
                <button key={s.name} role="menuitem" data-sim-scenario={s.name}
                  onClick={() => void run(m.name, s.name)}>
                  ▶ {s.name}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
