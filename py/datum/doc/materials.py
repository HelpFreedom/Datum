"""Каталог материалов: свойства пластика с происхождением — параметрами проекта.

ОГОВОРКА ВЛАДЕЛЬЦА: это просто числа из интернета — страницы товаров, без
стандартов и условий испытаний, — и они могут быть неточны. Других нет,
поэтому используются эти; свой замер их перебивает (см. ниже). Оговорка
повторена в `ref` каждого параметра, чтобы её видел каждый, кто его читает.

ОТКУДА ЧИСЛА. Только со страниц производителя пластика, которым печатает
владелец (Elegoo, Centauri Carbon 2), снятых 2026-10-02 — адрес у каждого
материала. ЧЕГО У ELEGOO НЕТ: стандартов испытаний (ни ISO, ни ASTM),
условий печати образцов, модуля упругости при растяжении, температуры
стеклования, усадки. Образцы — печатные, по двум направлениям: X-Y (в
плоскости слоя) и Z (поперёк слоёв).

МОДУЛЬ УПРУГОСТИ — ДОПУЩЕНИЕ (решение владельца): модуль ПРИ ИЗГИБЕ Elegoo
вместо модуля при растяжении, которого Elegoo не даёт. Испытание другое,
поэтому `MAT_E` — assumed с диапазоном от образца Z минус его разброс до X-Y
плюс разброс, и `sweep_assumed`
покажет, решает ли он дело. Для сравнения: печатные образцы по ISO 527 у
Bambu Lab и Prusament — PLA 2.3…2.6 ГПа, PETG 1.5…2.8 ГПа.

ДОПУСТИМОЙ ДЕФОРМАЦИИ ЗДЕСЬ НЕТ (решение владельца): для печатных PLA и PETG
надёжного источника не нашлось — только блоги с разбросом в разы. Предел
деформации защёлки остаётся параметром проекта, угаданным, с диапазоном.

КАК ИСПОЛЬЗУЕТСЯ. Проект добавляет параметры `MAT_*` материала из
`datum.toml` (`printer.material`), если не объявил их сам: свой замер
перебивает каталог. Проверки с аргументом `modulus` или `density` берут их,
когда аргумент не задан.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Material", "CATALOG", "CAVEAT", "material_params", "describe"]

ACCESSED = "2026-10-02"
NO_STANDARD = "страница товара Elegoo, стандарт испытаний не указан"

#: Оговорка владельца — в каждом параметре и в отчёте.
CAVEAT = ("числа из интернета, могут быть неточны; других нет — берём эти, "
          "свой замер перебивает")


@dataclass(frozen=True)
class Material:
    name: str
    url: str
    density: float                         # г/см³
    tensile: tuple[float, float]           # МПа, X-Y / Z
    elongation: tuple[float, float]        # %, удлинение при разрыве, X-Y / Z
    flex_modulus: tuple[float, float] | None        # МПа, X-Y / Z
    flex_modulus_sd: tuple[float, float] | None     # МПа, разброс по Elegoo
    flex_strength: tuple[float, float] | None       # МПа, X-Y / Z
    hdt_c: float | None                    # °C при 0.45 МПа
    vicat_c: float | None                  # °C
    nozzle_c: tuple[float, float]
    bed_c: tuple[float, float]
    note: str = ""
    elongation_at_least: bool = False      # «>500 %» — дана только нижняя граница


CATALOG: dict[str, Material] = {
    "PLA": Material(
        "Elegoo PLA", "https://us.elegoo.com/products/pla-filament-1-75mm-colored-1kg",
        density=1.26, tensile=(38, 23), elongation=(10.5, 6.7),
        flex_modulus=(2993, 2633), flex_modulus_sd=(182, 165), flex_strength=(72, 45),
        hdt_c=57, vicat_c=62, nozzle_c=(190, 230), bed_c=(35, 65)),
    "PETG": Material(
        "Elegoo PETG Pro", "https://us.elegoo.com/products/petg-pro-filament-1-75mm-colored-1kg",
        density=1.27, tensile=(40, 25), elongation=(7.6, 5.8),
        flex_modulus=(2111, 1846), flex_modulus_sd=(119, 145), flex_strength=(70, 42),
        hdt_c=73, vicat_c=71, nozzle_c=(230, 260), bed_c=(65, 75),
        note="простого PETG без «Pro» на сайте Elegoo нет — данные PETG Pro"),
    "ABS": Material(
        "Elegoo ABS", "https://us.elegoo.com/products/abs-filament-1-75mm-colored-1kg",
        density=1.05, tensile=(35, 29), elongation=(7.2, 6.9),
        flex_modulus=(2484, 2133), flex_modulus_sd=(115, 105), flex_strength=(68, 53),
        hdt_c=85, vicat_c=86, nozzle_c=(240, 280), bed_c=(90, 100)),
    "ASA": Material(
        "Elegoo ASA", "https://us.elegoo.com/products/asa-filament-1-75mm-colored-1kg",
        density=1.09, tensile=(35, 30), elongation=(10.6, 9.4),
        flex_modulus=(2349, 2219), flex_modulus_sd=(102, 96), flex_strength=(65, 54),
        hdt_c=99, vicat_c=102, nozzle_c=(240, 270), bed_c=(90, 100)),
    "TPU": Material(
        "Elegoo TPU 95A", "https://us.elegoo.com/products/tpu-filament-1-75mm-colored-1kg",
        density=1.16, tensile=(16, 6), elongation=(500, 160),
        flex_modulus=None, flex_modulus_sd=None, flex_strength=None,
        hdt_c=None, vicat_c=None, nozzle_c=(220, 240), bed_c=(30, 45),
        elongation_at_least=True,
        note="модуля при изгибе Elegoo не даёт — MAT_E нет; удлинение — «больше»"),
}


def material_params(material: str) -> dict[str, dict]:
    """Параметры `MAT_*` материала — описаниями, как в params/base.yaml.

    Неизвестный материал — пусто: каталог не угадывает.
    """
    m = CATALOG.get(str(material).strip().upper())
    if m is None:
        return {}
    ref = f"{m.name}: {m.url} ({NO_STANDARD}; снято {ACCESSED}; {CAVEAT})"
    out: dict[str, dict] = {
        "MAT_DENSITY": {"value": m.density, "unit": "g/cm3", "provenance": "standard",
                        "ref": ref, "tags": ["material"]},
        "MAT_TENSILE_XY": {"value": m.tensile[0], "unit": "MPa", "provenance": "standard",
                           "ref": ref, "note": "прочность на растяжение, образец X-Y",
                           "tags": ["material"]},
        "MAT_TENSILE_Z": {"value": m.tensile[1], "unit": "MPa", "provenance": "standard",
                          "ref": ref, "note": "прочность на растяжение поперёк слоёв (Z)",
                          "tags": ["material"]},
        "MAT_ELONG_XY": {"value": m.elongation[0], "unit": "%", "provenance": "standard",
                         "ref": ref, "tags": ["material"],
                         "note": "удлинение при разрыве, X-Y"
                                 + (" — не меньше" if m.elongation_at_least else "")},
        "MAT_ELONG_Z": {"value": m.elongation[1], "unit": "%", "provenance": "standard",
                        "ref": ref, "tags": ["material"],
                        "note": "удлинение при разрыве, Z"
                                + (" — не меньше" if m.elongation_at_least else "")},
    }
    if m.flex_modulus is not None and m.flex_modulus_sd is not None:
        xy, z = m.flex_modulus
        sd_xy, sd_z = m.flex_modulus_sd
        out["MAT_E"] = {
            "value": xy, "unit": "MPa", "provenance": "assumed",
            "range": [round(z - sd_z, 6), round(xy + sd_xy, 6)], "tags": ["material"],
            "reason": (f"модуль ПРИ ИЗГИБЕ {m.name} ({xy:g}±{sd_xy:g} МПа X-Y, "
                       f"{z:g}±{sd_z:g} Z) вместо модуля при растяжении — его Elegoo не "
                       f"даёт (решение владельца); диапазон — от Z минус разброс до "
                       f"X-Y плюс разброс"),
            "source": ref}
    return out


def describe(material: str) -> str:
    """Строка для отчёта: что известно о пластике и откуда."""
    m = CATALOG.get(str(material).strip().upper())
    if m is None:
        return f"{material}: в каталоге материалов нет — свойства только из параметров проекта"
    parts = [f"{m.name}: плотность {m.density:g} г/см³",
             f"растяжение {m.tensile[0]:g}/{m.tensile[1]:g} МПа (X-Y/Z)"]
    if m.hdt_c is not None:
        parts.append(f"HDT {m.hdt_c:g} °C")
    parts.append(f"сопло {m.nozzle_c[0]:g}–{m.nozzle_c[1]:g} °C, стол "
                 f"{m.bed_c[0]:g}–{m.bed_c[1]:g} °C")
    return (", ".join(parts) + f" — {m.url}" + (f" ({m.note})" if m.note else "")
            + f". ВНИМАНИЕ: {CAVEAT}")
