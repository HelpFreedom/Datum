"""Проверки удержания и подвижности.

ЗДЕСЬ ЖИВЁТ ГЛАВНЫЙ УРОК КОРПУСА.

Проект `phone-case-gears` печатался четыре раза. Версия v3 прошла все проверки и
развалилась в руках. Разбор в NOTES.md:

    «check3 считал «держит» по касанию (перекрытие > 0.1 мм³ при малом сдвиге),
     а не по зацепу после зазора.»

То есть деталь, которая всего лишь КАСАЕТСЯ корпуса при крошечном сдвиге,
засчитывалась как удержанная. На печати зазор съедал это касание, и деталь
вываливалась. Замеренные потом зацепы v3: шторка 0.8 − 0.57 = 0.23 мм,
эксцентрики крышки 0.00, кулачки 0.10.

Правильная проверка — `escape`: деталь обязана СВОБОДНО пройти весь печатный
зазор и только после этого упереться в материал заметным объёмом. Обе ветви
важны: без первой проверка ловит заклинившую деталь, без второй — выпадающую.

`naive_touch` оставлена намеренно и помечена устаревшей: на ней воспроизводится
провал v3, и это первый приёмочный тест Datum.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import replace

import numpy as np

from datum.checks.result import Artifact, CheckResult
from datum.geom.measure import inter_vol, man
from datum.geom.solid import Solid
from datum.geom.xform import place

__all__ = ["DIRECTIONS", "direction", "escape", "naive_touch", "travel_sweep", "stop_contact",
           "joint_sweep", "joint_matrix"]

#: Именованные направления. Регистр не важен: в корпусе писали и "+Z", и "+z".
DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "+X": (1, 0, 0), "-X": (-1, 0, 0),
    "+Y": (0, 1, 0), "-Y": (0, -1, 0),
    "+Z": (0, 0, 1), "-Z": (0, 0, -1),
}


def direction(key: str | Iterable[float]) -> np.ndarray:
    """Направление по имени ('+z') или как вектор. Нормируется."""
    if isinstance(key, str):
        k = key.strip().upper()
        if k not in DIRECTIONS:
            raise ValueError(f"неизвестное направление {key!r}; "
                             f"ожидается одно из {sorted(DIRECTIONS)} или вектор")
        v = np.array(DIRECTIONS[k], dtype=float)
    else:
        v = np.asarray(list(key), dtype=float)
    n = float(np.linalg.norm(v))
    if n == 0:
        raise ValueError("направление не может быть нулевым")
    return v / n


def _dir_label(key) -> str:
    """Имя направления для идентификатора: '+Z' как есть, вектор — '(0,0,1)'.

    Раньше вектор попадал в id как '[0, 0, 1]' — с пробелами и скобками, и
    такой идентификатор не брался ни в ссылки, ни в поиск по ленте.
    """
    if isinstance(key, str):
        return key.strip()          # регистр — как у автора: от него зависят id в снимках
    return "(" + ",".join(f"{float(v):g}" for v in key) + ")"


def _shifted(part: Solid, d: np.ndarray, dist: float):
    return man(part).translate(tuple(d * dist))


def _escape_picture(status, jammed, part, holder, direction, shift,
                    part_name, holder_name, check_id, message) -> tuple:
    """Картинка к провалу зацепа — только при провале.

    Показывается то положение, В КОТОРОМ проверка и поймала беду: при заклине
    — малый сдвиг, где деталь уже упёрлась; при «не держит» — большой, где она
    должна была упереться и не упёрлась.
    """
    if status != "fail":
        return ()
    from datum.checks.evidence import escape_evidence

    try:
        artifact = escape_evidence(
            part, holder, direction, shift,
            part_name=part_name, holder_name=holder_name,
            title=f"{check_id} — {'КЛИНИТ' if jammed else 'НЕ ДЕРЖИТ'}",
            note=message)
    except Exception as e:
        return (Artifact(kind="text",
                         caption=f"доказательство не построилось: "
                                 f"{type(e).__name__}: {e}"),)
    return (artifact,) if artifact is not None else ()


def escape(part: Solid, holder: Solid, dirs: Iterable[str], *,
           gap: float, hold_vol: float = 1.0, free_tol: float = 0.05,
           free_max_vol: float = 0.20, over: float = 1.0,
           id_prefix: str = "", part_name: str = "", holder_name: str = "",
           depends_on: tuple[str, ...] = ()) -> list[CheckResult]:
    """Зацеп после зазора — по одной проверке на направление.

    Деталь сдвигается вдоль направления на:
      * `gap - free_tol` — здесь пересечение должно быть практически нулевым
        (деталь свободно выбирает печатный зазор);
      * `gap + over`    — здесь пересечение обязано быть не меньше `hold_vol`
        (деталь упирается в материал, а не просто касается).

    Порт `check4.escape` из `phone-case-gears`, где значения по умолчанию
    были free_tol=0.05, over=1.0, hold_vol=1.0.
    """
    part_name = part_name or part.name or "деталь"
    holder_name = holder_name or holder.name or "корпус"
    prefix = id_prefix or f"{part_name}.escape"
    if hold_vol <= 0:
        # При нуле «держит» любая деталь, у которой губки нет вовсе.
        raise ValueError(f"hold_vol {hold_vol:g}: упор обязан быть заметным, больше нуля")
    if gap < 0:
        raise ValueError(f"gap {gap:g}: печатный зазор не бывает отрицательным")
    out: list[CheckResult] = []

    for key in dirs:
        t0 = time.perf_counter()
        d = direction(key)
        key = _dir_label(key)
        free_shift = max(0.0, gap - free_tol)
        v_free = inter_vol(_shifted(part, d, free_shift), holder)
        v_hold = inter_vol(_shifted(part, d, gap + over), holder)

        jammed = v_free > free_max_vol
        loose = v_hold < hold_vol
        status = "fail" if (jammed or loose) else "pass"

        if jammed and loose:
            msg = (f"{part_name} {key}: и клинит, и не держит — при сдвиге "
                   f"{free_shift:.2f} мм уже упирается ({v_free:.2f} мм³), "
                   f"а при {gap + over:.2f} мм зацеп всего {v_hold:.2f} мм³")
        elif jammed:
            msg = (f"{part_name} {key}: КЛИНИТ — при сдвиге {free_shift:.2f} мм "
                   f"(это меньше печатного зазора {gap:.2f}) уже пересекается с "
                   f"{holder_name} на {v_free:.2f} мм³ > {free_max_vol:.2f}")
        elif loose:
            msg = (f"{part_name} {key}: НЕ ДЕРЖИТ — при сдвиге {gap + over:.2f} мм "
                   f"зацеп {v_hold:.2f} мм³ < {hold_vol:.2f}; свободный ход "
                   f"{v_free:.2f} мм³. Касание не равно удержанию — ровно на этом "
                   f"развалилась v3 в phone-case-gears")
        else:
            # «Свободно до 0.25» при зазоре 0.30 читалось как ошибка: откуда
            # 0.25, не сказано. Это зазор без допуска `free_tol` — назван.
            msg = (f"{part_name} {key}: свободно при сдвиге {free_shift:.2f} мм "
                   f"(зазор {gap:.2f} − допуск {free_tol:.2f}; задевание "
                   f"{v_free:.2f} мм³), упор при {gap + over:.2f} мм "
                   f"{v_hold:.2f} мм³ ≥ {hold_vol:.2f}")

        # Число в результате — то, по которому проверка провалилась. При заклине
        # это объём на свободном ходу против его предела: раньше и здесь
        # отдавался упор, и заклинившая деталь с упором 52 мм³ получала запас
        # +51 — в списке «худшее первым» она уезжала в самый хвост провалов.
        value, op, threshold = ((v_free, "<=", free_max_vol) if jammed and not loose
                                else (v_hold, ">=", hold_vol))
        out.append(CheckResult(
            id=f"{prefix}.{key}", title=f"Зацеп после зазора, {key}",
            group="motion", status=status, message=msg,
            value=value, unit="мм³", op=op, threshold=threshold,
            inputs=(part_name, holder_name), depends_on=depends_on,
            duration_ms=(time.perf_counter() - t0) * 1000,
            evidence=(
                Artifact(kind="json", caption="объёмы пересечения",
                         data={"free_shift_mm": free_shift, "free_vol_mm3": v_free,
                               "hold_shift_mm": gap + over, "hold_vol_mm3": v_hold}),
                *_escape_picture(status, jammed, part, holder, d,
                                 free_shift if jammed else gap + over,
                                 part_name, holder_name, f"{prefix}.{key}", msg),
            ),
        ))
    return out


def naive_touch(part: Solid, holder: Solid, dirs: Iterable[str], *,
                shift: float = 0.1, min_overlap: float = 0.1,
                id_prefix: str = "", part_name: str = "",
                holder_name: str = "") -> list[CheckResult]:
    """УСТАРЕВШАЯ проверка «держит по касанию» — та самая, что пропустила v3.

    Оставлена только чтобы провал был воспроизводим и нагляден: на одной и той же
    геометрии она говорит «ок», а `escape` говорит «не держит».
    НЕ ИСПОЛЬЗОВАТЬ в проектах.
    """
    part_name = part_name or part.name or "деталь"
    holder_name = holder_name or holder.name or "корпус"
    prefix = id_prefix or f"{part_name}.naive_touch"
    out: list[CheckResult] = []
    for key in dirs:
        d = direction(key)
        v = inter_vol(_shifted(part, d, shift), holder)
        ok = v > min_overlap
        out.append(CheckResult(
            id=f"{prefix}.{key}", title=f"Касание при малом сдвиге, {key}",
            group="motion", status="pass" if ok else "fail",
            message=(f"[устарела] {part_name} {key}: при сдвиге {shift:.2f} мм "
                     f"перекрытие {v:.2f} мм³ "
                     f"{'>' if ok else '≤'} {min_overlap:.2f} — «держит»"),
            value=v, unit="мм³", op=">=", threshold=min_overlap,
            inputs=(part_name, holder_name), severity="info",
        ))
    return out


#: Предел числа положений в одной развёртке. Упёрлись — ответ помечается как
#: не достигший заданного разрешения, а не выдаётся за полный.
MAX_SWEEP_SAMPLES = 2000

#: Сколько положений брать, когда сдвиг между ними измерить нельзя.
BLIND_SWEEP_SAMPLES = 200

#: Доля интервала для пробы «не прошёл ли ход полный оборот» (`travel_sweep`).
_GOLDEN = (3.0 - 5.0 ** 0.5) / 2.0          # 0.381966…


def _vertices(solid: Solid) -> np.ndarray:
    return np.asarray(man(solid).to_mesh().vert_properties, dtype=float)[:, :3]


def _displacement(a: np.ndarray, b: np.ndarray) -> float | None:
    """Наибольший сдвиг вершины между двумя положениями. None — несравнимы.

    Жёсткое движение (перенос, поворот) топологию меша не меняет: вершины
    сопоставляются один к одному, и наибольший сдвиг — это ровно то, насколько
    далеко ушла самая быстрая точка детали. Для поворота это хорда у дальней
    вершины. Если деталь меняет форму от хода и число вершин разное, сдвиг
    измерить нельзя.
    """
    if a.shape != b.shape or not len(a):
        return None
    return float(np.linalg.norm(a - b, axis=1).max())


def travel_sweep(build: Callable[[float], Solid], others: Iterable[Solid], *,
                 u_range: tuple[float, float] = (0.0, 1.0), samples: int = 9,
                 refine: int = 6, max_inter_vol: float = 0.05,
                 resolution: float = 0.25,
                 id: str = "travel", title: str = "Ход без задевания",
                 depends_on: tuple[str, ...] = (),
                 at: Callable[[float], str] = lambda u: f"u={u:.4g}") -> CheckResult:
    """Развёртка хода подвижной детали с поиском худшего положения.

    Заменяет цикл `for u in np.arange(...)` из корпуса.

    ШАГ БЕРЁТСЯ ИЗ ГЕОМЕТРИИ, А НЕ ИЗ `samples`. Первая версия бралась за
    грубую сетку из `samples` положений и уточняла только вокруг найденного на
    ней максимума. Если деталь проходила СКВОЗЬ препятствие между узлами —
    ребро в полмиллиметра поперёк 40-миллиметрового хода, — на всех узлах
    пересечение было нулём, уточнять было нечего, и проверка говорила «ход
    свободен». Для зубьев и кулачков, чей контакт длится доли шага, это
    ложный зелёный.

    Теперь `samples` — лишь начальная сетка. Между соседними положениями
    меряется, насколько сдвинулась самая быстрая вершина детали, и интервал
    делится, пока сдвиг не станет меньше `resolution` (мм). Ход при этом может
    быть в чём угодно — миллиметрах, градусах, долях: считается движение
    детали, а не число `u`. Препятствие тоньше `resolution` проверка
    по-прежнему может проскочить, и это её честная граница.

    Если сдвиг измерить нельзя (деталь меняет форму от хода, и вершины не
    сопоставляются), берётся равномерная сетка `BLIND_SWEEP_SAMPLES`, и
    сообщение говорит об этом прямо.

    После покрытия худшее место уточняется бисекцией — это даёт точный пик.
    """
    t0 = time.perf_counter()
    others = list(others)
    if not u_range[1] > u_range[0]:
        raise ValueError(f"u_range {tuple(u_range)}: конец хода должен быть больше начала")
    if not resolution > 0:
        raise ValueError(f"resolution {resolution}: нужен положительный шаг, мм")

    # Пересечение храним для всех положений (это одно число), а вершины — лишь
    # для текущего и ещё не принятых справа: у сложной детали их сотни тысяч, и
    # держать их для всего хода значило бы гигабайты.
    value: dict[float, float] = {}
    shape: dict[float, np.ndarray] = {}
    part_name = ""

    def visit(u: float, keep_shape: bool) -> float:
        nonlocal part_name
        if u not in value or (keep_shape and u not in shape):
            part = build(u)
            part_name = part_name or part.name
            value[u] = float(max((inter_vol(part, o) for o in others), default=0.0))
            if keep_shape:
                shape[u] = _vertices(part)
        return value[u]

    lo_u, hi_u = float(u_range[0]), float(u_range[1])
    grid = [float(u) for u in np.linspace(lo_u, hi_u, max(2, int(samples)))]

    # Покрытие слева направо: следующее положение принимается, только если
    # самая быстрая точка детали дошла до него не дальше `resolution`; иначе
    # интервал делится пополам.
    blind = False
    capped = False
    min_blind_step = (hi_u - lo_u) / BLIND_SWEEP_SAMPLES
    pending = list(reversed(grid))
    current = pending.pop()
    visit(current, keep_shape=True)
    while pending:
        nxt = pending[-1]
        visit(nxt, keep_shape=True)
        d = _displacement(shape[current], shape[nxt])
        if d is None:
            blind = True
            too_far = (nxt - current) > min_blind_step * 1.0001
        else:
            too_far = d > resolution
            if not too_far and nxt - current > 0:
                # Сдвиг между концами мал — но мал ли путь? Хорда поворота на
                # целое число оборотов равна нулю: ход 0…2880° с начальной
                # сеткой через 360° выглядел неподвижным, интервалы не
                # делились, и проверка писала «максимум 0.000 мм³» с 15
                # положениями (аудит 2026-11-21). Пробуем точку внутри
                # интервала на доле золотого сечения: она иррациональна, и
                # целым числом оборотов не окажется ни при каком шаге.
                inner = current + (nxt - current) * _GOLDEN
                d_in = _displacement(shape[current], _vertices(build(inner)))
                too_far = d_in is not None and d_in > resolution
        if too_far and len(value) < MAX_SWEEP_SAMPLES:
            pending.append((current + nxt) / 2)
            continue
        capped = capped or too_far
        shape.pop(current, None)
        current = pending.pop()
    shape.clear()

    ordered = sorted(value)
    vals = [value[u] for u in ordered]
    i = int(np.argmax(vals))
    best_u, best_v = ordered[i], vals[i]

    # Уточнение пика между соседями худшей точки.
    lo = ordered[i - 1] if i > 0 else best_u
    hi = ordered[i + 1] if i + 1 < len(ordered) else best_u
    for _ in range(refine):
        for u in ((best_u + lo) / 2, (best_u + hi) / 2):
            v = visit(u, keep_shape=False)
            if v > best_v:
                best_u, best_v = u, v
        lo, hi = (best_u + lo) / 2, (best_u + hi) / 2
    built = value

    ok = best_v <= max_inter_vol
    status = "pass" if ok else "fail"
    who = f"{part_name}: " if part_name else ""
    how = (f"шаг по ходу равномерный, {len(built)} положений: деталь меняет форму от "
           f"хода, и её сдвиг измерить нельзя" if blind else
           f"{len(built)} положений, самая быстрая точка детали проходит между "
           f"соседними не больше {resolution:g} мм")
    if capped:
        how += (f"; ПРЕДЕЛ {MAX_SWEEP_SAMPLES} положений исчерпан — разрешение "
                f"{resolution:g} мм достигнуто не везде")
        if ok:
            status = "warn"
    ordered = sorted(built)
    return CheckResult(
        id=id, title=title, group="motion", status=status,
        message=(f"{who}по всему ходу максимум пересечения {best_v:.3f} мм³ при "
                 f"{at(best_u)} {'≤' if ok else '>'} {max_inter_vol:.3f} ({how})"),
        value=best_v, unit="мм³", op="<=", threshold=max_inter_vol,
        inputs=(part_name,) if part_name else (),
        depends_on=depends_on,
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="series", caption="пересечение по ходу",
                           data={"u": ordered, "v": [built[u] for u in ordered],
                                 "worst_u": best_u, "worst_v": best_v,
                                 "resolution_mm": resolution, "blind": blind,
                                 "capped": capped}),),
    )


def stop_contact(build: Callable[[float], Solid], stop: Solid, *,
                 u_over: float, min_vol: float = 1.0, id: str = "stop",
                 title: str = "Упор берёт нагрузку") -> CheckResult:
    """В конце хода нагрузку должен брать упор, а не зубья или защёлка."""
    part = build(u_over)
    v = inter_vol(part, stop)
    ok = v >= min_vol
    who = f"{part.name}: " if part.name else ""
    stop_name = f" ({stop.name})" if stop.name else ""
    return CheckResult(
        id=id, title=title, group="motion",
        status="pass" if ok else "fail",
        message=(f"{who}при перебеге u={u_over:.3f} упор{stop_name} перекрывается на "
                 f"{v:.2f} мм³ {'≥' if ok else '<'} {min_vol:.2f}"),
        value=v, unit="мм³", op=">=", threshold=min_vol,
        inputs=tuple(n for n in (part.name, stop.name) if n),
    )


# ------------------------------------------------------------------ шарниры
JOINT_KINDS = ("revolute", "prismatic")


def joint_matrix(kind: str, axis, origin, u: float) -> np.ndarray:
    """Жёсткое движение звена в положении u: матрица 4×4.

    revolute — поворот на u градусов вокруг оси `axis` через точку `origin`,
    по правилу правой руки (большой палец по оси, пальцы — куда растёт угол);
    prismatic — сдвиг на u мм вдоль `axis`.
    """
    a = direction(axis)
    m = np.eye(4)
    if kind == "prismatic":
        m[:3, 3] = a * u
        return m
    if kind != "revolute":
        raise ValueError(f"неизвестный вид шарнира {kind!r}; известны {', '.join(JOINT_KINDS)}")
    t = np.deg2rad(u)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    r = np.eye(3) + np.sin(t) * k + (1 - np.cos(t)) * (k @ k)      # Родриг
    c = np.asarray(origin, dtype=float)
    m[:3, :3] = r
    m[:3, 3] = c - r @ c
    return m


def joint_sweep(part: Solid, others: Iterable[Solid], *, kind: str, axis,
                limits: tuple[float, float], origin=(0.0, 0.0, 0.0),
                max_inter_vol: float = 0.05, resolution: float = 0.25,
                id: str = "joint", title: str = "",
                depends_on: tuple[str, ...] = ()) -> CheckResult:
    """Звено проходит весь объявленный ход шарнира, никого не задевая.

    Это `travel_sweep`, у которого функцию хода не пишет человек: движение
    ЖЁСТКОЕ и задано объявлением (`joints:` в assembly.yaml) — поворот вокруг
    оси или сдвиг вдоль неё. Поэтому вершины положений сопоставляются один к
    одному, и шаг берётся по пути самой быстрой точки звена: у крышки длиной
    100 мм шаг по углу мельче, чем у короткого рычага, без всякой настройки.

    Деталь берётся в собранном положении (с переносом из окна), ось и точка —
    в тех же координатах сцены. Положение «0» — то, в котором деталь построена.
    Если в нём касание задумано (крышка лежит на коробке без зазора), ход
    начинают чуть дальше нуля — молча вычитать посадку проверка не будет.
    """
    if kind not in JOINT_KINDS:
        raise ValueError(f"неизвестный вид шарнира {kind!r}; известны {', '.join(JOINT_KINDS)}")
    lo, hi = (float(v) for v in limits)
    others = list(others)

    unit = "°" if kind == "revolute" else " мм"

    def build(u: float) -> Solid:
        return place(part, joint_matrix(kind, axis, origin, u)) if u else part

    res = travel_sweep(build, others, u_range=(lo, hi), max_inter_vol=max_inter_vol,
                       resolution=resolution, id=id,
                       title=title or ("Шарнир поворачивается свободно" if kind == "revolute"
                                       else "Ползун ходит свободно"),
                       depends_on=depends_on,
                       at=lambda u: f"{u:.4g}{unit}")
    worst_u = res.evidence[0].data["worst_u"]
    message = res.message.replace("по всему ходу", f"на ходу {lo:g}…{hi:g}{unit}", 1)
    inputs = tuple(dict.fromkeys(res.inputs + tuple(o.name for o in others if o.name)))
    evidence = res.evidence
    if res.status == "fail" and part.name:
        evidence += (Artifact(kind="pose", caption="худшее положение звена",
                              data={"part": part.name, "u": round(worst_u, 6)}),)
    if res.status == "fail":
        # Кто именно мешает — первое, что спросит человек.
        moved = build(worst_u)
        hits = sorted(((inter_vol(moved, o), o.name or "?") for o in others), reverse=True)
        named = ", ".join(f"{n} {v:.2f} мм³" for v, n in hits if v > max_inter_vol)
        message += f". Мешает: {named}" if named else ""
    return replace(res, message=message, inputs=inputs, evidence=evidence)
