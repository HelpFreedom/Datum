/**
 * Знак и имя Datum.
 *
 * Datum по-английски — база: плоскость или точка, от которой отсчитываются
 * размеры. Знак о том же: изометрический куб, верхняя грань которого залита
 * как базовая плоскость, и светящаяся точка в его вершине — начало отсчёта.
 *
 * Рисуется в разметке, а не файлом: так он наследует цвета темы и не требует
 * ещё одного пути в раздаче бандла.
 */
export function Logo() {
  return (
    <div className="logo" aria-label="Datum">
      <svg className="logo-mark" width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
        <defs>
          <linearGradient id="logo-plane" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#7cc4ff" stopOpacity="0.95" />
            <stop offset="1" stopColor="#2f7bff" stopOpacity="0.55" />
          </linearGradient>
          <radialGradient id="logo-dot">
            <stop offset="0" stopColor="#ffffff" />
            <stop offset="0.45" stopColor="#9fd4ff" />
            <stop offset="1" stopColor="#3d9bff" stopOpacity="0" />
          </radialGradient>
        </defs>
        {/* Верхняя грань — базовая плоскость. */}
        <path d="M12 2.6 L20.2 7.3 L12 12 L3.8 7.3 Z" fill="url(#logo-plane)" />
        {/* Контур куба и рёбра, сходящиеся в ближней вершине. */}
        <path
          d="M12 2.6 L20.2 7.3 L20.2 16.7 L12 21.4 L3.8 16.7 L3.8 7.3 Z M12 12 L12 21.4 M12 12 L20.2 7.3 M12 12 L3.8 7.3"
          fill="none"
          stroke="#9cc8ff"
          strokeWidth="1.3"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {/* Начало отсчёта. */}
        <circle cx="12" cy="12" r="4.2" fill="url(#logo-dot)" />
        <circle cx="12" cy="12" r="1.35" fill="#ffffff" />
      </svg>
      <span className="logo-word">datum</span>
    </div>
  );
}
