"""Механизм для симуляции: звенья, суставы, приводы, грузы, сценарии.

Объявляется в скрипте проекта, как деталь (`@mechanism` в parts/*.py), и
собирается функцией — с параметрами прогона (`P.…`), поэтому зависимости
видны в отчёте, а развёртка гоняет и их. Здесь — только объявления: модуль
не импортирует MuJoCo, и проект открывается там, где его нет.

СОГЛАШЕНИЯ — те же, что у шарниров `assembly.yaml`:
  * координаты — собранной сцены, мм; положение 0 — как тела построены;
  * ось сустава и точка на ней — в той же сцене при нулевом положении всех
    суставов; поворот — в градусах по правилу правой руки, сдвиг — в мм;
  * звено — жёсткая группа тел; у звена не больше одного сустава — к
    РОДИТЕЛЮ. Звено без сустава приварено к родителю. Корень — одно звено
    без родителя: основание.

МАССА ТЕЛА — без догадок (решение владельца 2026-11-08):
  * `part="имя"` — печатная деталь проекта: масса и инерция по её НАСТРОЙКАМ
    ПЕЧАТИ (`datum.fab`), тем самым, что уйдут в слайсер;
  * `mass_g=…` — покупное (мотор, серва, плата): масса известна, инерция — как
    у сплошного тела этой формы;
  * ни того, ни другого — отказ: «масса из воздуха» дала бы числа, которым
    верить нельзя.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

__all__ = ["Mechanism", "Link", "Piece", "Joint", "Drive", "Payload", "Scenario",
           "MechanismError", "BASES", "revolute", "prismatic", "drive", "Bearing", "bearing",
           "Capacity", "capacity"]

#: Как механизм стоит в мире:
#:   clamped — основание закреплено (струбцина, винты): опрокинуться не может,
#:             считается, ЧТО держит крепление (силы и моменты на основании);
#:   resting — основание просто стоит на столе: считается, не опрокинется ли
#:             и не поедет ли (опорный многоугольник, трение).
BASES = ("clamped", "resting")

#: Направления осей, как в assembly.yaml.
AXES = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0),
        "+Z": (0, 0, 1), "-Z": (0, 0, -1)}


class MechanismError(ValueError):
    """Механизм объявлен так, что считать его нельзя — с причиной."""


def _finite(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, np.floating, np.integer)):
        raise MechanismError(f"{what}: нужно число, получено {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise MechanismError(f"{what}: {v!r} — не число")
    return f


def _vec(v: Any, what: str) -> tuple[float, float, float]:
    if isinstance(v, str):
        key = v.strip().upper()
        if key not in AXES:
            raise MechanismError(f"{what}: направление {v!r} — одно из {', '.join(AXES)} "
                                 f"или вектор [x, y, z]")
        return tuple(float(c) for c in AXES[key])          # type: ignore[return-value]
    try:
        seq = list(v)
    except TypeError:
        raise MechanismError(f"{what}: вектор [x, y, z], получено {v!r}") from None
    if len(seq) != 3:
        raise MechanismError(f"{what}: вектор из трёх чисел, получено {v!r}")
    return tuple(_finite(c, what) for c in seq)             # type: ignore[return-value]


@dataclass(frozen=True)
class Drive:
    """Привод сустава: предел момента (Н·м) или силы (Н) и скорости (°/с или мм/с).

    `accel` — предел ускорения (°/с², мм/с²); не задан — разгон до скорости за
    `ramp_s` секунд (0.25 с — плавный пуск, как у прошивок любительских рук).
    """

    limit: float
    speed: float
    accel: float | None = None
    ramp_s: float = 0.25
    name: str = ""
    #: КПД передачи, когда привод ведёт груз (0…1]. `limit` — момент НА ВЫХОДЕ, уже
    #: за вычетом этих потерь (так дают паспорта мотор-редукторов и серв).
    efficiency: float = 1.0
    #: КПД обратного хода, когда груз ведёт привод (тормозит им). ≤ 0 —
    #: САМОТОРМОЖЕНИЕ (червяк): в покое передача держит сама, а опускать груз
    #: привод обязан ТОЛКАТЬ с |τ|·|η_обр|·η. Не задан — равен `efficiency`.
    back_efficiency: float | None = None
    #: Люфт передачи на выходе, ° (ползун — мм).
    backlash: float = 0.0
    #: Инерция ротора, приведённая к выходу, кг·м² (ползун — кг): I_рот·i².
    rotor_inertia: float = 0.0

    @property
    def max_accel(self) -> float:
        return self.accel if self.accel is not None else self.speed / self.ramp_s

    @property
    def back(self) -> float:
        return self.efficiency if self.back_efficiency is None else self.back_efficiency

    @property
    def self_locking(self) -> bool:
        return self.back <= 0.0


def drive(limit: float, speed: float, *, accel: float | None = None, ramp_s: float = 0.25,
          efficiency: float = 1.0, back_efficiency: float | None = None,
          backlash: float = 0.0, rotor_inertia: float = 0.0, name: str = "") -> Drive:
    """Привод: предел момента (Н·м; у ползуна — силы, Н) и скорость (°/с; мм/с).

    `efficiency`, `back_efficiency` (≤ 0 — самоторможение), `backlash` (°, мм) и
    `rotor_inertia` (кг·м² на выходе) — по паспорту или угаданным параметрам с
    диапазоном; не заданы — передача идеальная, и отчёт это говорит.

    `rotor_inertia` = J_рот·i²·η: ротор разгоняется до потерь передачи, а `limit` —
    момент после них (мотору нужно J_рот·α_м + τ/(i·η) ≤ T_макс; × i·η)."""
    lim = _finite(limit, "drive.limit")
    spd = _finite(speed, "drive.speed")
    if lim <= 0:
        raise MechanismError(f"drive.limit = {limit!r}: предел момента должен быть больше нуля")
    if spd <= 0:
        raise MechanismError(f"drive.speed = {speed!r}: скорость должна быть больше нуля")
    acc = None if accel is None else _finite(accel, "drive.accel")
    if acc is not None and acc <= 0:
        raise MechanismError(f"drive.accel = {accel!r}: должно быть больше нуля")
    ramp = _finite(ramp_s, "drive.ramp_s")
    if ramp <= 0:
        raise MechanismError(f"drive.ramp_s = {ramp_s!r}: должно быть больше нуля")
    eff = _finite(efficiency, "drive.efficiency")
    if not 0 < eff <= 1:
        raise MechanismError(f"drive.efficiency = {efficiency!r}: КПД — доля (0…1]")
    back = None if back_efficiency is None else _finite(back_efficiency, "drive.back_efficiency")
    # Ниже −1 — законно: у червяка η_обр = tan(γ−φ)/tan γ, при γ 2° и μ 0.15 это −3.3;
    # опускание груза стоит приводу |η_обр|·η = tan(φ−γ)/tan(γ+φ) < 1 предела. Нижняя
    # граница −20 — от опечаток (φ в 21 раз больше угла подъёма не бывает у передач).
    if back is not None and not -20 <= back <= 1:
        raise MechanismError(f"drive.back_efficiency = {back_efficiency!r}: от −20 до 1 "
                             f"(≤ 0 — самоторможение; у червяка tan(γ−φ)/tan γ)")
    lash = _finite(backlash, "drive.backlash")
    rotor = _finite(rotor_inertia, "drive.rotor_inertia")
    if lash < 0 or rotor < 0:
        raise MechanismError("drive.backlash и drive.rotor_inertia не бывают отрицательными")
    return Drive(lim, spd, acc, ramp, name, eff, back, lash, rotor)


@dataclass(frozen=True)
class Bearing:
    """Трение в опоре сустава: τ = drag + mu·|F_рад|·radius + mu_axial·|F_ос|·axial_radius.

    Нагрузки — РЕАКЦИИ сустава из динамики в каждый момент, а не догадка о
    них. Подшипник качения: mu ≈ 0.0015 и radius — половина диаметра
    отверстия (метод SKF), drag — сопротивление уплотнений; ось в печатной
    втулке: mu — трение скольжения пары, radius — радиус оси."""

    mu: float = 0.0
    radius_mm: float = 0.0
    drag: float = 0.0                       # Н·м (ползун — Н)
    mu_axial: float = 0.0
    axial_radius_mm: float = 0.0


def bearing(*, mu: float = 0.0, radius_mm: float = 0.0, drag: float = 0.0,
            mu_axial: float = 0.0, axial_radius_mm: float = 0.0) -> Bearing:
    """Трение в опоре сустава по реакциям (см. `Bearing`)."""
    vals = {k: _finite(v, f"bearing.{k}") for k, v in
            dict(mu=mu, radius_mm=radius_mm, drag=drag, mu_axial=mu_axial,
                 axial_radius_mm=axial_radius_mm).items()}
    if any(v < 0 for v in vals.values()):
        raise MechanismError("bearing: трение, радиусы и сопротивление не бывают отрицательными")
    if vals["mu"] > 1 or vals["mu_axial"] > 1:
        raise MechanismError("bearing.mu > 1 — коэффициент трения такой не бывает у опор")
    return Bearing(**vals)


@dataclass(frozen=True)
class Capacity:
    """Что выдерживает опора сустава: радиальная, осевая сила (Н) и момент
    поперёк оси (Н·м) — вал сервы, подшипник (C₀), ось во втулке. 0 — не задано."""

    radial: float = 0.0
    axial: float = 0.0
    moment: float = 0.0


def capacity(*, radial: float = 0.0, axial: float = 0.0, moment: float = 0.0) -> Capacity:
    """Допустимые нагрузки опоры сустава (см. `Capacity`) — для карты загрузки."""
    vals = {k: _finite(v, f"capacity.{k}") for k, v in
            dict(radial=radial, axial=axial, moment=moment).items()}
    if any(v < 0 for v in vals.values()):
        raise MechanismError("capacity: допустимые нагрузки не бывают отрицательными")
    return Capacity(**vals)


@dataclass(frozen=True)
class Joint:
    kind: str                                   # revolute | prismatic
    axis: tuple[float, float, float]            # единичный, в сцене при нуле
    origin: tuple[float, float, float]          # точка на оси, мм
    limits: tuple[float, float]                 # °, мм
    drive: Drive | None = None
    #: Ведомый сустав: (звено-ведущий, отношение) — положение = отношение ×
    #: положение ведущего. Шестерня, ремень, тяга: двигает его не свой привод.
    follows: tuple[str, float] | None = None
    #: КПД передачи от ведущего к этому ведомому (зацепление), (0…1].
    mesh_efficiency: float = 1.0
    friction: Bearing | None = None
    capacity: Capacity | None = None

    @property
    def unit(self) -> str:
        return "°" if self.kind == "revolute" else "мм"

    @property
    def effort_unit(self) -> str:
        return "Н·м" if self.kind == "revolute" else "Н"


def _joint(kind: str, axis, origin, range, drive: Drive | None,
           follows: tuple[str, float] | None = None, mesh_efficiency: float = 1.0,
           friction: Bearing | None = None, capacity: Capacity | None = None) -> Joint:
    a = np.asarray(_vec(axis, f"{kind}.axis"), float)
    n = float(np.linalg.norm(a))
    if n < 1e-9:
        raise MechanismError(f"{kind}.axis: нулевой вектор")
    try:
        lo, hi = (_finite(v, f"{kind}.range") for v in range)
    except (TypeError, ValueError):
        raise MechanismError(f"{kind}.range — пара [от, до], получено {range!r}") from None
    if not hi > lo:
        raise MechanismError(f"{kind}.range {list(range)}: конец больше начала")
    # Больше оборота — законно: мотор крутит сердце Gear Cube непрерывно. Предел
    # лишь от опечатки в порядке величины.
    if kind == "revolute" and hi - lo > 36000:
        raise MechanismError(f"{kind}.range {list(range)}: больше 100 оборотов — опечатка?")
    if drive is not None and not isinstance(drive, Drive):
        raise MechanismError("drive — объявите через drive(предел, скорость)")
    fol = None
    if follows is not None:
        try:
            leader, ratio = follows
        except (TypeError, ValueError):
            raise MechanismError(f"{kind}.follows — пара (звено-ведущий, отношение), "
                                 f"получено {follows!r}") from None
        if not isinstance(leader, str) or not leader:
            raise MechanismError(f"{kind}.follows: ведущий — имя звена")
        r = _finite(ratio, f"{kind}.follows: отношение")
        if r == 0:
            raise MechanismError(f"{kind}.follows: отношение 0 — сустав не двигается, "
                                 f"тогда это не сустав")
        if drive is not None:
            raise MechanismError(f"{kind}: у ведомого сустава нет своего привода — его "
                                 f"двигает ведущий; привод объявите у ведущего")
        fol = (leader, r)
    me = _finite(mesh_efficiency, f"{kind}.mesh_efficiency")
    if not 0 < me <= 1:
        raise MechanismError(f"{kind}.mesh_efficiency = {mesh_efficiency!r}: КПД — доля (0…1]")
    if me != 1.0 and fol is None:
        raise MechanismError(f"{kind}.mesh_efficiency — только у ведомого (follows=…): это КПД "
                             f"его зацепления с ведущим")
    if friction is not None and not isinstance(friction, Bearing):
        raise MechanismError(f"{kind}.friction — объявите через bearing(mu=…, radius_mm=…)")
    if friction is not None and kind == "revolute":
        # Трение шарнира — момент μ·F·r: без радиуса оно ноль, а объявленное
        # трение, которое молча равно нулю, хуже необъявленного — заметки
        # «трение опор не задано» при нём уже нет.
        if friction.mu > 0 and friction.radius_mm <= 0:
            raise MechanismError(
                "revolute.friction: bearing(mu=…) без radius_mm — момент трения μ·F·r "
                "вышел бы нулём. Укажите радиус, на котором трётся опора (радиус вала "
                "или средний радиус дорожки подшипника)")
        if friction.mu_axial > 0 and friction.axial_radius_mm <= 0:
            raise MechanismError(
                "revolute.friction: bearing(mu_axial=…) без axial_radius_mm — момент "
                "трения торца вышел бы нулём. Укажите средний радиус опорного торца")
    if capacity is not None and not isinstance(capacity, Capacity):
        raise MechanismError(f"{kind}.capacity — объявите через capacity(radial=…, …)")
    return Joint(kind, tuple(a / n), _vec(origin, f"{kind}.origin"), (lo, hi), drive, fol,
                 me, friction, capacity)


def revolute(axis, origin, range, *, drive: Drive | None = None,
             follows: tuple[str, float] | None = None, mesh_efficiency: float = 1.0,
             friction: Bearing | None = None, capacity: Capacity | None = None) -> Joint:
    """Поворот вокруг `axis` через точку `origin` (мм); `range` — [от, до] в градусах.

    `follows=("сердце", -1.5)` — ведомый: угол = −1.5 × угол сердца (шестерня),
    `mesh_efficiency` — КПД его зацепления; `friction=bearing(…)` — трение опоры
    по реакциям; `capacity=capacity(…)` — что опора выдерживает (карта загрузки)."""
    return _joint("revolute", axis, origin, range, drive, follows, mesh_efficiency,
                  friction, capacity)


def prismatic(axis, range, *, drive: Drive | None = None,
              follows: tuple[str, float] | None = None, mesh_efficiency: float = 1.0,
              friction: Bearing | None = None, capacity: Capacity | None = None) -> Joint:
    """Сдвиг вдоль `axis`; `range` — [от, до] в мм. Остальное — как у `revolute`."""
    return _joint("prismatic", axis, (0, 0, 0), range, drive, follows, mesh_efficiency,
                  friction, capacity)


@dataclass(frozen=True)
class Piece:
    """Тело звена: геометрия + откуда масса."""

    #: Тело в СВОЕЙ системе — системе скрипта детали `part` (в ней объявлена
    #: поза печати `print_up`, по ней режутся слои для массы).
    solid: Any
    label: str
    part: str | None = None                     # печатная деталь проекта
    mass_g: float | None = None                 # покупное — известная масса
    #: Куда тело ставится в сцене при нулевых суставах (4×4, мм): сцена = frame · тело.
    #: None — тело уже на своём месте в сцене.
    frame: tuple | None = None


@dataclass
class Link:
    name: str
    parent: str | None
    joint: Joint | None
    pieces: list[Piece] = field(default_factory=list)

    def add(self, solid, *, part: str | None = None, mass_g: float | None = None,
            label: str = "", frame=None) -> "Link":
        """Тело звена. `part` — печатная деталь (масса по её настройкам печати),
        `mass_g` — покупное. Оба сразу — печатная деталь с ИЗВЕСТНОЙ массой
        (взвешена, из G-code): масса — заданная, а деталь остаётся печатной — её
        считает МКЭ (`sim_stress`). `solid` — в системе скрипта детали; `frame`
        (4×4) — куда он встаёт в сцене при нулевых суставах (нет — уже на месте)."""
        if part is None and mass_g is None:
            raise MechanismError(
                f"звено {self.name!r}, тело {label or '?'}: масса не указана — part=«деталь» "
                f"(по настройкам печати), mass_g=… (покупное) или оба (печатная деталь с "
                f"известной массой: взвешена, из G-code)")
        m = None if mass_g is None else _finite(mass_g, f"{self.name}: mass_g")
        if m is not None and m <= 0:
            raise MechanismError(f"звено {self.name!r}: mass_g = {mass_g!r} — больше нуля")
        f = None
        if frame is not None:
            arr = np.asarray(frame, float)
            if arr.shape != (4, 4) or not np.all(np.isfinite(arr)):
                raise MechanismError(f"звено {self.name!r}: frame — матрица 4×4")
            f = tuple(map(tuple, arr))
        self.pieces.append(Piece(solid=solid, label=label or part or f"тело {len(self.pieces) + 1}",
                                 part=part, mass_g=m, frame=f))
        return self


@dataclass(frozen=True)
class Payload:
    name: str
    link: str
    mass_g: float
    at: tuple[float, float, float]              # точка в сцене при нуле, мм


@dataclass(frozen=True)
class Scenario:
    """Движение: позы по порядку. Поза — {звено: положение сустава}; не
    названные суставы стоят где стояли. `hold_s` — пауза в каждой позе,
    `payloads` — какие грузы несёт механизм в этом сценарии."""

    name: str
    poses: tuple[Mapping[str, float], ...]
    hold_s: float = 0.3
    payloads: tuple[str, ...] = ()
    speed_scale: float = 1.0


@dataclass
class Mechanism:
    """Механизм: base="clamped" (основание закреплено) или "resting" (стоит на столе)."""

    base: str = "clamped"
    links: dict[str, Link] = field(default_factory=dict)
    payloads: dict[str, Payload] = field(default_factory=dict)
    scenarios: dict[str, Scenario] = field(default_factory=dict)
    #: Коэффициент трения основания о стол (для base="resting").
    friction: float = 0.4

    def __post_init__(self) -> None:
        if self.base not in BASES:
            raise MechanismError(f"base={self.base!r}: {' или '.join(BASES)} — закреплено "
                                 f"основание или просто стоит на столе")

    def link(self, name: str, *, parent: str | None = None, joint: Joint | None = None) -> Link:
        """Звено: первое — основание (без родителя и сустава), остальные — с суставом к родителю."""
        if not isinstance(name, str) or not name.strip():
            raise MechanismError("у звена должно быть имя")
        if name in self.links:
            raise MechanismError(f"звено {name!r} объявлено дважды")
        if parent is None and self.links:
            raise MechanismError(f"звено {name!r} без родителя, а корень уже есть "
                                 f"({next(iter(self.links))!r}): у механизма одно основание")
        if parent is not None and parent not in self.links:
            raise MechanismError(f"звено {name!r}: родитель {parent!r} ещё не объявлен; "
                                 f"есть: {list(self.links)}")
        if parent is None and joint is not None:
            raise MechanismError(f"основание {name!r} не может иметь сустава — "
                                 f"как оно стоит, задаёт base=")
        if joint is not None and not isinstance(joint, Joint):
            raise MechanismError(f"звено {name!r}: сустав — revolute(…) или prismatic(…)")
        if joint is not None and joint.follows is not None:
            leader = joint.follows[0]
            lj = self.links[leader].joint if leader in self.links else None
            if lj is None:
                raise MechanismError(f"звено {name!r} следует за {leader!r}, а у того нет "
                                     f"сустава (или он ещё не объявлен)")
            if lj.follows is not None:
                raise MechanismError(f"звено {name!r} следует за {leader!r}, а тот сам ведомый: "
                                     f"отношение считайте до ведущего с приводом")
        lk = Link(name, parent, joint)
        self.links[name] = lk
        return lk

    def payload(self, name: str, *, link: str, mass_g: float, at) -> Payload:
        """Сосредоточенный груз на звене (банка в захвате, гиря на рычаге)."""
        if link not in self.links:
            raise MechanismError(f"груз {name!r}: нет звена {link!r}; есть: {list(self.links)}")
        m = _finite(mass_g, f"груз {name}: mass_g")
        if m <= 0:
            raise MechanismError(f"груз {name!r}: mass_g больше нуля")
        p = Payload(name, link, m, _vec(at, f"груз {name}: at"))
        self.payloads[name] = p
        return p

    def scenario(self, name: str, poses: Sequence[Mapping[str, float]], *, hold_s: float = 0.3,
                 payloads: Iterable[str] = (), speed_scale: float = 1.0) -> Scenario:
        """Сценарий: позы по порядку ({звено: положение}); первая — откуда начать."""
        if name in self.scenarios:
            raise MechanismError(f"сценарий {name!r} объявлен дважды")
        poses = [dict(p) for p in poses]
        if not poses:
            raise MechanismError(f"сценарий {name!r}: хотя бы одна поза")
        moving = set(self.drivers())
        for i, pose in enumerate(poses):
            for jn, v in pose.items():
                if jn not in moving:
                    why = ("он ведомый — его положение следует за ведущим"
                           if jn in self.links and self.links[jn].joint is not None
                           else "у него нет сустава")
                    raise MechanismError(f"сценарий {name!r}, поза {i + 1}: {jn!r} — {why}; "
                                         f"задаются: {sorted(moving)}")
                lo, hi = self.links[jn].joint.limits                 # type: ignore[union-attr]
                val = _finite(v, f"сценарий {name}, поза {i + 1}, {jn}")
                if not lo - 1e-9 <= val <= hi + 1e-9:
                    raise MechanismError(f"сценарий {name!r}, поза {i + 1}: {jn} = {val:g} вне "
                                         f"хода [{lo:g}, {hi:g}]")
                pose[jn] = val
        pl = tuple(payloads)
        for p in pl:
            if p not in self.payloads:
                raise MechanismError(f"сценарий {name!r}: нет груза {p!r}; есть: {list(self.payloads)}")
        hold = _finite(hold_s, f"сценарий {name}: hold_s")
        scale = _finite(speed_scale, f"сценарий {name}: speed_scale")
        if hold < 0 or not 0 < scale <= 1:
            raise MechanismError(f"сценарий {name!r}: hold_s ≥ 0, 0 < speed_scale ≤ 1")
        sc = Scenario(name, tuple(poses), hold, pl, scale)
        self.scenarios[name] = sc
        return sc

    # ------------------------------------------------------------------ сверка
    def validate(self) -> None:
        if not self.links:
            raise MechanismError("в механизме нет звеньев")
        for lk in self.links.values():
            if not lk.pieces:
                raise MechanismError(f"звено {lk.name!r} без тел: массу и форму взять неоткуда")
        if not any(lk.joint is not None for lk in self.links.values()):
            raise MechanismError("ни одного сустава — двигаться нечему; для устойчивости "
                                 "неподвижного изделия есть tipping_margin")

    @property
    def root(self) -> str:
        return next(iter(self.links))

    def joints(self) -> list[str]:
        """Звенья с суставами — в порядке объявления (он же порядок в модели)."""
        return [n for n, lk in self.links.items() if lk.joint is not None]

    def drivers(self) -> list[str]:
        """Независимые суставы — те, что задаёт сценарий и двигает привод."""
        return [n for n in self.joints() if self.links[n].joint.follows is None]

    def coupling(self) -> np.ndarray:
        """Матрица связи C (все суставы × независимые): q_все = C · q_незав.

        Для идеальной передачи возможная работа сохраняется, поэтому момент,
        который должен дать ведущий, — Cᵀ · τ_все (вклад ведомого — его
        момент, умноженный на отношение)."""
        allj, ind = self.joints(), self.drivers()
        c = np.zeros((len(allj), len(ind)))
        for i, n in enumerate(allj):
            f = self.links[n].joint.follows
            if f is None:
                c[i, ind.index(n)] = 1.0
            else:
                c[i, ind.index(f[0])] = f[1]
        return c

    def chain(self, name: str) -> list[str]:
        """Звенья от корня до `name` включительно."""
        out = []
        cur: str | None = name
        while cur is not None:
            out.append(cur)
            cur = self.links[cur].parent
        return out[::-1]
