/**
 * Сколько раз подряд можно поднимать упавшее заново.
 *
 * Общий счёт для того, что оболочка чинит сама (сервер проекта): несколько
 * попыток за окно времени, потом — отказ словами. Без предела упавший на
 * старте сервер перезапускался бы без конца. Модуль без electron — его берёт
 * тест.
 */
export class RestartBudget {
  private times: number[] = [];

  constructor(private readonly max: number, private readonly windowMs: number) {}

  /** Можно ли ещё раз; если да — попытка засчитана. */
  allow(now: number = Date.now()): boolean {
    this.times = this.times.filter((t) => now - t < this.windowMs);
    if (this.times.length >= this.max) return false;
    this.times.push(now);
    return true;
  }

  get count(): number {
    return this.times.length;
  }

  reset(): void {
    this.times = [];
  }
}
