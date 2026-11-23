"""Модель проекта и CLI — на примере `examples/latch`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datum.cli import main as cli
from datum.doc.project import Project, ProjectError

ROOT = Path(__file__).resolve().parents[3]
LATCH = ROOT / "examples" / "latch"


@pytest.fixture
def pr(latch):
    return Project.open(latch)


# ------------------------------------------------------------------ открытие
def test_project_loads_everything(pr):
    s = pr.summary()
    assert s["name"] == "latch"
    assert s["parts"] == ["корпус", "крышка"]
    assert s["checks"] == 14, ("семь из каталога, одна проектная, одна из порядка "
                               "сборки и пять базовых печатности (того вида, что "
                               "у детали не объявлен руками)")
    assert s["assembly"]["order"] == ["корпус", "крышка"]
    assert s["assembly"]["derived_checks"] == 1
    assert s["assembly"]["unlisted"] == [], "все детали проекта попали в порядок"
    assert s["guard"] == "checkpoint"
    assert pr.bed == (256.0, 256.0, 256.0)


def test_missing_directory_is_reported():
    with pytest.raises(ProjectError, match="нет каталога"):
        Project.open(ROOT / "examples" / "нет-такого")


def test_parts_do_not_leak_between_projects(pr, tmp_path):
    """Детали регистрируются глобально — набор обязан чиститься при загрузке."""
    empty = tmp_path / "пусто"
    (empty / "parts").mkdir(parents=True)
    other = Project.open(empty)
    assert other.parts == {}, "детали соседнего проекта не должны просочиться"
    assert set(pr.parts) == {"корпус", "крышка"}


# ------------------------------------------------------------------ сборка
def test_build_all_parts(pr):
    res = pr.build()
    assert all(r.ok for r in res.values()), [r.line() for r in res.values()]
    assert res["крышка"].topology.volume == pytest.approx(55.4 * 35.4 * 3.0, rel=1e-6)


def test_build_records_dependencies(pr):
    res = pr.build()
    # Корпус читает глубину губки, крышка — нет.
    assert "LIP_D" in res["корпус"].depends_on
    assert "LIP_D" not in res["крышка"].depends_on
    # Производные тянут за собой свои источники.
    assert "NOZZLE" in res["корпус"].depends_on


def test_build_of_unknown_part_is_reported(pr):
    with pytest.raises(ProjectError, match="нет таких деталей"):
        pr.build(["шестерня"])


def test_overrides_change_the_geometry(pr):
    thin = pr.build(["корпус"], overrides={"LIP_D": 0.3})["корпус"]
    thick = pr.build(["корпус"], overrides={"LIP_D": 1.5})["корпус"]
    assert thick.topology.volume > thin.topology.volume, "губка глубже — материала больше"


# ------------------------------------------------------------------ проверки
def test_all_checks_pass_on_the_example(pr):
    run = pr.run_checks()
    assert run.ok, run.report()
    assert len(run.results) == 18, ("пять направлений зацепа, шесть одиночных, "
                                    "защёлка из порядка сборки, усилие вставки и "
                                    "пять базовых печатности")


def test_declarative_ids_are_preserved(pr):
    ids = {r.id for r in pr.run_checks().results}
    assert "крышка.зацеп.+Z" in ids, "направления обязаны различаться"
    assert "корпус.без_пузырей" in ids
    assert "губка.против_зазора" in ids, "проектная проверка из custom.py"


def test_check_dependencies_include_the_parts_params(pr):
    """Проверка читает параметры не напрямую, а через детали — и это должно учитываться."""
    run = pr.run_checks()
    esc = next(r for r in run.results if r.id == "крышка.зацеп.+Z")
    assert "LIP_D" in esc.depends_on, "зацеп зависит от губки через корпус"
    assert "GAP" in esc.depends_on


def test_shallow_lip_breaks_retention(pr):
    """Подстановка нижнего края диапазона обязана обрушить удержание."""
    run = pr.run_checks(overrides={"LIP_D": 0.3})
    assert not run.ok
    ids = {r.id for r in run.failed}
    assert "крышка.зацеп.+Z" in ids
    assert "губка.против_зазора" in ids


# ------------------------------------------------------------------ чувствительность
def test_sweep_points_at_the_lip(pr):
    s = pr.sweep()
    # LIP_D решает сам по себе. Модуль и трение — только вместе: у жёсткого PLA
    # при большом трении усилие вставки уходит внутрь вилки. Трение появилось
    # параметром, когда его убрали из кода проверки, а совместный край нашёлся,
    # когда развёртка стала перебирать углы пар.
    assert s.critical_params == ("E_PLA", "FRICTION", "LIP_D")
    assert {j.params for j in s.joint if j.flips} == {("E_PLA", "FRICTION")}
    # Защёлка попала сюда сама: выведенная из порядка сборки проверка читает
    # те же параметры, что и деталь, и участвует в развёртке наравне с прочими.
    # Усилие вставки тоже зависит от глубины губки: от неё и отжатие, и скос.
    assert set(s.sensitive_checks("LIP_D")) == {
        "крышка.зацеп.+Z", "губка.против_зазора", "крышка.защёлка",
        "крышка.усилие_вставки"}


# ------------------------------------------------------------------ ошибки описания
def _mini(tmp_path: Path, checks_yaml: str) -> Path:
    root = tmp_path / "мини"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "checks").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "W: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('брусок')\ndef build():\n    return box(0, P.W, 0, P.W, 0, P.W)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(checks_yaml, encoding="utf-8")
    return root


def test_unknown_check_name_is_reported(tmp_path):
    """Сразу при открытии: иначе `info` отвечал «проверок: 1», а `check` падал."""
    root = _mini(tmp_path, "- id: x\n  use: такой_проверки_нет\n  args: {}\n")
    with pytest.raises(ProjectError, match="неизвестная проверка"):
        Project.open(root)


def test_duplicate_check_ids_are_refused(tmp_path):
    """Две проверки с одним id обе исполнялись, а в сравнении версий
    схлопывались в одну: сломавшаяся пряталась за прошедшей."""
    root = _mini(tmp_path, (
        '- id: a\n  use: single_body\n  args: {part: "@брусок"}\n'
        '- id: a\n  use: no_sealed_void\n  args: {part: "@брусок"}\n'))
    with pytest.raises(ProjectError, match="объявлена дважды"):
        Project.open(root)


def test_two_unnamed_checks_of_one_kind_need_ids(tmp_path):
    root = _mini(tmp_path, ('- use: single_body\n  args: {part: "@брусок"}\n'
                            '- use: single_body\n  args: {part: "@брусок"}\n'))
    with pytest.raises(ProjectError, match="single_body"):
        Project.open(root)


def test_reference_to_missing_part_is_reported(tmp_path):
    root = _mini(tmp_path, '- id: x\n  use: single_body\n  args: {part: "@нетути"}\n')
    run = Project.open(root).run_checks()
    bad = run.results[0]
    assert bad.status == "error" and "несуществующую деталь" in bad.message


def test_part_argument_without_at_sign_is_rejected(tmp_path):
    root = _mini(tmp_path, "- id: x\n  use: single_body\n  args: {part: брусок}\n")
    run = Project.open(root).run_checks()
    assert run.results[0].status == "error"
    assert "через «@имя»" in run.results[0].message


def test_cyrillic_param_reference_resolves(tmp_path):
    """Имена параметров бывают кириллические — ссылка обязана разбираться.

    Первая версия шаблона допускала только латиницу, и такая ссылка молча
    уезжала внутрь проверки строкой, падая там невнятным TypeError.
    """
    root = tmp_path / "кириллица"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "checks").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "СТОРОНА: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n"
        "СТЕНКА:  {value: 1.6, unit: mm, provenance: chosen, reason: '—'}\n",
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('брусок')\ndef build():\n"
        "    return box(0, P.СТОРОНА, 0, P.СТОРОНА, 0, P.СТОРОНА)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        '- id: брусок.стенка\n  use: min_wall\n'
        '  args: {part: "@брусок", wall: "${params.СТЕНКА}", samples: 6}\n',
        encoding="utf-8")
    run = Project.open(root).run_checks()
    assert run.results[0].status == "pass", run.results[0].message
    assert "СТЕНКА" in run.results[0].depends_on, "чтение параметра должно записаться"


def test_unresolved_reference_is_rejected(tmp_path):
    """Опечатка в виде ссылки («param» вместо «params») не должна уезжать молча."""
    root = _mini(tmp_path,
                 '- id: x\n  use: min_wall\n  args: {part: "@брусок", wall: "${param.W}"}\n')
    run = Project.open(root).run_checks()
    assert run.results[0].status == "error"
    assert "неразобранная ссылка" in run.results[0].message


def test_reference_to_missing_param_is_reported(tmp_path):
    root = _mini(tmp_path,
                 '- id: x\n  use: min_wall\n  args: {part: "@брусок", wall: "${params.НЕТУ}"}\n')
    run = Project.open(root).run_checks()
    assert run.results[0].status == "error" and "НЕТУ" in run.results[0].message


# ------------------------------------------------------------------ CLI
def test_cli_check_returns_zero_on_success(capsys, latch):
    assert cli(["check", str(latch)]) == 0
    assert "ИТОГ: OK" in capsys.readouterr().out


def test_cli_json_output_is_valid(capsys, latch):
    assert cli(["check", str(latch), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["n_fail"] == 0 and len(data["results"]) == 18


def test_cli_info_warns_about_unmeasured(capsys, latch):
    cli(["info", str(latch)])
    assert "НЕ ГОТОВ К ПЕЧАТИ" in capsys.readouterr().out


def test_cli_sweep(capsys, latch):
    cli(["sweep", str(latch)])
    out = capsys.readouterr().out
    assert "ИЗМЕРИТЬ В ПЕРВУЮ ОЧЕРЕДЬ" in out and "LIP_D" in out


def test_cli_build_json(capsys, latch):
    """JSON у всех операций одной формы: сама выдача плюс `report` для человека."""
    assert cli(["build", str(latch), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert set(data["parts"]) == {"корпус", "крышка"}
    assert all(v["ok"] for v in data["parts"].values())
    assert "report" in data


def test_cli_reports_missing_project(capsys, latch):
    """Ошибка — это код возврата и внятный текст, а не исключение.

    `main` должен быть вызываем как обычная функция: из тестов, из скрипта, из
    другой программы. SystemExit посреди библиотеки этому мешает.
    """
    # 3 — отказ операции; 2 — неверные аргументы (так отвечает argparse).
    assert cli(["info", str(ROOT / "examples" / "нет-такого")]) == 3
    assert "нет каталога проекта" in capsys.readouterr().err



# ------------------------------------------------------------------ datum init
def test_init_makes_an_empty_project_that_opens(capsys, tmp_path):
    """Новый проект — пустой (решение владельца): открывается, деталей и
    проверок нет, и прогон честно говорит «нечего проверять», а не «OK»."""
    root = tmp_path / "новый"
    assert cli(["init", str(root)]) == 0
    assert (root / "datum.toml").is_file() and (root / "parts").is_dir()
    assert not any((root / "parts").iterdir())
    pr = Project.open(root)
    assert pr.name == "новый" and pr.parts == {} and pr.check_decls == []
    assert pr.params.summary()["print_ready"]
    assert not pr.run_checks().ok
    # Путь к интерпретатору — рабочий, иначе `claude` в каталоге проекта не
    # увидит инструментов.
    mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert Path(mcp["mcpServers"]["datum"]["command"]).exists()


def test_a_part_by_bed_stands_in_the_middle_of_the_bed(cube):
    """Деталь, поставленная по `bed()`, — в центре стола, и стол берётся из datum.toml.

    Сменили стол — деталь обязана пересобраться, а не встать из кеша на
    прежнее место: стол входит в ключ кеша так же, как импортированные модели."""
    from datum.geom.measure import bbox

    root = cube
    lo, hi = bbox(Project.open(root).build(["кубик"])["кубик"].solid)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(128) and (lo[1] + hi[1]) / 2 == pytest.approx(128)
    assert lo[2] == pytest.approx(0)

    toml = root / "datum.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        "bed = [256, 256, 256]", "bed = [200, 300, 250]"), encoding="utf-8")
    lo, hi = bbox(Project.open(root).build(["кубик"])["кубик"].solid)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(100) and (lo[1] + hi[1]) / 2 == pytest.approx(150)


def test_bed_is_only_known_inside_a_build():
    from datum.build import NoParamsError, bed

    with pytest.raises(NoParamsError, match="стол принтера"):
        bed()


def test_the_hello_example_is_what_init_makes():
    """examples/hello — по устройству репозитория то, что создаёт `init`.
    Шаблон поменяли, а пример нет — и пример учит устаревшему.

    Сверяется то, что В РЕПОЗИТОРИИ: рабочая копия ИЛИ закоммиченная версия.
    В примере работают руками (импорт, правка параметров, удалённая деталь), и эти правки —
    не расхождение шаблона с примером, а чужие данные, которых тест не судит."""
    import subprocess

    from datum.doc.scaffold import _files

    repo = Path(__file__).resolve().parents[3]
    hello = repo / "examples" / "hello"

    def committed(rel: str) -> str | None:
        try:
            out = subprocess.run(["git", "show", f"HEAD:examples/hello/{rel}"], cwd=repo,
                                 capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout.decode("utf-8")

    for name, text in _files("hello").items():
        if name == ".mcp.json":
            continue                     # путь к интерпретатору — у каждого свой
        path = hello / name
        here = path.read_text(encoding="utf-8") if path.is_file() else None
        assert here == text or committed(name) == text, name
    # Шаблон пустой — и в примере деталей нет (в рабочей копии ИЛИ в коммите).
    assert not any((hello / "parts").glob("*.py")) or committed("parts/cube.py") is None


def test_an_untitled_project_opens_with_nothing_in_it(capsys, tmp_path):
    """С него открывается окно без проекта — так его создаёт оболочка."""
    root = tmp_path / "untitled-1"
    assert cli(["init", "--name", "Без названия", str(root)]) == 0
    capsys.readouterr()
    project = Project.open(root)
    assert project.name == "Без названия"
    assert project.parts == {} and project.check_decls == []
    assert (root / "parts").is_dir() and not any((root / "parts").iterdir())
    # Образец «угаданного» параметра в комментарии — он нужен и пустому проекту.
    assert "provenance: assumed" in (root / "params" / "base.yaml").read_text(encoding="utf-8")


def test_init_never_overwrites(capsys, tmp_path):
    root = tmp_path / "занято"
    (root / "params").mkdir(parents=True)
    assert cli(["init", str(root)]) == 3
    assert "уже похож на проект" in capsys.readouterr().err
    assert not (root / "datum.toml").exists()

    # Своё вне устройства проекта (заметки, .gitignore) тоже не трогается.
    other = tmp_path / "с заметками"
    other.mkdir()
    (other / "NOTES.md").write_text("моё", encoding="utf-8")
    assert cli(["init", str(other), "--name", "имя"]) == 0
    assert (other / "NOTES.md").read_text(encoding="utf-8") == "моё"
    assert Project.open(other).name == "имя"

# ------------------------------------------------------------ ориентация печати
def _oriented_project(tmp_path: Path, checks_yaml: str, print_up: str = "+X") -> Path:
    """Брусок 10×20×5: площадь прилипания различает ориентации."""
    root = tmp_path / "ориентация"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "W: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        f"@part('брусок', print_up='{print_up}')\n"
        "def build():\n    return box(0, P.W, 0, 2 * P.W, 0, P.W / 2)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(checks_yaml, encoding="utf-8")
    return root


def test_print_orientation_comes_from_the_part(tmp_path):
    """Проверка без `up` считает деталь в ориентации, объявленной у детали."""
    root = _oriented_project(tmp_path, (
        '- id: прилипание\n  use: bed_contact\n  args: {part: "@брусок", min_area: 1}\n'
        '- id: целостность\n  use: single_body\n  args: {part: "@брусок"}\n'))
    run = Project.open(root).run_checks()
    by_id = {r.id: r for r in run.results}
    contact = by_id["прилипание"]
    assert contact.value == pytest.approx(100.0)            # грань 20×5, а не 10×20
    assert "вверх +X" in contact.message
    # Проверка, которой ориентация ни к чему, её не получает — и не падает.
    assert by_id["целостность"].status == "pass"


def test_a_part_without_print_up_is_checked_as_built(tmp_path):
    root = _oriented_project(tmp_path, (
        '- id: прилипание\n  use: bed_contact\n  args: {part: "@брусок", min_area: 1}\n'),
        print_up="")
    contact = Project.open(root).run_checks().results[0]
    assert contact.value == pytest.approx(200.0) and "вверх +Z" in contact.message


def test_a_conflicting_up_in_the_check_is_refused(tmp_path):
    """Две разные ориентации одной детали — не выбор, а ошибка: какая верна,
    знает человек. Тихо предпочесть одну значило бы считать не ту деталь."""
    root = _oriented_project(tmp_path, (
        '- id: прилипание\n  use: bed_contact\n'
        '  args: {part: "@брусок", up: "+Z", min_area: 1}\n'))
    with pytest.raises(ProjectError, match="print_up='\\+X'"):
        Project.open(root).run_checks()


def test_the_same_up_in_the_check_is_accepted(tmp_path):
    root = _oriented_project(tmp_path, (
        '- id: прилипание\n  use: bed_contact\n'
        '  args: {part: "@брусок", up: "+X", min_area: 1}\n'))
    assert Project.open(root).run_checks().results[0].value == pytest.approx(100.0)


def test_the_same_up_in_another_spelling_is_accepted(tmp_path):
    root = _oriented_project(tmp_path, (
        '- id: прилипание\n  use: bed_contact\n'
        '  args: {part: "@брусок", up: " +x", min_area: 1}\n'))
    assert Project.open(root).run_checks().results[0].value == pytest.approx(100.0)


# ------------------------------------------------ страж на пути проверок
def _voided(tmp_path: Path, guard: str = "checkpoint") -> Path:
    root = tmp_path / "полость"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True)
    (root / "datum.toml").write_text(f'name = "полость"\n[build]\nguard = "{guard}"\n',
                                     encoding="utf-8")
    (root / "params" / "base.yaml").write_text(
        "W: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box, diff\n\n"
        "@part('куб')\ndef build():\n"
        "    return diff(box(0, P.W, 0, P.W, 0, P.W), [box(3, 7, 3, 7, 3, 7)])\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        '- id: куб.габарит\n  use: bbox_fits\n  args: {part: "@куб"}\n', encoding="utf-8")
    return root


def test_checks_build_parts_through_the_guard(tmp_path):
    """Куб с запаянной полостью: `build` его отвергает — и проверки обязаны тоже.

    Раньше `check` собирал детали мимо стража и печатал «ИТОГ: OK», а снимок
    объявлял такую деталь готовой к печати.
    """
    pr = Project.open(_voided(tmp_path))
    assert not pr.build()["куб"].ok
    run = pr.run_checks()
    assert not run.ok
    bad = run.results[0]
    assert bad.status == "error" and "полост" in bad.message


def _counted(tmp_path: Path) -> Path:
    """Проект, деталь которого считает свои сборки строчкой в файле."""
    root = tmp_path / "счёт"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True)
    (root / "datum.toml").write_text('name = "счёт"\n', encoding="utf-8")
    (root / "params" / "base.yaml").write_text(
        "W: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from pathlib import Path\n"
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('куб')\ndef build():\n"
        "    with open(Path(__file__).resolve().parents[1] / 'calls.txt', 'a') as f:\n"
        "        f.write('x')\n"
        "    return box(0, P.W, 0, P.W, 0, P.W)\n", encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        '- id: куб.габарит\n  use: bbox_fits\n  args: {part: "@куб"}\n'
        '- id: куб.одно\n  use: single_body\n  args: {part: "@куб"}\n',
        encoding="utf-8")
    return root


def _calls(root: Path) -> int:
    log = root / "calls.txt"
    return len(log.read_text()) if log.is_file() else 0


def test_checks_take_the_built_part_from_the_cache(tmp_path):
    """Проверки звали скрипт детали сами, мимо кеша сборки: `check_run` после
    `part_build` собирал заново, а скульптура полем упиралась в предел
    процессорного времени."""
    root = _counted(tmp_path)
    assert Project.open(root).build()["куб"].ok
    assert _calls(root) == 1
    run = Project.open(root).run_checks()
    assert run.ok, run.report()
    assert _calls(root) == 1, "проверки обязаны взять деталь из кеша сборки"


def test_checks_build_once_and_leave_the_part_in_the_cache(tmp_path):
    root = _counted(tmp_path)
    assert Project.open(root).run_checks().ok
    assert _calls(root) == 1, "две проверки одной детали — одна сборка"
    assert Project.open(root).build()["куб"].cached


def test_substituted_values_are_still_built_by_the_script(tmp_path):
    """Развёртка неизвестных подставляет значения — такой детали в кеше нет."""
    root = _counted(tmp_path)
    pr = Project.open(root)
    assert pr.build()["куб"].ok and _calls(root) == 1
    run = pr.run_checks(overrides={"W": 12.0})
    assert run.ok and _calls(root) == 2
    assert any("W" in r.depends_on for r in run.results), "зависимости не теряются"


def test_cached_part_still_reports_what_the_checks_read(tmp_path):
    root = _counted(tmp_path)
    Project.open(root).build()
    run = Project.open(root).run_checks()
    assert all("W" in r.depends_on for r in run.results)


def test_the_void_is_named_where_the_part_stands(tmp_path):
    """Деталь перенесена мышью — место полости в отказе там же, где её режет
    `section`: в положении из placement.yaml, а не в координатах скрипта."""
    root = _voided(tmp_path)
    (root / "placement.yaml").write_text(
        "куб: {move: [100, 0, 0], rotate: [0, 0, 0]}\n", encoding="utf-8")
    res = Project.open(root).build(use_cache=False)["куб"]
    assert not res.ok
    assert "у (105.00, 5.00, 5.00)" in str(res.error), str(res.error)


def _drafty(tmp_path: Path) -> Path:
    """Деталь, которая в черновике выходит другой: так видно, что взяли."""
    root = _counted(tmp_path)
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, draft, part\nfrom datum.geom import box\n\n"
        "@part('куб')\ndef build():\n"
        "    w = P.W / 2 if draft() else P.W\n"
        "    return box(0, w, 0, w, 0, w)\n", encoding="utf-8")
    return root


def test_a_draft_build_is_its_own_cache_entry(tmp_path):
    root = _drafty(tmp_path)
    rough = Project.open(root).build(draft=True)["куб"]
    assert rough.draft and rough.topology.volume == pytest.approx(125.0)
    assert "ЧЕРНОВИК" in rough.line()
    assert Project.open(root).build(draft=True)["куб"].cached
    exact = Project.open(root).build()["куб"]
    assert not exact.draft and not exact.cached, "точная сборка — не из черновика"
    assert exact.topology.volume == pytest.approx(1000.0)


def test_checks_never_see_a_draft(tmp_path):
    """Проверки собирают сами, обычной сборкой: числа не бывают черновыми."""
    root = _drafty(tmp_path)
    (root / "checks" / "checks.yaml").write_text(
        '- id: куб.габарит\n  use: bbox_fits\n  args: {part: "@куб"}\n', encoding="utf-8")
    pr = Project.open(root)
    pr.build(draft=True)
    run = pr.run_checks()
    assert run.ok
    assert "габарит 10.0×10.0×10.0 мм" in run.results[0].message, run.results[0].message


def test_draft_is_false_outside_a_build():
    from datum.build import draft

    assert draft() is False


def test_guard_off_in_the_manifest_is_respected_by_checks(tmp_path):
    root = _voided(tmp_path, guard="off")
    # Базовая no_sealed_void полость нашла бы честно — тест не о ней, а о страже.
    toml = root / "datum.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[checks]\nbaseline = false\n", encoding="utf-8")
    run = Project.open(root).run_checks()
    assert run.ok


@pytest.mark.parametrize("guard", ["bogus", "paranoid", "full"])
def test_an_unknown_guard_mode_is_refused_when_opening(tmp_path, guard):
    with pytest.raises(ProjectError, match="build.guard"):
        Project.open(_voided(tmp_path, guard=guard))


@pytest.mark.parametrize("bed", ["[256, 256]", "[0, 256, 256]", "'большой'"])
def test_a_broken_bed_is_refused_when_opening(tmp_path, bed):
    root = _voided(tmp_path)
    (root / "datum.toml").write_text(f"[printer]\nbed = {bed}\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="printer.bed"):
        Project.open(root)


def test_an_empty_directory_is_not_a_project(tmp_path):
    empty = tmp_path / "пусто"
    empty.mkdir()
    with pytest.raises(ProjectError, match="не проект Datum"):
        Project.open(empty)


# ------------------------------------------------ помощники lib/ и ключ кеша
def _with_lib(tmp_path: Path, name: str = "с_помощником", thickness: int = 1) -> Path:
    root = tmp_path / name
    for sub in ("parts", "params", "lib"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "datum.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    (root / "params" / "base.yaml").write_text(
        "W: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "lib" / "helpers.py").write_text(
        f"from datum.geom import box\n\ndef slab(w):\n    return box(0, w, 0, w, 0, {thickness})\n",
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom lib.helpers import slab\n\n"
        "@part('плита')\ndef build():\n    return slab(P.W)\n", encoding="utf-8")
    return root


def test_parts_import_their_helpers_from_lib(tmp_path):
    """Навык обещал, что lib/ «подхватывается сам», а импорт падал."""
    res = Project.open(_with_lib(tmp_path)).build(use_cache=False)["плита"]
    assert res.ok and res.topology.volume == pytest.approx(100.0)


def test_editing_a_helper_invalidates_the_build_cache(tmp_path):
    """Ключ кеша смотрел только на тело функции детали: правка помощника
    отдавала вчерашний меш."""
    root = _with_lib(tmp_path)
    first = Project.open(root).build()["плита"]
    assert first.topology.volume == pytest.approx(100.0) and not first.cached
    assert Project.open(root).build()["плита"].cached
    (root / "lib" / "helpers.py").write_text(
        "from datum.geom import box\n\ndef slab(w):\n    return box(0, w, 0, w, 0, 3)\n",
        encoding="utf-8")
    again = Project.open(root).build()["плита"]
    assert not again.cached and again.topology.volume == pytest.approx(300.0)


def test_a_change_in_datum_s_own_geometry_code_invalidates_the_cache(tmp_path, monkeypatch):
    """Ключ кеша не знал кода Datum: после правки `geom/field.py` деталь
    собиралась «из кеша» со старой геометрией."""
    import importlib

    # `datum.build.part` — и модуль, и функция в пакете: берём модуль.
    part_mod = importlib.import_module("datum.build.part")

    root = _with_lib(tmp_path)
    assert not Project.open(root).build()["плита"].cached
    assert Project.open(root).build()["плита"].cached
    monkeypatch.setattr(part_mod, "engine_digest", lambda: "0" * 16)
    assert not Project.open(root).build()["плита"].cached


def test_editing_a_module_constant_invalidates_the_build_cache(tmp_path):
    root = _with_lib(tmp_path)
    part = root / "parts" / "p.py"
    part.write_text("from datum.build import P, part\nfrom datum.geom import box\n\n"
                    "T = 1\n\n@part('плита')\ndef build():\n    return box(0, P.W, 0, P.W, 0, T)\n",
                    encoding="utf-8")
    assert Project.open(root).build()["плита"].topology.volume == pytest.approx(100.0)
    part.write_text(part.read_text(encoding="utf-8").replace("T = 1", "T = 2"), encoding="utf-8")
    assert Project.open(root).build()["плита"].topology.volume == pytest.approx(200.0)


def test_two_projects_in_one_process_do_not_share_lib(tmp_path):
    a = Project.open(_with_lib(tmp_path, "а", thickness=1)).build(use_cache=False)
    b = Project.open(_with_lib(tmp_path, "б", thickness=4)).build(use_cache=False)
    assert a["плита"].topology.volume == pytest.approx(100.0)
    assert b["плита"].topology.volume == pytest.approx(400.0)


def test_a_part_declared_twice_is_refused(tmp_path):
    root = _with_lib(tmp_path)
    (root / "parts" / "q.py").write_text(
        "from datum.build import part\nfrom datum.geom import box\n\n"
        "@part('плита')\ndef build():\n    return box(0, 1, 0, 1, 0, 1)\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="объявлена дважды"):
        Project.open(root)


def test_a_check_keeps_its_own_severity_unless_the_project_says_otherwise(tmp_path):
    """Умолчание «blocking» в проекте перебивало важность, заданную проверкой."""
    root = _mini(tmp_path, (
        '- id: нависания\n  use: overhang\n  args: {part: "@брусок"}\n'
        '- id: нависания_строго\n  use: overhang\n  args: {part: "@брусок"}\n'
        '  severity: blocking\n'))
    by_id = {r.id: r for r in Project.open(root).run_checks().results}
    assert by_id["нависания"].severity == "warn"
    assert by_id["нависания_строго"].severity == "blocking"


def test_an_unknown_severity_is_refused(tmp_path):
    root = _mini(tmp_path, ('- id: x\n  use: single_body\n  args: {part: "@брусок"}\n'
                            '  severity: critical\n'))
    with pytest.raises(ProjectError, match="severity"):
        Project.open(root).run_checks()


def test_a_declared_warning_warns_instead_of_blocking(tmp_path):
    """`severity: warn` прежде ни на что не влияло: проверка оставалась FAIL, и
    проект был «не готов» из-за объявленного предупреждения (Gear Cube:
    устойчивость статуэтки, опоры скалы). Теперь провал неблокирующей
    проверки — «ВНИМ»: виден, но печати не мешает."""
    root = _mini(tmp_path, (
        '- id: стол.строго\n  use: bed_contact\n  args: {part: "@брусок", min_area: 1000}\n'
        '- id: стол.мягко\n  use: bed_contact\n  args: {part: "@брусок", min_area: 1000}\n'
        '  severity: warn\n'
        '- id: стол.справка\n  use: bed_contact\n  args: {part: "@брусок", min_area: 1000}\n'
        '  severity: info\n'))
    run = Project.open(root).run_checks()
    by_id = {r.id: r for r in run.results}
    assert by_id["стол.строго"].status == "fail"
    assert by_id["стол.мягко"].status == "warn"
    assert by_id["стол.справка"].status == "warn"
    assert "ВНИМ" in by_id["стол.мягко"].line()
    assert [r.id for r in run.failed] == ["стол.строго"]


def test_a_blocking_declaration_makes_a_soft_failure_a_failure(tmp_path):
    """Обратное: `overhang` каталога отвечает «warn» сам; проект, объявивший
    его блокирующим, получает отказ, а не предупреждение с пометкой
    «блокирует печать»."""
    root = _mini(tmp_path, (
        '- id: нависания\n  use: overhang\n  args: {part: "@полка", max_frac: 0.01}\n'
        '- id: нависания_строго\n  use: overhang\n  args: {part: "@полка", max_frac: 0.01}\n'
        '  severity: blocking\n'))
    (root / "parts" / "polka.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box, union\n\n"
        "@part('полка')\ndef build():\n"
        "    return union([box(0, P.W, 0, P.W, 0, P.W), box(0, 3 * P.W, 0, P.W, P.W, P.W + 2)])\n",
        encoding="utf-8")
    by_id = {r.id: r for r in Project.open(root).run_checks().results}
    assert by_id["нависания"].status == "warn"
    assert by_id["нависания_строго"].status == "fail"


def test_a_project_check_with_warn_severity_does_not_block():
    """Правило одно и для проектных проверок (`checks/custom.py`)."""
    from datum.checks.result import CheckResult
    from datum.checks.runner import CheckSpec, run_checks
    from datum.doc.params import ParamRegistry

    def soft(_p):
        return CheckResult(id="своя", title="своя", status="fail", message="мало",
                           severity="warn")

    def broken(_p):
        raise RuntimeError("сломано")

    run = run_checks([CheckSpec("своя", soft), CheckSpec("сломанная", broken)],
                     ParamRegistry())
    by_id = {r.id: r for r in run.results}
    assert by_id["своя"].status == "warn"
    assert by_id["сломанная"].status == "error"          # ошибка не прячется
    assert not run.ok


# ------------------------------------------------ профиль принтера — в проверки
def _printer_project(tmp_path: Path, bed: str, check_args: str) -> Path:
    root = tmp_path / "принтер"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "datum.toml").write_text(f"[printer]\nbed = {bed}\n", encoding="utf-8")
    (root / "params" / "base.yaml").write_text(
        "W: {value: 200, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('плита')\ndef build():\n    return box(0, P.W, 0, 20, 0, 3)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        f'- id: плита.на_столе\n  use: bbox_fits\n  args: {{part: "@плита"{check_args}}}\n',
        encoding="utf-8")
    return root


def test_the_bed_comes_from_the_printer_profile(tmp_path):
    """Профиль принтера в datum.toml раньше не читал никто: стол переписывался
    руками в каждом checks.yaml, и смена принтера проверок не касалась."""
    big = Project.open(_printer_project(tmp_path, "[256, 256, 256]", "")).run_checks()
    assert big.results[0].status == "pass"
    small = Project.open(_printer_project(tmp_path / "2", "[180, 180, 180]", "")).run_checks()
    assert small.results[0].status == "fail" and "180×180×180" in small.results[0].message


def test_a_bed_contradicting_the_profile_is_refused(tmp_path):
    root = _printer_project(tmp_path, "[256, 256, 256]", ", bed: [300, 300, 300]")
    with pytest.raises(ProjectError, match="профиле принтера"):
        Project.open(root)


# ------------------------------------------------------- параметры, которых не читают
def test_a_parameter_nobody_reads_is_named(cube):
    """Объявлен, но не прочитан ни деталью, ни проверкой — почти всегда ошибка:
    правят его, а деталь не меняется."""
    base = cube / "params" / "base.yaml"
    base.write_text(base.read_text(encoding="utf-8")
                    + 'FORGOTTEN: {value: 3.0, unit: mm, provenance: chosen, reason: "забыт"}\n'
                    + 'ONLY_CHECK: {value: 1.2, unit: mm, provenance: chosen, reason: "порог"}\n',
                    encoding="utf-8")
    checks = cube / "checks" / "checks.yaml"
    checks.write_text(checks.read_text(encoding="utf-8")
                      + '\n- id: кубик.тонко\n  use: min_wall\n'
                        '  args: {part: "@кубик", wall: "${params.ONLY_CHECK}", samples: 4}\n',
                      encoding="utf-8")
    pr = Project.open(cube)
    run = pr.run_checks()
    # Читаемое только проверкой (через ${params.…}) — читается; SIZE, NOZZLE и
    # WALL читает деталь (WALL — производный от NOZZLE).
    assert pr.unread_params(run) == ["FORGOTTEN"]


def test_unread_parameters_are_not_judged_while_a_part_is_broken(cube):
    part = cube / "parts" / "cube.py"
    part.write_text(part.read_text(encoding="utf-8").replace("return diff", "raise RuntimeError('сломано') #"),
                    encoding="utf-8")
    pr = Project.open(cube)
    assert pr.unread_params(pr.run_checks()) is None


# ------------------------------------------------------- базовые проверки
def test_every_part_gets_baseline_printability_checks(cube):
    """Найдено обзором: пустой checks.yaml — и проект выгружался «готовым к
    печати» со стенкой 0.2 мм. Теперь у каждой детали есть базовые проверки
    печатности; объявленная руками того же вида перебивает базовую."""
    (cube / "checks" / "checks.yaml").write_text("", encoding="utf-8")
    pr = Project.open(cube)
    ids = {d["id"]: d for d in pr.derived_decls() if d.get("baseline")}
    assert set(ids) == {"кубик.стенка", "кубик.без_пузырей", "кубик.на_столе",
                        "кубик.нависания"}
    assert ids["кубик.стенка"]["args"]["wall"] == 0.4          # одна линия сопла
    assert Project.open(cube).run_checks().ok

    # Своя проверка того же вида — базовой нет.
    (cube / "checks" / "checks.yaml").write_text(
        '- id: моя.стенка\n  use: min_wall\n  args: {part: "@кубик", wall: 1.2}\n',
        encoding="utf-8")
    ids = {d["id"] for d in Project.open(cube).derived_decls() if d.get("baseline")}
    assert "кубик.стенка" not in ids and "кубик.нависания" in ids


def test_a_wall_thinner_than_a_line_is_not_ready_without_any_declared_check(cube):
    """Стенка 0.3 мм (тоньше линии сопла 0.4), checks.yaml пуст: прежде —
    ready: True; теперь базовая проверка не даёт."""
    import asyncio

    from datum.tools.ops import clear_project_cache, export

    (cube / "checks" / "checks.yaml").write_text("", encoding="utf-8")
    params = cube / "params" / "base.yaml"
    params.write_text(params.read_text(encoding="utf-8").replace(
        'WALL:   {provenance: derived, expr: "4 * NOZZLE", unit: mm}',
        'WALL:   {value: 0.3, unit: mm, provenance: chosen, reason: "проба"}'), encoding="utf-8")
    clear_project_cache()
    out = asyncio.run(export(project=str(cube), checks=True))
    assert not out.get("error"), out.get("error")
    assert out["ready"] is False, out.get("report")


def test_baseline_checks_can_be_switched_off_for_a_project(cube):
    toml = cube / "datum.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[checks]\nbaseline = false\n",
                    encoding="utf-8")
    assert not [d for d in Project.open(cube).derived_decls() if d.get("baseline")]


def _with_board_and_float(cube):
    """Кубик + покупная плата (printed=False, нарочно за краем стола) +
    поплавок с объявленной полостью."""
    (cube / "parts" / "extra.py").write_text(
        "from datum.build import centre_on_bed, part\n"
        "from datum.geom import Expect, box, diff\n\n"
        "@part('плата', printed=False)\n"
        "def board():\n    return box(300, 340, 0, 20, 0, 1.6)\n\n"
        "@part('поплавок', expect=Expect(allow_sealed_voids=True))\n"
        "def float_():\n"
        "    return centre_on_bed(diff(box(0, 10, 0, 10, 0, 10), [box(3, 7, 3, 7, 3, 7)]), dx=30)\n",
        encoding="utf-8")
    return cube


def test_a_part_that_is_not_printed_has_no_baseline_checks(cube):
    """Покупное и показное «напечатается ли» не проверяет; полость, которую
    деталь объявила намеренной, базовая проверка не судит (Gear Cube:
    статуэтка целиком с allow_sealed_voids краснела «три полости»)."""
    pr = Project.open(_with_board_and_float(cube))
    ids = {d["id"] for d in pr.derived_decls() if d.get("baseline")}
    assert not [i for i in ids if i.startswith("плата.")]
    assert "поплавок.без_пузырей" not in ids and "поплавок.стенка" in ids
    assert pr.summary()["not_printed"] == ["плата"]
    # В проверках плата участвует как любая деталь.
    (cube / "checks" / "checks.yaml").write_text(
        '- id: плата.одна\n  use: single_body\n  args: {part: "@плата"}\n', encoding="utf-8")
    run = Project.open(cube).run_checks()
    assert {r.id: r.status for r in run.results}["плата.одна"] == "pass"
    assert run.ok, run.report()


def test_export_and_build_skip_a_part_that_is_not_printed(cube):
    import asyncio

    from datum.tools.ops import clear_project_cache, export, part_build

    _with_board_and_float(cube)
    clear_project_cache()
    built = asyncio.run(part_build(project=str(cube)))
    assert built["ok"] and built["parts"]["плата"]["printed"] is False
    # Плата за краем стола — не повод предупреждать: на стол она не ляжет.
    assert not [w for w in built["bed_warnings"] if "плата" in w], built["bed_warnings"]

    out = asyncio.run(export(project=str(cube), checks=False, format="stl"))
    assert not out.get("error"), out.get("error")
    assert set(out["parts"]) == {"кубик", "поплавок"} and out["skipped"] == ["плата"]
    assert "не печатаются (printed=False), пропущены: плата" in out["report"]
    assert not (cube / "output" / "плата.stl").exists()

    only = asyncio.run(export(project=str(cube), parts=["плата"], checks=False))
    assert "выгружать нечего" in only["error"] and "плата" in only["error"]


def test_printed_must_be_a_bool():
    from datum.build.part import part

    with pytest.raises(ValueError, match="printed"):
        part("x", printed="нет")            # type: ignore[arg-type]


def test_a_formula_check_is_named_by_its_declaration(cube):
    """Найдено обзором: `крышка.юбка.деформация` печаталась «флексура:
    ПРЕВЫШЕН» — при двух листах в проекте не понять, какой. Имя — из id."""
    (cube / "checks" / "checks.yaml").write_text(
        "- id: крышка.юбка.деформация\n  use: beam_strain\n"
        "  args: {length: 6, thickness: 1.2, deflection: 0.5, limit: 0.02}\n",
        encoding="utf-8")
    run = Project.open(cube).run_checks()
    (r,) = [x for x in run.results if x.id == "крышка.юбка.деформация"]
    assert r.message.startswith("крышка.юбка"), r.message
