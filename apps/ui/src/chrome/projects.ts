/**
 * Подписи диалога «Проекты…»: когда правили и сколько весит кеш. Отдельно от
 * разметки — так их берёт тест.
 */

/** «сегодня 14:05», «вчера 09:30», «3 дня назад», «12.10.2026». */
export function ago(modified: number, now: number = Date.now()): string {
  if (!modified) return "не правился";
  const then = new Date(modified);
  const today = new Date(now);
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(today) - day(then)) / 86_400_000);
  const clock = `${String(then.getHours()).padStart(2, "0")}:${String(then.getMinutes()).padStart(2, "0")}`;
  if (days <= 0) return `сегодня ${clock}`;
  if (days === 1) return `вчера ${clock}`;
  if (days < 7) return `${days} ${days < 5 ? "дня" : "дней"} назад`;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(then.getDate())}.${p(then.getMonth() + 1)}.${then.getFullYear()}`;
}

/** «0», «412 КБ», «38 МБ», «1.4 ГБ». */
export function bytesText(bytes: number): string {
  if (!(bytes > 0)) return "0";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  if (bytes < 1024 ** 3) return `${Math.round(bytes / 1024 / 1024)} МБ`;
  return `${(bytes / 1024 ** 3).toFixed(1)} ГБ`;
}

/** «1 деталь», «3 детали», «18 деталей», «без деталей». */
export function partsText(count: number): string {
  if (!count) return "без деталей";
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} деталей`;
  if (ones === 1) return `${count} деталь`;
  if (ones >= 2 && ones <= 4) return `${count} детали`;
  return `${count} деталей`;
}
