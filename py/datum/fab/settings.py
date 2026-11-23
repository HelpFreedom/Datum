"""Настройки печати детали: откуда каждое число и что уходит в слайсер.

Решение владельца (2026-11-08): массу печатной детали Datum считает от
настроек, которые выставляет агент, — и ровно эти настройки уходят в
слайсер при нарезке. Иначе масса в симуляции и масса напечатанного жили бы
каждая своей жизнью.

Слои, от общего к частному (каждое число помнит, откуда оно):

  1. профиль слайсера — процесс и пластик, выбранные по datum.toml так же, как
     для `slice` (`io.slicer.choose`): ширины линий, подача, плотность, число
     верхних и нижних слоёв. Профилей нет — умолчания Elegoo CC2 0.4 / PETG
     (`FALLBACK`), и это сказано;
  2. `[print]` в datum.toml — для всего проекта;
  3. `@part(..., print={...})` — для детали. Значение — число или ИМЯ
     параметра (`"INFILL_GEAR"`): тогда оно читается через реестр, зависимость
     видна, а развёртка по диапазону гоняет и его — так агент подбирает
     заполнение по проверкам.

В слайсер уходит только заданное в 2 и 3: то, что взято из профиля, слайсер и
так знает.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

__all__ = ["PrintSettings", "SETTING_KEYS", "SLICER_KEYS", "FALLBACK", "resolve", "with_overrides",
           "from_profiles", "SettingsError"]


class SettingsError(ValueError):
    """Настройка печати задана так, что её не понять."""


#: Что можно задать в `[print]` и `@part(print=…)`, с пределами.
SETTING_KEYS = {
    "walls": "число стенок (периметров), 1…20",
    "infill": "доля заполнения 0…1 (или строка «15%»)",
    "top_layers": "сплошных слоёв сверху, 0…50",
    "bottom_layers": "сплошных слоёв снизу, 0…50",
    "pattern": "узор заполнения слайсера (gyroid, cubic, grid, rectilinear…)",
}

#: Ключ настройки → ключ профиля OrcaSlicer/ElegooSlicer.
SLICER_KEYS = {"walls": "wall_loops", "infill": "sparse_infill_density",
               "top_layers": "top_shell_layers", "bottom_layers": "bottom_shell_layers",
               "pattern": "sparse_infill_pattern"}

#: Профиль Elegoo «0.20mm Standard @Elegoo CC2 0.4 nozzle» + «Elegoo PETG @ECC2»
#: (снято с профилей ElegooSlicer 2026-11-08). Берётся, только если профилей нет.
FALLBACK = {"walls": 2, "infill": 0.15, "top_layers": 5, "bottom_layers": 3,
            "top_thickness": 1.0, "bottom_thickness": 0.6, "w_outer": 0.42,
            "w_inner": 0.45, "flow": 0.98 * 0.97, "density_g_cm3": 1.25,
            "pattern": "rectilinear"}


@dataclass(frozen=True)
class PrintSettings:
    layer: float
    walls: int
    infill: float
    top_layers: int
    bottom_layers: int
    top_thickness: float
    bottom_thickness: float
    w_outer: float
    w_inner: float
    flow: float
    density_g_cm3: float
    pattern: str = ""
    #: откуда каждое число: «профиль …», «datum.toml [print]», «деталь», «параметр X»
    sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def wall_thickness(self) -> float:
        return self.w_outer + max(self.walls - 1, 0) * self.w_inner

    @property
    def top_count(self) -> int:
        """Сплошных слоёв сверху — как у слайсера: не меньше числа слоёв и не
        тоньше `top_shell_thickness`, в НОМИНАЛЬНЫХ слоях. По толщине слоя
        разрезки (высота/число слоёв) 1.0 мм при 0.198 вместо 0.2 давали 6 слоёв
        вместо 5 — дно Gear Cube выходило на 8 % тяжелее слайсера."""
        return max(self.top_layers, math.ceil(self.top_thickness / self.layer - 1e-6))

    @property
    def bottom_count(self) -> int:
        return max(self.bottom_layers, math.ceil(self.bottom_thickness / self.layer - 1e-6))

    def slicer_overrides(self) -> dict[str, str]:
        """Правки профиля слайсера — только то, что задал проект или деталь."""
        out = {}
        for key, slicer_key in SLICER_KEYS.items():
            src = self.sources.get(key, "")
            if not src or src.startswith("профиль") or src.startswith("умолчание"):
                continue
            value = getattr(self, key)
            if key == "infill":
                out[slicer_key] = f"{round(value * 100, 2):g}%"
            else:
                out[slicer_key] = str(value)
        return out

    def describe(self) -> str:
        return (f"стенок {self.walls}, заполнение {self.infill * 100:.0f} %"
                + (f" {self.pattern}" if self.pattern else "")
                + f", верх/низ {self.top_layers}/{self.bottom_layers}, слой {self.layer:g} мм")

    def as_dict(self) -> dict:
        return {"layer": self.layer, "walls": self.walls, "infill": self.infill,
                "top_layers": self.top_layers, "bottom_layers": self.bottom_layers,
                "top_thickness": self.top_thickness, "bottom_thickness": self.bottom_thickness,
                "w_outer": self.w_outer, "w_inner": self.w_inner, "flow": round(self.flow, 6),
                "density_g_cm3": self.density_g_cm3, "pattern": self.pattern,
                "sources": dict(self.sources)}


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def from_profiles(printer: Mapping[str, Any]) -> tuple[dict, dict]:
    """Базовые числа из профилей слайсера для принтера datum.toml.

    Возвращает (значения, источники). Профилей нет или принтер не знаком —
    `FALLBACK` с источником «умолчание (профиль не найден: …)».
    """
    try:
        from datum.io.slicer import Profiles, choose

        profiles = Profiles()
        choice = choose(profiles, printer_profile=str(printer.get("profile", "elegoo-cc2-0.4")),
                        nozzle=float(printer.get("nozzle", 0.4)),
                        layer=float(printer.get("layer", 0.2)),
                        material=str(printer.get("material", "PLA")))
        proc = profiles.resolve("process", choice.process)
        fil = profiles.resolve("filament", choice.filament)
    except Exception as e:                     # нет профилей, незнакомый принтер или пластик
        why = str(e).split("\n")[0][:160]
        return dict(FALLBACK), {k: f"умолчание (профиль не найден: {why})" for k in FALLBACK}

    def num(d, key, fallback):
        try:
            return float(str(_first(d.get(key, fallback))).rstrip("%"))
        except ValueError:
            return float(fallback)

    values = {
        "walls": int(num(proc, "wall_loops", FALLBACK["walls"])),
        "infill": num(proc, "sparse_infill_density", FALLBACK["infill"] * 100) / 100.0,
        "top_layers": int(num(proc, "top_shell_layers", FALLBACK["top_layers"])),
        "bottom_layers": int(num(proc, "bottom_shell_layers", FALLBACK["bottom_layers"])),
        "top_thickness": num(proc, "top_shell_thickness", FALLBACK["top_thickness"]),
        "bottom_thickness": num(proc, "bottom_shell_thickness", FALLBACK["bottom_thickness"]),
        "w_outer": num(proc, "outer_wall_line_width", FALLBACK["w_outer"]),
        "w_inner": num(proc, "inner_wall_line_width", FALLBACK["w_inner"]),
        "flow": num(proc, "print_flow_ratio", 1.0) * num(fil, "filament_flow_ratio", 1.0),
        "density_g_cm3": num(fil, "filament_density", FALLBACK["density_g_cm3"]),
        "pattern": str(_first(proc.get("sparse_infill_pattern", FALLBACK["pattern"]))),
    }
    tag = f"профиль {choice.process} / {choice.filament}"
    return values, {k: tag for k in values}


def _value(key: str, raw: Any, where: str, read_param) -> tuple[Any, str]:
    """Значение настройки и его источник. Строка-имя параметра читается реестром."""
    src = where
    if isinstance(raw, str) and key != "pattern":
        text = raw.strip()
        if text.endswith("%"):
            try:
                raw = float(text[:-1]) / 100.0
            except ValueError:
                raise SettingsError(f"{where}: {key} = {raw!r} — не число процентов") from None
        else:
            name = text[len("${params."):-1] if text.startswith("${params.") else text
            if read_param is None:
                raise SettingsError(f"{where}: {key} = {raw!r} — параметр здесь не прочесть")
            try:
                raw = read_param(name)
            except Exception as e:
                raise SettingsError(f"{where}: {key} = {raw!r} — нет такого параметра "
                                    f"({type(e).__name__})") from None
            src = f"{where}, параметр {name}"
    if key == "pattern":
        if not isinstance(raw, str) or not raw.strip():
            raise SettingsError(f"{where}: pattern — имя узора строкой")
        return raw.strip(), src
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise SettingsError(f"{where}: {key} = {raw!r} — нужно число ({SETTING_KEYS[key]})")
    if key == "infill":
        if not 0.0 <= raw <= 1.0:
            hint = f" — проценты пишутся «{raw:g}%»" if 1.0 < raw <= 100 else ""
            raise SettingsError(f"{where}: infill = {raw!r} — доля 0…1{hint}")
        return float(raw), src
    lim = 20 if key == "walls" else 50
    lo = 1 if key == "walls" else 0
    if float(raw) != int(raw) or not lo <= raw <= lim:
        raise SettingsError(f"{where}: {key} = {raw!r} — целое {lo}…{lim}")
    return int(raw), src


def resolve(base: Mapping[str, Any], base_sources: Mapping[str, str], *, layer: float,
            project: Mapping[str, Any] | None = None, part: Mapping[str, Any] | None = None,
            part_name: str = "", read_param=None) -> PrintSettings:
    """Настройки детали: профиль ← [print] проекта ← @part(print=…)."""
    values, sources = dict(base), dict(base_sources)
    for layer_cfg, where in ((project or {}, "datum.toml [print]"),
                             (part or {}, f"деталь {part_name}" if part_name else "деталь")):
        if not isinstance(layer_cfg, Mapping):
            raise SettingsError(f"{where}: настройки печати — словарь {{walls: 3, infill: 0.2}}")
        unknown = sorted(set(layer_cfg) - set(SETTING_KEYS))
        if unknown:
            raise SettingsError(f"{where}: непонятные настройки печати {unknown}; известны: "
                                + "; ".join(f"{k} — {v}" for k, v in SETTING_KEYS.items()))
        for key, raw in layer_cfg.items():
            values[key], sources[key] = _value(key, raw, where, read_param)
    if not (math.isfinite(layer) and 0.04 <= layer <= 1.0):
        raise SettingsError(f"слой {layer!r} мм — вне 0.04…1.0")
    return PrintSettings(layer=float(layer), walls=int(values["walls"]), infill=float(values["infill"]),
                         top_layers=int(values["top_layers"]),
                         bottom_layers=int(values["bottom_layers"]),
                         top_thickness=float(values["top_thickness"]),
                         bottom_thickness=float(values["bottom_thickness"]),
                         w_outer=float(values["w_outer"]), w_inner=float(values["w_inner"]),
                         flow=float(values["flow"]), density_g_cm3=float(values["density_g_cm3"]),
                         pattern=str(values.get("pattern", "")),
                         sources={**sources, "layer": "datum.toml [printer].layer"})


def with_overrides(s: PrintSettings, **kw) -> PrintSettings:
    """Те же настройки с правкой (для подбора: «а если заполнение 30 %?»)."""
    sources = dict(s.sources)
    for k in kw:
        sources[k] = "подбор"
    return replace(s, sources=sources, **kw)
