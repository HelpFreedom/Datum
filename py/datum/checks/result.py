"""Результат проверки — замена идиомы `gate(ok, msg)` из рабочего процесса CAD.

Там во всех проектах стояло одно и то же:

    FAILS = []
    def gate(ok, msg):
        print(("  OK   " if ok else "  FAIL ") + msg)
        if not ok: FAILS.append(msg)
    ...
    print("ИТОГ:", "OK" if not FAILS else f"{len(FAILS)} FAIL")

Скопировано дословно в 15 файлов, а строка `ИТОГ` — в 26. Единственным способом
узнать результат был разбор stdout глазами, и таблицы в NOTES.md перенабирались
руками.

Здесь то же самое становится объектом: интерфейс, агент и сравнение версий
читают поля, а привычный текстовый вывод остаётся — его печатает рендерер.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["Artifact", "CheckResult", "CheckRun", "Status", "Severity", "Op"]

Status = Literal["pass", "fail", "warn", "skip", "error"]
Severity = Literal["blocking", "warn", "info"]
Op = Literal[">=", "<=", "==", "!=", "in", "none"]


@dataclass(frozen=True, slots=True)
class Artifact:
    """Доказательство: сечение в PNG, геометрия в GLB, ряд значений."""

    #: «pose» — положение подвижного звена (`{"part", "u"}`): окно ставит туда
    #: звено кнопкой «к худшему», поэтому его данные уходят в отчёт целиком.
    kind: Literal["png", "glb", "json", "series", "text", "pose"]
    caption: str
    data: Any = None
    digest: str = ""
    url: str = ""
    #: Путь к файлу относительно корня проекта. Нужен агенту: своим `Read` он
    #: прочтёт картинку напрямую, без токена и HTTP. Окну хватает `url`.
    path: str = ""


@dataclass(frozen=True, slots=True)
class CheckResult:
    id: str
    title: str
    status: Status
    message: str
    group: str = ""
    value: float | None = None
    unit: str = ""
    op: Op = "none"
    threshold: float | tuple | None = None
    depends_on: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    evidence: tuple[Artifact, ...] = ()
    severity: Severity = "blocking"
    duration_ms: float = 0.0
    check_version: int = 1

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "skip")

    @property
    def margin(self) -> float | None:
        """Запас со знаком, нормированный. Отрицательный — не проходит.

        Нужен для сортировки «худшее первым»: без нормировки объём в мм³ и угол
        в градусах между собой не сравнить.
        """
        if self.value is None:
            return None
        if self.op == "in" and isinstance(self.threshold, (tuple, list)):
            # Вилка: запас — до БЛИЖНЕЙ границы. Иначе проверка, стоящая
            # вплотную к верхнему краю, притворялась бы благополучной за счёт
            # далёкого нижнего. Открытая граница (None) в счёт не идёт.
            low, high = (list(self.threshold) + [None, None])[:2]
            gaps = []
            # Нулевая граница (частое «от нуля») нормировке не поддаётся — см.
            # ниже; в запас идёт только граница, от которой он осмыслен.
            # Бесконечная граница — «предела нет»: нормировать на неё нечего.
            if isinstance(low, (int, float)) and 1e-9 < abs(float(low)) < math.inf:
                gaps.append((self.value - float(low)) / abs(float(low)))
            if isinstance(high, (int, float)) and 1e-9 < abs(float(high)) < math.inf:
                gaps.append((float(high) - self.value) / abs(float(high)))
            return min(gaps) if gaps else None
        if not isinstance(self.threshold, (int, float)):
            return None
        t = float(self.threshold)
        if not math.isfinite(t):
            # Порог «без предела» (`support_volume` по умолчанию: max_volume=inf):
            # запас inf/inf был nan, и страж ответов обрывал ВЕСЬ прогон проверок.
            return None
        if abs(t) <= 1e-9:
            # Нулевой порог нормировать не на что. Раньше делили на единицу, и
            # «запас» выходил числом в единицах самой величины — несравнимым с
            # остальными: счётная проверка «лезвий ≤ 0» при нуле лезвий
            # оказывалась «ближе всего к краю» (+0.00) раньше настоящих узких мест.
            return None
        scale = abs(t)
        if self.op == ">=":
            return (self.value - t) / scale
        if self.op == "<=":
            return (t - self.value) / scale
        if self.op == "==":
            # У равенства «запас» бессмысленен как величина: проверка либо
            # совпала, либо нет. Возвращаем None, чтобы такие проверки не
            # притворялись худшими при сортировке.
            return None
        return None

    def line(self) -> str:
        """Строка в привычном виде корпуса."""
        mark = {"pass": "  OK   ", "fail": "  FAIL ", "warn": "  ВНИМ ",
                "skip": "  ПРОП ", "error": "  ОШИБ "}[self.status]
        return mark + self.message

    def as_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "group": self.group,
            "status": self.status, "message": self.message,
            "value": self.value, "unit": self.unit,
            "op": self.op, "threshold": self.threshold,
            "margin": self.margin,
            "depends_on": list(self.depends_on), "inputs": list(self.inputs),
            "severity": self.severity, "duration_ms": round(self.duration_ms, 2),
            "evidence": [{"kind": a.kind, "caption": a.caption, "url": a.url,
                          **({"path": a.path} if a.path else {}),
                          **({"data": a.data} if a.kind == "pose" else {})}
                         for a in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class CheckRun:
    results: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status in ("fail", "error"))

    @property
    def empty(self) -> bool:
        return not self.results

    @property
    def ok(self) -> bool:
        """Прошло — и было что проверять.

        Пустой прогон не «OK». Раньше он им был, и `datum check` на пустом
        каталоге или на проекте, где проверки забыли объявить, печатал
        «ИТОГ: OK» с кодом 0 — зелёная проверка пустоты.
        """
        return bool(self.results) and not self.failed

    @property
    def worst(self) -> CheckResult | None:
        """Самая тяжёлая проверка: сначала отказы, внутри — по запасу."""
        if not self.results:
            return None
        def key(r: CheckResult):
            rank = {"error": 0, "fail": 1, "warn": 2, "pass": 3, "skip": 4}[r.status]
            m = r.margin
            # Проверка без измеримого запаса не должна оказываться «худшей»
            # только потому, что её запас нечем выразить.
            return (rank, m if m is not None else float("inf"))
        return min(self.results, key=key)

    def report(self) -> str:
        """Текстовый отчёт в том же виде, к которому привык рабочий процесс CAD."""
        if not self.results:
            return ("ИТОГ: НЕЧЕГО ПРОВЕРЯТЬ — ни одной проверки. Объявите их в "
                    "checks/checks.yaml (каталог — `datum checks`) или в "
                    "checks/custom.py")
        lines = [r.line() for r in self.results]
        n_fail = len(self.failed)
        lines.append("")
        lines.append("ИТОГ: OK" if n_fail == 0 else f"ИТОГ: {n_fail} FAIL")
        # Пропуск — не «прошло»: проверка ничего не измерила. Итог он не
        # красит (пропускается и законное: нет привода — нечего судить), но
        # обязан быть виден рядом с итогом, а не теряться среди строк.
        skipped = [r.id for r in self.results if r.status == "skip"]
        if skipped:
            lines.append(f"  пропущено без замера ({len(skipped)}): " + ", ".join(skipped[:12])
                         + (" …" if len(skipped) > 12 else "")
                         + " — прочтите их строки: это не «прошло»")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "empty": self.empty,
            "n_pass": sum(1 for r in self.results if r.status == "pass"),
            "n_fail": len(self.failed),
            "n_warn": sum(1 for r in self.results if r.status == "warn"),
            "n_skip": sum(1 for r in self.results if r.status == "skip"),
            "results": [r.as_dict() for r in self.results],
            "worst": self.worst.as_dict() if self.worst else None,
        }

    def exit_code(self) -> int:
        return 0 if self.ok else 1
