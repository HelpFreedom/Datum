/**
 * Выпадающее меню: строка меню наверху и список видов во вьюпорте.
 *
 * Своё, а не меню Electron: у оболочки меню нет (окно без рамки у тайлового
 * менеджера), а пункты здесь — те же действия, что у кнопок, и обязаны
 * вести себя одинаково. В меню только то, что окно действительно умеет:
 * пустой пункт «для вида» — ложь интерфейса.
 */
import { Check } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

export type MenuItem =
  | {
      label: string;
      onSelect: () => void;
      keys?: string;
      disabled?: boolean;
      /** Флажок: пункт-переключатель показывает своё состояние. */
      checked?: boolean;
      hint?: string;
    }
  | { separator: true }
  | { heading: string };

interface Props {
  label: ReactNode;
  items: MenuItem[];
  buttonClass?: string;
  /** Управление снаружи — для строки меню, где открыто не больше одного. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onHover?: () => void;
  align?: "left" | "right";
}

export function Menu({ label, items, buttonClass, open, onOpenChange, onHover, align = "left" }: Props) {
  const [own, setOwn] = useState(false);
  const shown = open ?? own;
  const setShown = (value: boolean) => {
    if (open === undefined) setOwn(value);
    onOpenChange?.(value);
  };
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Закрыть по щелчку мимо и по Esc. Слушатель — на фазе захвата: щелчок по
  // холсту вьюпорта (там свои обработчики) тоже обязан закрыть меню.
  useEffect(() => {
    if (!shown) return;
    const onDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setShown(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setShown(false);
      }
    };
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("keydown", onKey, true);
    };
  });

  // Открытое меню — с фокусом на первом доступном пункте: стрелки работают сразу.
  useEffect(() => {
    if (!shown) return;
    const first = listRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)");
    first?.focus({ preventScroll: true });
  }, [shown]);

  const move = (step: number) => {
    const buttons = [...(listRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    if (!buttons.length) return;
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    buttons[(index + step + buttons.length) % buttons.length].focus();
  };

  return (
    <div className="menu" ref={rootRef}>
      <button
        className={buttonClass ?? "menu-button"}
        aria-haspopup="menu"
        aria-expanded={shown}
        onClick={() => setShown(!shown)}
        onMouseEnter={onHover}
      >
        {label}
      </button>
      {shown && (
        <div
          ref={listRef}
          className={`menu-list ${align === "right" ? "align-right" : ""}`}
          role="menu"
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              move(1);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              move(-1);
            }
          }}
        >
          {items.map((item, i) => {
            if ("separator" in item) return <div key={i} className="menu-separator" role="separator" />;
            if ("heading" in item) return <div key={i} className="menu-heading">{item.heading}</div>;
            return (
              <button
                key={i}
                role={item.checked === undefined ? "menuitem" : "menuitemcheckbox"}
                aria-checked={item.checked}
                className="menu-item"
                disabled={item.disabled}
                title={item.hint}
                onClick={() => {
                  setShown(false);
                  item.onSelect();
                }}
              >
                <span className="menu-check">{item.checked && <Check size={14} />}</span>
                <span className="menu-label">{item.label}</span>
                {item.keys && <kbd className="menu-keys">{item.keys}</kbd>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Строка меню: открыто не больше одного, и при открытом наведение переключает. */
export function MenuBar({ menus }: { menus: { label: string; items: MenuItem[] }[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <nav className="menubar" aria-label="Меню">
      {menus.map((menu) => (
        <Menu
          key={menu.label}
          label={menu.label}
          items={menu.items}
          open={open === menu.label}
          onOpenChange={(value) => setOpen(value ? menu.label : null)}
          onHover={() => {
            if (open && open !== menu.label) setOpen(menu.label);
          }}
        />
      ))}
    </nav>
  );
}
