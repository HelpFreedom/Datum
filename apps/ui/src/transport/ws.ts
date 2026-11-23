/**
 * Лента событий сервера.
 *
 * Токен уходит ПЕРВЫМ КАДРОМ, а не в строке запроса: строки запроса попадают в
 * журналы и историю, а токен — это ключ от сервера.
 *
 * При обрыве окно переподключается и просит повтор с последнего виденного
 * номера. Сервер держит кольцо на 1000 событий: если пропущено больше, он
 * честно отвечает `resync`, и окно перечитывает состояние целиком, вместо того
 * чтобы достраивать его из дырявой ленты.
 */
import { endpoint } from "./endpoint";

export interface DatumEvent {
  seq: number;
  t: string;
  [key: string]: unknown;
}

type Listener = (event: DatumEvent) => void;
type StatusListener = (connected: boolean) => void;

const RECONNECT_MS = [200, 400, 800, 1600, 3000] as const;

export class EventStream {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();
  private seq = 0;
  private attempt = 0;
  private timer: number | null = null;
  // Остановлен ли поток. Раньше `start()` сбрасывал этот флаг, и `stop()`,
  // вызванный до того, как `start()` дождался токена, ничего не останавливал:
  // сокет открывался после и закрыть его было некому.
  private stopped = false;
  /** Страница уже подключалась: следующее подключение — после обрыва. */
  private joined = false;

  /** Подписаться. Возвращает функцию отписки. */
  on(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Следить за связью. Раньше о ней не сообщалось вовсе, и строка состояния
   * горела «● сервер» и после смерти бэкенда.
   */
  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  private status(connected: boolean): void {
    for (const listener of this.statusListeners) listener(connected);
  }

  async start(): Promise<void> {
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
    this.socket?.close();
    this.socket = null;
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    const { token } = await endpoint();
    if (this.stopped) return;                    // остановили, пока ждали токен
    // Происхождение то же самое, поэтому адрес получается из location, а не из
    // настройки: разойтись им негде.
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      // Первое подключение страницы — БЕЗ досылки прошлого (`fresh`): состояние
      // она и так читает целиком (`refreshAll`). Иначе сервер досылал новой
      // странице всё кольцо событий, и каждое старое `geom.updated` заставляло
      // грузить и разбирать уже устаревшую геометрию — сразу после
      // перезагрузки из-за нехватки памяти. Досылка нужна только после
      // обрыва связи у живой страницы.
      socket.send(JSON.stringify({ token, since: this.seq, fresh: !this.joined }));
    };
    socket.onmessage = (message) => {
      let event: DatumEvent;
      try {
        event = JSON.parse(message.data as string) as DatumEvent;
      } catch {
        return;
      }
      if (typeof event.seq === "number") this.seq = event.seq;
      this.attempt = 0;
      // Связь считается установленной, когда сервер принял токен, а не когда
      // открылся сокет: неверный токен закрывает сокет сразу после открытия.
      if (event.t === "hello") {
        this.joined = true;
        this.status(true);
      }
      for (const listener of this.listeners) listener(event);
    };
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      this.status(false);
      this.scheduleReconnect();
    };
    socket.onerror = () => socket.close();
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.timer !== null) return;
    const delay = RECONNECT_MS[Math.min(this.attempt, RECONNECT_MS.length - 1)];
    this.attempt += 1;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      void this.connect();
    }, delay);
  }
}
