// Модуль без node-pty — его берёт тест.
/**
 * Хвост вывода терминала — чтобы окно, перезагруженное после падения, показало
 * экран как был. Вывод claude — это перерисовки TUI, и повтор хвоста в xterm
 * тех же размеров воспроизводит экран. Срез — по началу строки: оборванная
 * управляющая последовательность испортила бы верх экрана.
 */
export class Backlog {
  private chunks: string[] = [];
  private size = 0;

  constructor(private readonly limit = 4 * 1024 * 1024) {}

  push(chunk: string): void {
    this.chunks.push(chunk);
    this.size += chunk.length;
    if (this.size <= this.limit) return;
    let text = this.chunks.join("");
    text = text.slice(text.length - this.limit);
    const line = text.indexOf("\n");
    if (line >= 0) text = text.slice(line + 1);
    this.chunks = [text];
    this.size = text.length;
  }

  text(): string {
    return this.chunks.join("");
  }

  clear(): void {
    this.chunks = [];
    this.size = 0;
  }
}
