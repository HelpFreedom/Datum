/**
 * Подпись «контекст агента» в строке состояния. Отдельно от разметки — её
 * берёт тест.
 *
 * Зачем число на виду (аудит 2026-11-21): каждый ход агента перечитывает весь
 * контекст, и 71 % расхода сессий — именно это. Чтение из кеша стоит десятую
 * долю свежего входного токена, отсюда «ход ≈ N свежих».
 */

/** Доля цены свежего входного токена, которую стоит токен, читаемый из кеша. */
export const CACHED_SHARE = 0.1;
/** С этого размера строка желтеет: пора записать решения и начать заново. */
export const CONTEXT_WARN = 300_000;

/** «84 тыс.», «420 тыс.», «1.2 млн». */
export function tokensText(tokens: number): string {
  if (!(tokens > 0)) return "0";
  if (tokens < 1000) return String(Math.round(tokens));
  if (tokens < 1_000_000) return `${Math.round(tokens / 1000)} тыс.`;
  return `${(tokens / 1_000_000).toFixed(1)} млн`;
}

export function contextLabel(tokens: number): { text: string; hint: string; warn: boolean } {
  const warn = tokens >= CONTEXT_WARN;
  return {
    text: `контекст ${tokensText(tokens)}`,
    hint:
      `В разговоре агента ${tokensText(tokens)} токенов. Каждый его ход перечитывает их все: `
      + `ход стоит как ${tokensText(tokens * CACHED_SHARE)} свежих входных токенов, что бы он ни делал.`
      + (warn
        ? " Закончили этап — попросите агента записать решения (журнал проекта) и наберите /clear: "
          + "сводка проекта вернётся в новый разговор сама."
        : ""),
    warn,
  };
}
