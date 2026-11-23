#!/usr/bin/env python3
"""Опись зависимостей: лицензии, вирусность, скрипты установки.

Запуск: `.venv/bin/python scripts/audit.py`

Проверяет три вещи, каждая из которых однажды кусала кого-нибудь:

1. ВИРУСНЫЕ ЛИЦЕНЗИИ. Метаданные пакета их не всегда показывают: shapely
   объявлен BSD, но везёт с собой GEOS под LGPL отдельной библиотекой. Поэтому
   отдельно осматриваются вшитые .so.
2. СКРИПТЫ УСТАНОВКИ в node_modules. Каждый такой скрипт — произвольный код,
   выполняемый при установке. Политика проекта их запрещает (.npmrc), и список
   исключений должен оставаться коротким и знакомым.
3. НЕОПОЗНАННЫЕ лицензии — их надо разобрать руками, а не молча пропустить.
4. ИЗВЕСТНЫЕ УЯЗВИМОСТИ обеих половин дерева: `npm audit` и `pip-audit`.
   Лицензия и скрипт установки говорят о происхождении пакета, а это — о его
   состоянии сегодня; одно другого не заменяет.

Уязвимости требуют сети, и она здесь ходит через прокси. Адрес берётся из
DATUM_PROXY (то же имя, что у оболочки), по умолчанию http://127.0.0.1:10808.
Недоступность сети НЕ роняет опись, но и не выдаётся за успех. Коды возврата:
0 — чисто и полно, 1 — есть находки, 2 — отчёт неполон (что-то не проверено).
Молчаливое «уязвимостей нет» из-за оборванной сети было бы хуже любого
найденного CVE, поэтому отдельный код, а не ноль.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter
from importlib import metadata

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Что считаем требующим внимания при распространении. Для личного
#: использования ни то ни другое обязательств не создаёт.
COPYLEFT = re.compile(r"\bA?GPL|GNU General|GNU Affero", re.I)
#: Границы слов обязательны: «Intel SiMPLified Software License» (MKL) без них
#: попадала в MPL.
WEAK_COPYLEFT = re.compile(r"LGPL|\bLesser\b|\bMPL\b|\bEPL\b|\bCDDL\b", re.I)

#: Лицензии вшитых нативных библиотек. В метаданных пакета их НЕТ: shapely
#: объявлен BSD, а везёт GEOS под LGPL; scipy объявлен BSD, а везёт libgfortran
#: под GPL-3. Поэтому таблица ведётся руками — и именно здесь всплывает то,
#: что автоматический обход не видит.
NATIVE_LICENCES = {
    "libgeos": ("LGPL-2.1", "динамическая линковка; при распространении LGPL это допускает"),
    "libgeos_c": ("LGPL-2.1", "то же"),
    # GMP приехал с pytetwild (мешер fTetWild): точная арифметика нужна ему,
    # чтобы предикаты ориентации не врали на почти вырожденных тетраэдрах.
    "libgmp": ("LGPL-3.0-or-later OR GPL-2.0-or-later",
               "двойная лицензия; берём LGPL, динамическая линковка её допускает"),
    "libgfortran": ("GPL-3.0 WITH GCC-exception-3.1",
                    "исключение для рантайма GCC снимает требование открывать код"),
    "libquadmath": ("GPL-3.0 WITH GCC-exception-3.1", "то же"),
    "libscipy_openblas": ("BSD-3-Clause", ""),
    "libscipy_openblas64_": ("BSD-3-Clause", ""),
    "libXau": ("MIT", ""),
    "libavif": ("BSD-2-Clause", ""),
    "libspatialindex": ("MIT", ""),
    "liblcms2": ("MIT", ""),
    "liblzma": ("0BSD", ""),
    "libopenjp2": ("BSD-2-Clause", ""),
    "libpng16": ("libpng (разрешительная)", ""),
    "libsharpyuv": ("BSD-3-Clause", ""),
    "libtiff": ("libtiff (разрешительная)", ""),
    "libwebp": ("BSD-3-Clause", ""),
    "libwebpdemux": ("BSD-3-Clause", ""),
    "libwebpmux": ("BSD-3-Clause", ""),
    "libxcb": ("MIT", ""),
    "libzstd": ("BSD-3-Clause OR GPL-2.0", "разрешительная половина применима"),
    "libjpeg": ("IJG (разрешительная)", ""),
    "libbrotlicommon": ("MIT", ""),
    "libbrotlidec": ("MIT", ""),
    "libbrotlienc": ("MIT", ""),
    "libfreetype": ("FTL или GPL-2.0", "разрешительная половина применима"),
    "libharfbuzz": ("MIT", ""),
    "libopenblas": ("BSD-3-Clause", ""),
    "libgomp": ("GPL-3.0 WITH GCC-exception-3.1", "исключение для рантайма GCC"),
}


def native_licence(filename: str) -> tuple[str, str]:
    """Имя вида «libgeos-3ef06f11.so.3.13.1» → лицензия."""
    stem = filename.split("/")[-1]
    stem = stem.split(".so")[0]
    stem = re.sub(r"-[0-9a-f]{6,}.*$", "", stem)
    return NATIVE_LICENCES.get(stem, ("?", "разобрать руками"))


#: Пакеты, которым скрипты установки позволены явно, и зачем.
ALLOWED_SCRIPTS = {
    "esbuild": "докачивает бинарник под платформу",
    "node-pty": "собирает нативный модуль (N-API, пересборка под Electron не нужна)",
}


def python_licences() -> tuple[Counter, list[tuple[str, str]]]:
    counter: Counter = Counter()
    flagged: list[tuple[str, str]] = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name or name == "datum":
            continue
        licence = (dist.metadata.get("License-Expression")
                   or dist.metadata.get("License") or "")
        if not licence or len(licence) > 60:
            classifiers = [c for c in (dist.metadata.get_all("Classifier") or [])
                           if c.startswith("License ::")]
            licence = classifiers[-1].split("::")[-1].strip() if classifiers else "?"
        licence = re.sub(r"\s+", " ", licence).strip()
        counter[licence] += 1
        if COPYLEFT.search(licence) or WEAK_COPYLEFT.search(licence):
            flagged.append((name, licence))
    return counter, flagged


def js_packages():
    root = ROOT / "node_modules"
    if not root.is_dir():
        return Counter(), [], []
    counter: Counter = Counter()
    flagged: list[tuple[str, str]] = []
    scripts: list[tuple[str, str, str]] = []
    for path in root.rglob("package.json"):
        parts = path.relative_to(root).parts
        # Только сами пакеты: верхний уровень и область (@scope/имя).
        if len(parts) > 2 or path.parent.name == "node_modules":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        licence = data.get("license") or "?"
        if isinstance(licence, dict):
            licence = licence.get("type", "?")
        counter[str(licence)] += 1
        if COPYLEFT.search(str(licence)) or WEAK_COPYLEFT.search(str(licence)):
            flagged.append((data.get("name", path.parent.name), str(licence)))
        for hook in ("preinstall", "install", "postinstall"):
            command = (data.get("scripts") or {}).get(hook)
            if command:
                scripts.append((data.get("name", path.parent.name), hook, command))
    return counter, flagged, scripts


def bundled_native() -> list[str]:
    """Вшитые библиотеки: их лицензия в метаданных пакета не видна."""
    found = []
    site = pathlib.Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    for libs in sorted(site.glob("*.libs")):
        for lib in sorted(libs.iterdir()):
            found.append(f"{libs.name}/{lib.name}")
    return found


def _proxy_env() -> dict:
    """Окружение для сетевых проверок: прокси наружу, петля — напрямую."""
    proxy = os.environ.get("DATUM_PROXY", "http://127.0.0.1:10808")
    return {**os.environ, "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
            "http_proxy": proxy, "https_proxy": proxy,
            "NO_PROXY": "127.0.0.1,localhost,::1"}


def _почему(code: int, err: str, программа: str) -> str:
    """Внятная причина неудачи. Пустой строки тут быть не должно: «НЕ ПРОВЕРЕНО — »
    без объяснения — это ровно то молчание, от которого раздел и заводился."""
    хвост = [ln for ln in err.strip().splitlines() if ln.strip()]
    return хвост[-1][:300] if хвост else f"{программа} вернул {code}, ничего не сказав"


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=_proxy_env(), timeout=timeout,
                           capture_output=True, text=True)
    except FileNotFoundError:
        return -1, "", f"нет программы {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"{cmd[0]}: не ответил за {timeout} с"
    return r.returncode, r.stdout, r.stderr


def js_vulnerabilities() -> tuple[dict | None, str]:
    """`npm audit`. Возвращает счётчик по уровням либо причину, почему нет."""
    code, out, err = _run(["npm", "audit", "--json"])
    if not out.strip():
        return None, _почему(code, err, "npm audit")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return None, f"ответ npm не разобрался: {e}"
    # Без сети npm отдаёт ПРАВДОПОДОБНЫЙ json, где error.summary и error.detail
    # ПУСТЫ, а настоящая причина лежит в message. Читать только summary значило
    # бы напечатать «НЕ ПРОВЕРЕНО — » без причины; так и было.
    if "error" in data or "message" in data:
        причина = (str(data.get("message") or "").strip()
                   or str(data.get("error", {}).get("summary") or "").strip()
                   or str(data.get("error", {}).get("detail") or "").strip())
        return None, причина[:300] or "npm audit отказал, не назвав причины"
    # Отчёт БЕЗ блока метаданных — это не «нулей нет», это «ничего не сказано».
    # Считать его чистым нельзя: так тишина превращается в зелёную галочку.
    meta = data.get("metadata", {}).get("vulnerabilities")
    if not isinstance(meta, dict) or "total" not in meta:
        return None, "в ответе npm нет сводки metadata.vulnerabilities"
    return dict(meta), ""


def python_vulnerabilities() -> tuple[list[tuple[str, str, str]] | None, str]:
    """`pip-audit`. Возвращает список (пакет, версия, идентификаторы)."""
    venv_pip_audit = ROOT / ".venv" / "bin" / "pip-audit"
    cmd = [str(venv_pip_audit)] if venv_pip_audit.is_file() else ["pip-audit"]
    code, out, err = _run([*cmd, "--format", "json", "--progress-spinner", "off"])
    if not out.strip():
        return None, _почему(code, err, "pip-audit")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return None, f"ответ pip-audit не разобрался: {e}"
    found = []
    SKIPPED.clear()
    for dep in data.get("dependencies", []):
        # Пакет, который pip-audit пропустил (не нашёл на PyPI, локальный), —
        # НЕ проверен, а не чист.
        if dep.get("skip_reason"):
            if dep.get("name") != "datum":
                SKIPPED.append(f"{dep.get('name', '?')} ({dep['skip_reason'][:60]})")
            continue
        vulns = dep.get("vulns") or []
        if vulns:
            found.append((dep.get("name", "?"), dep.get("version", "?"),
                          ", ".join(v.get("id", "?") for v in vulns)))
    return found, ""


#: Пакеты, которые pip-audit пропустил в последнем прогоне.
SKIPPED: list[str] = []


def loose_native() -> list[str]:
    """Нативные библиотеки ВНЕ каталогов `*.libs`: так лежат MKL, TBB,
    intel-openmp (пришли с pypardiso) и libmujoco. Модули расширения Python
    (`*.cpython-*.so`, `*.abi3.so`) сюда не входят — это код самого пакета."""
    site = pathlib.Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    roots = [site, pathlib.Path(sys.prefix) / "lib"]
    found = set()
    for root in roots:
        pattern = "**/*.so*" if root == site else "*.so*"
        for lib in root.glob(pattern):
            name = lib.name
            if ".cpython-" in name or ".abi3." in name or not lib.is_file():
                continue
            if any(part.endswith(".libs") for part in lib.parts):
                continue
            rel = lib.relative_to(root)
            owner = rel.parts[0] if len(rel.parts) > 1 else "lib"
            found.add(f"{owner}/{name}")
    return sorted(found)


def lock_drift() -> tuple[list[str], list[str], list[str]]:
    """Окружение против замка: лишнее, недостающее, другой версии.

    Замок описывает среду, на которой всё проверено; окружение, которое от
    него отличается, — уже другая среда. Так нашёлся `sniffio`: стоял в
    окружении, хотя его не требовал ни один пакет, — хвост прежней установки.
    Сам по себе безвреден, но отчёт «ставится ровно замок» был бы неправдой.
    """
    import importlib.metadata as md

    def norm(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name.lower())

    lock = (ROOT / "py" / "requirements.lock").read_text(encoding="utf-8")
    locked = {norm(m.group(1)): m.group(2)
              for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==([^\s\\;]+)", lock, re.M)}
    installed = {norm(d.metadata["Name"]): d.version for d in md.distributions()}
    own = {"datum", "pip", "setuptools", "wheel"}
    extra = sorted(n for n in installed if n not in locked and n not in own)
    missing = sorted(n for n in locked if n not in installed)
    other = sorted(f"{n} {installed[n]} (в замке {v})" for n, v in locked.items()
                   if n in installed and installed[n] != v)
    return extra, missing, other


def main() -> int:
    rogue_scripts = 0
    unresolved = 0

    print("== Окружение против замка ==")
    extra, missing, other = lock_drift()
    drift = len(extra) + len(missing) + len(other)
    print(f"  лишнее: {', '.join(extra) or 'нет'}")
    print(f"  недостаёт: {', '.join(missing) or 'нет'}")
    print(f"  другой версии: {', '.join(other) or 'нет'}")
    print()

    print("== Python: лицензии ==")
    counter, flagged = python_licences()
    for licence, n in counter.most_common():
        print(f"  {n:3}  {licence}")
    if flagged:
        print("  требуют внимания:")
        for name, licence in sorted(flagged):
            print(f"    {name}: {licence}")

    print("\n== JavaScript: лицензии ==")
    js_counter, js_flagged, scripts = js_packages()
    for licence, n in js_counter.most_common():
        print(f"  {n:3}  {licence}")
    if js_flagged:
        print("  требуют внимания:")
        for name, licence in sorted(set(js_flagged)):
            print(f"    {name}: {licence}")

    print("\n== Вшитые нативные библиотеки ==")
    print("  (их лицензия в метаданных пакета НЕ видна)")
    unknown_native = []
    native_copyleft = []
    for lib in bundled_native():
        licence, note = native_licence(lib)
        if licence == "?":
            unknown_native.append(lib)
            unresolved += 1
        if COPYLEFT.search(licence) or WEAK_COPYLEFT.search(licence):
            native_copyleft.append((lib, licence, note))
        print(f"  {lib:52} {licence}")
    if native_copyleft:
        print("\n  копилефт среди них:")
        for lib, licence, note in native_copyleft:
            print(f"    {lib.split('/')[-1]}: {licence}"
                  + (f" — {note}" if note else ""))
    if unknown_native:
        print("\n  НЕ РАЗОБРАНЫ (дополните NATIVE_LICENCES):")
        for lib in unknown_native:
            print(f"    {lib}")

    print("\n== Скрипты установки в node_modules ==")
    if not scripts:
        print("  нет ни одного")
    for name, hook, command in sorted(scripts):
        note = ALLOWED_SCRIPTS.get(name)
        mark = "разрешён" if note else "НЕ В СПИСКЕ"
        if not note:
            rogue_scripts += 1
        print(f"  {name} [{hook}] — {mark}{': ' + note if note else ''}")
        print(f"      {command[:80]}")

    npmrc = (ROOT / ".npmrc").read_text(encoding="utf-8") if (ROOT / ".npmrc").is_file() else ""
    if "ignore-scripts=true" not in npmrc:
        print("\n  ОТКАЗ: в .npmrc не запрещены скрипты установки")
        rogue_scripts += 1

    print("\n== Известные уязвимости ==")
    уязвимых = 0
    непроверено = []

    js_vulns, js_why = js_vulnerabilities()
    if js_vulns is None:
        print(f"  JavaScript: НЕ ПРОВЕРЕНО — {js_why}")
        непроверено.append("npm audit")
    else:
        всего = sum(v for k, v in js_vulns.items() if k != "total")
        опасных = sum(js_vulns.get(k, 0) for k in ("high", "critical"))
        средних = sum(js_vulns.get(k, 0) for k in ("moderate", "low"))
        уязвимых += опасных
        print(f"  JavaScript: {всего or 'нет'}"
              + (f" — {', '.join(f'{k}: {v}' for k, v in js_vulns.items() if v and k != 'total')}"
                 if всего else ""))

    py_vulns, py_why = python_vulnerabilities()
    if py_vulns is None:
        print(f"  Python: НЕ ПРОВЕРЕНО — {py_why}")
        непроверено.append("pip-audit")
    elif not py_vulns:
        print("  Python: нет")
    else:
        уязвимых += len(py_vulns)
        for name, version, ids in py_vulns:
            print(f"    {name} {version}: {ids}")

    if SKIPPED:
        print(f"  Python: pip-audit пропустил {len(SKIPPED)}: {', '.join(SKIPPED[:8])}")
        непроверено.append(f"пакеты, пропущенные pip-audit ({len(SKIPPED)})")
    вне = loose_native()
    if вне:
        print("\n== Нативные библиотеки вне *.libs (лицензии не описаны) ==")
        owners: dict = {}
        for item in вне:
            owners.setdefault(item.split("/")[0], []).append(item.split("/", 1)[1])
        for owner, libs in sorted(owners.items()):
            print(f"  {owner}: {len(libs)} — {', '.join(libs[:4])}" + (" …" if len(libs) > 4 else ""))
        непроверено.append(f"лицензии нативных библиотек вне *.libs ({len(вне)})")
    без_лицензии = counter.get("?", 0) + js_counter.get("?", 0)
    if без_лицензии:
        непроверено.append(f"пакеты с неопознанной лицензией ({без_лицензии})")

    print("\n== Итог ==")
    print("  вирусных лицензий в пакетах (GPL/AGPL): "
          + (", ".join(n for n, l in flagged + js_flagged if COPYLEFT.search(l)) or "нет"))
    print("  слабо-копилефтных: "
          + (", ".join(sorted({n for n, l in flagged + js_flagged
                               if WEAK_COPYLEFT.search(l)})) or "нет"))
    print(f"  чужих скриптов установки: {rogue_scripts}")
    print(f"  неразобранных нативных библиотек: {unresolved}")
    print(f"  опасных уязвимостей: {уязвимых}")
    if js_vulns is not None and средних:
        print(f"  уязвимостей средней и низкой тяжести (JavaScript): {средних} — в код "
              f"возврата НЕ идут; решение по каждой — в docs/security.md")
    print(f"  расхождений окружения с замком: {drift}")
    if непроверено:
        print(f"  НЕ ПРОВЕРЕНО: {', '.join(непроверено)} — отчёт неполон")
    if rogue_scripts or unresolved or уязвимых or drift:
        return 1
    return 2 if непроверено else 0


if __name__ == "__main__":
    raise SystemExit(main())
