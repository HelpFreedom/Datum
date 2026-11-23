/**
 * Картинка от сервера с увеличением по щелчку: доказательство проверки,
 * сравнение формы версий. Файл забирается с токеном (`useArtifact`).
 */
import { useEffect, useState } from "react";

import { useArtifact } from "../transport/artifact";

/**
 * Доказательство: сечение, на котором виновное место видно глазом.
 *
 * Проверка, сообщающая «пересечение 12.4 мм³», говорит правду и не говорит
 * ничего: где именно, по какой грани, на сколько залезли. Картинка строится
 * сервером на той же геометрии, по которой посчитано число.
 */
export function Evidence({ url, caption }: { url: string; caption: string }) {
  const href = useArtifact(url);
  const [big, setBig] = useState(false);

  useEffect(() => {
    if (!big) return;
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") setBig(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [big]);

  if (!href) return <div className="sub dim">картинка загружается…</div>;
  return (
    <>
      <button className="evidence-button" title={caption + " — увеличить"} onClick={() => setBig(true)}>
        <img className="evidence" src={href} alt={caption} />
      </button>
      {big && (
        <div
          className="lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={caption}
          onClick={() => setBig(false)}
        >
          <img src={href} alt={caption} />
        </div>
      )}
    </>
  );
}
