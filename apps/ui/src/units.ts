/**
 * Единицы для показа.
 *
 * В проекте они хранятся как есть («mm», «deg»), потому что это данные, и
 * переписывать их в файле было бы неверно: их читают и скрипты, и агент.
 * Переводится только то, что видит человек.
 */
const SHOWN: Record<string, string> = {
  mm: "мм",
  mm2: "мм²",
  mm3: "мм³",
  deg: "°",
  rad: "рад",
  g: "г",
  kg: "кг",
  "g/cm3": "г/см³",
  "kg/m3": "кг/м³",
  n: "Н",
  mpa: "МПа",
  s: "с",
  mms: "мм/с",
};

/** Единицы-заглушки: у безразмерной величины сервер пишет «—» или «доля». */
const NONE = new Set(["", "—", "-", "–", "доля"]);

export function unit(value: string | undefined | null): string {
  if (!value || NONE.has(value.trim())) return "";
  return SHOWN[value.trim().toLowerCase()] ?? value;
}

/**
 * Запас до порога. Сервер отдаёт его ДОЛЕЙ порога — иначе мм³ и градусы не
 * сравнить при сортировке. Показывать его голым числом нельзя: «+0.50» при
 * пороге 0.4 мм человек прочтёт как полмиллиметра.
 */
export function margin(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  const percent = value * 100;
  const digits = Math.abs(percent) < 10 ? 1 : 0;
  const text = percent.toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${percent >= 0 ? "+" : ""}${text} %`;
}

/** Число по-русски с разумной точностью и единицей. */
export function amount(
  value: number | null | undefined,
  unitName?: string,
  digits = 3,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const text = value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const suffix = unit(unitName);
  return suffix ? `${text} ${suffix}` : text;
}
