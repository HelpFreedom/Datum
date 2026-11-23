/**
 * Картинка-доказательство в окне.
 *
 * `<img src="/obj/...">` здесь не работает, и это не досадная мелочь: браузер
 * не даёт приложить к загрузке картинки свои заголовки, а `/obj` охраняется
 * токеном наравне с `/api`. Открыть его без токена значило бы ослабить охрану
 * ради удобства показа.
 *
 * Поэтому файл забирается обычным запросом — с токеном, как всё остальное — и
 * превращается в объектный адрес. Адрес ОСВОБОЖДАЕТСЯ: прежде комментарий это
 * обещал, а код — нет (`cache.set` шёл раньше проверки, и `revokeObjectURL` не
 * вызывался никогда), так что каждая открытая картинка жила в памяти вкладки
 * до конца сессии (найдено обзором; процесс окна однажды уже убивала
 * нехватка памяти). Теперь — пул со счётчиком: пока картинку показывают,
 * адрес жив; не показывают — лежит среди последних IDLE_KEEP (повторное
 * открытие без нового запроса), а старше — освобождается.
 */
import { useEffect, useState } from "react";

import { endpoint } from "./endpoint";

/** Сколько непоказываемых картинок держать наготове. */
export const IDLE_KEEP = 24;

/** Пул объектных адресов со счётчиком пользователей. */
export class ObjectUrlPool {
  private readonly live = new Map<string, { href: string; users: number }>();
  /** Непоказываемые — в порядке ухода (первый — давнее всех). */
  private readonly idle = new Map<string, string>();

  constructor(
    private readonly revoke: (href: string) => void,
    private readonly keep = IDLE_KEEP,
  ) {}

  /** Взять готовый адрес (и стать его пользователем) — или null. */
  acquire(url: string): string | null {
    const used = this.live.get(url);
    if (used) {
      used.users += 1;
      return used.href;
    }
    const resting = this.idle.get(url);
    if (resting === undefined) return null;
    this.idle.delete(url);
    this.live.set(url, { href: resting, users: 1 });
    return resting;
  }

  /** Положить только что созданный адрес; пользователь — вызывающий. */
  add(url: string, href: string): string {
    const known = this.acquire(url);
    if (known !== null) {
      this.revoke(href);            // гонка двух загрузок одной картинки: лишний — прочь
      return known;
    }
    this.live.set(url, { href, users: 1 });
    return href;
  }

  release(url: string): void {
    const used = this.live.get(url);
    if (!used) return;
    used.users -= 1;
    if (used.users > 0) return;
    this.live.delete(url);
    this.idle.set(url, used.href);
    while (this.idle.size > this.keep) {
      const [oldest, href] = this.idle.entries().next().value as [string, string];
      this.idle.delete(oldest);
      this.revoke(href);
    }
  }

  get size(): { live: number; idle: number } {
    return { live: this.live.size, idle: this.idle.size };
  }
}

const pool = new ObjectUrlPool((href) => URL.revokeObjectURL(href));

export function useArtifact(url: string | undefined): string | null {
  const [href, setHref] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setHref(null);
      return;
    }
    let alive = true;
    let holding = false;
    const known = pool.acquire(url);
    if (known !== null) {
      holding = true;
      setHref(known);
    } else {
      setHref(null);
      void (async () => {
        try {
          const { token } = await endpoint();
          const response = await fetch(url, { headers: { "X-Datum-Token": token } });
          if (!response.ok || !alive) return;
          const blob = await response.blob();
          if (!alive) return;
          const created = URL.createObjectURL(blob);
          holding = true;
          setHref(pool.add(url, created));
        } catch {
          /* доказательство не критично: проверка и так рассказала числами */
        }
      })();
    }
    return () => {
      alive = false;
      if (holding) pool.release(url);
    };
  }, [url]);

  return href;
}
