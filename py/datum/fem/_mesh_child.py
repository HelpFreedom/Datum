"""Мешер в отдельном процессе — для лестницы сеток (`tetmesh.tet_meshes`).

python -m datum.fem._mesh_child <вход.npz> <выход.npz> <ребро> <конверт> <потоков>
Вход — вершины и грани тела, выход — узлы и тетраэдры с временем. Своя команда, а
не multiprocessing: `spawn` заново исполняет главный модуль родителя (сервер,
сценарий), а `fork` после первого вызова мешера зависал (замерено).
"""

import sys

import numpy as np

from datum.fem.tetmesh import _tetrahedralize

if __name__ == "__main__":
    src, dst, edge, eps, threads = sys.argv[1:6]
    data = np.load(src)
    points, tets, ms = _tetrahedralize(np.ascontiguousarray(data["v"], np.float64),
                                       np.ascontiguousarray(data["f"], np.uint32),
                                       float(edge), float(eps), int(threads))
    np.savez(dst, points=points, tets=tets, ms=np.array(ms))
