/**
 * Предохранитель: сбой в одной панели не должен гасить всё окно.
 *
 * Без него любая ошибка отрисовки — поле, которого не оказалось в ответе, —
 * снимала весь интерфейс, и оставался чёрный экран без единого слова. Так и
 * было: синтаксическая ошибка в скрипте детали давала ответ сервера с полем
 * `error` вместо сводки, окно лезло в отсутствующее поле и гасло. Самый
 * частый ход агента — правка скрипта — убивал окно молча.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  /** Что за область — чтобы в тексте было понятно, что именно отказало. */
  area: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class Fault extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // В консоль — для сценариев проверки окна и для разработки.
    console.error(`[datum] ${this.props.area}: ${error.message}`, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="alert" role="alert">
        <div>
          {this.props.area}: сбой отрисовки — {error.message}
        </div>
        <button onClick={() => this.setState({ error: null })}>Попробовать снова</button>
      </div>
    );
  }
}
