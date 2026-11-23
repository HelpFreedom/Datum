"""Операции Datum — единственное место, где они объявлены.

Отсюда строятся и MCP-инструменты для агента, и команды CLI для человека. Списки
не ведутся руками и потому не расходятся.

Соглашение: операция возвращает словарь. Ключ `report` — текст для человека,
остальное — машинные данные. Об ошибке сообщается ключом `error`, а не
исключением: агент должен получить внятный результат, а не оборванную сессию.

Сами операции лежат по темам в `ops_*.py`; этот модуль — их общий вход и
прежнее имя: `from datum.tools.ops import part_build` работает как раньше.
Порядок импорта ниже — порядок объявления операций (так они идут в справке
и в списке инструментов).
"""

from __future__ import annotations

from datum.tools.ops_base import (  # noqa: F401
    _PROJECTS, open_project, clear_project_cache, project_root_only, _err, _guard, Project,
    Plane, Face, Direction, datum_status,
)
from datum.tools.ops_doc import (  # noqa: F401
    project_info, part_place, _trash_dir, part_delete, trash_list, part_restore, project_save,
    project_unpack,
)
from datum.tools.ops_param import (  # noqa: F401
    params_list, param_set, _material_catalog_params, _measure_material,
)
from datum.tools.ops_build import (  # noqa: F401
    part_build, ExportFormat, export, _geom_event, _lesson_reminders, _printed_names,
    _skipped_line, _readiness, slice_parts,
)
from datum.tools.ops_check import (  # noqa: F401
    check_run, check_list, fem_verify, FEM_EDGE_RANGE, FEM_FORCE_MAX_N, fem_solve,
    _near_concave_edge, sweep_assumed,
)
from datum.tools.ops_snapshot import (  # noqa: F401
    snapshot_create, snapshot_list, snapshot_diff,
)
from datum.tools.ops_measure import (  # noqa: F401
    measure_parts, SEARCH_MIN_MM, measure_probe, _ring, section, Pose, part_view, _blob_rel,
    _below_cut, _print_limits, FOOTPRINT_CLEARANCE_MAX_MM, footprint_op, occupancy_map,
    MAP_CELLS_MAX,
)
from datum.tools.ops_cache import (  # noqa: F401
    cache_stats, cache_gc,
)
from datum.tools.ops_models import (  # noqa: F401
    FOREIGN_MESH_HEADROOM_MB, mesh_diagnose, _model_name, _store_model, import_model,
    import_to_bed, imported_list, imported_views, import_to_part, REPAIR_CLOSE_MAX,
    mesh_repair, fonts_list, font_add,
)
from datum.tools.ops_window import (  # noqa: F401
    critic_verdict, window_report, window_state, SHOW_NOTE_MAX, window_show, scene_view,
)

__all__ = ["open_project", "clear_project_cache"]

from datum.tools import ops_sim  # noqa: E402,F401
from datum.tools import ops_params  # noqa: E402,F401
from datum.tools import ops_script  # noqa: E402,F401
from datum.tools import ops_explain  # noqa: E402,F401
from datum.tools import ops_make  # noqa: E402,F401
from datum.tools import ops_gcode  # noqa: E402,F401
from datum.tools import ops_components  # noqa: E402,F401
from datum.tools import ops_guide  # noqa: E402,F401
from datum.tools import ops_lessons  # noqa: E402,F401
from datum.tools import ops_multi  # noqa: E402,F401
from datum.tools import ops_project  # noqa: E402,F401
from datum.tools import ops_sim_export  # noqa: E402,F401
