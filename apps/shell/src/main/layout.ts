/**
 * Размеры панелей окна — между запусками.
 *
 * Раскладка — привычка человека, а не свойство проекта (решение владельца):
 * один файл на все проекты, `$XDG_CONFIG_HOME/datum/layout.json`. Хранит её
 * оболочка, а не `localStorage` окна: окно живёт на происхождении сервера, у
 * которого порт каждый запуск новый, и хранилище страницы было бы каждый раз
 * пустым.
 *
 * Файл читается как чужой: в нём только числа в разумных пределах, всё прочее
 * отбрасывается. Под сценарием проверки (`DATUM_SCENARIO`) файл не читается и
 * не пишется: снимки не должны зависеть от того, как человек разложил себе
 * окно, а прогон `make ui` — менять ему раскладку (то же правило, что с
 * масштабом страницы). Модуль без импорта electron — его берёт тест.
 */
import * as fs from "node:fs";
import * as path from "node:path";

/** Что помнится: ширины боковых панелей и высота нижней, в пикселях CSS. */
export interface SavedLayout {
  left?: number;
  right?: number;
  dock?: number;
}

const KEYS = ["left", "right", "dock"] as const;
/** Больше любого экрана: всё, что сверх, — не размер, а мусор в файле. */
const MAX_PX = 20_000;

export function layoutFile(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.XDG_CONFIG_HOME || path.join(env.HOME || "", ".config");
  return path.join(base, "datum", "layout.json");
}

/** Только известные ключи с конечными положительными числами. */
export function sanitizeLayout(data: unknown): SavedLayout {
  const out: SavedLayout = {};
  if (!data || typeof data !== "object") return out;
  for (const key of KEYS) {
    const value = (data as Record<string, unknown>)[key];
    if (typeof value === "number" && Number.isFinite(value) && value > 0 && value <= MAX_PX) {
      out[key] = Math.round(value);
    }
  }
  return out;
}

export function loadLayout(env: NodeJS.ProcessEnv = process.env): SavedLayout {
  if (env.DATUM_SCENARIO) return {};
  try {
    return sanitizeLayout(JSON.parse(fs.readFileSync(layoutFile(env), "utf-8")));
  } catch {
    return {};                    // нет файла или он испорчен — умолчания окна
  }
}

/** Записать атомарно: оборванная запись не должна оставить половину файла. */
export function saveLayout(data: unknown, env: NodeJS.ProcessEnv = process.env): void {
  if (env.DATUM_SCENARIO) return;
  const file = layoutFile(env);
  const clean = sanitizeLayout(data);
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(clean, null, 2) + "\n", "utf-8");
    fs.renameSync(tmp, file);
  } catch (e) {
    // Не записалось — окно работает дальше, просто в следующий раз откроется
    // с умолчаниями. Причина — в журнал.
    console.error(`  раскладка не сохранилась: ${e instanceof Error ? e.message : String(e)}`);
  }
}
