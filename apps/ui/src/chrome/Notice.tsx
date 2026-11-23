/** Уведомление внизу окна — «удалено · Вернуть», «положение возвращено». */
import { X } from "lucide-react";

import { useUi } from "../store/ui";

export function NoticeBar() {
  const notice = useUi((s) => s.notice);
  const { notify } = useUi.getState();
  if (!notice) return null;
  return (
    <div className={`notice ${notice.tone}`} role="status">
      <span>{notice.text}</span>
      {notice.action && (
        <button
          className="chip-button"
          onClick={() => {
            notice.action?.run();
            notify(null);
          }}
        >
          {notice.action.label}
        </button>
      )}
      <button className="icon-button small" aria-label="Закрыть" onClick={() => notify(null)}>
        <X size={14} />
      </button>
    </div>
  );
}
