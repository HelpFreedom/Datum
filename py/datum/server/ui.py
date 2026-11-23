"""Раздача бандла окна.

Окно живёт на ТОМ ЖЕ происхождении, что и сервер, — иначе оно не может с ним
разговаривать вовсе. Замер в живом Electron: окно на `app://` не выполнило ни
одного запроса к серверу, ни GET, ни WebSocket, потому что сервер намеренно не
отдаёт заголовков `Access-Control-*`. Обход потребовал бы двух послаблений —
оболочки, подделывающей заголовки за сервер, и списка разрешённых `Origin` в
самом сервере. Одно происхождение не требует ни одного.

Файлы отдаются без токена: к переходу браузера на страницу заголовок не
приложишь. Секретов в бандле нет — токен окно получает из preload Electron по
IPC, поэтому открытое в обычном браузере окно просто мертво.

Читается ровно два вида путей: `/` и `/assets/<имя>`, причём имя проверяется
выражением без косых черт. Ни склейки путей из запроса, ни обхода каталога.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["ui_dir", "index_html", "asset_path", "NOT_BUILT", "MEDIA"]

MEDIA = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
    ".ico": "image/vnd.microsoft.icon",
}


def default_ui_dir() -> Path:
    """Собранный бандл рядом с исходниками: py/datum/… → ../../apps/ui/dist."""
    return Path(__file__).resolve().parents[3] / "apps" / "ui" / "dist"


def ui_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("DATUM_UI_DIR")
    return Path(env).resolve() if env else default_ui_dir()


def index_html(root: Path) -> Path | None:
    path = root / "index.html"
    return path if path.is_file() else None


def asset_path(root: Path, name: str) -> Path | None:
    """Файл бандла по имени. Имя уже проверено охраной; проверяем и здесь —
    страховка стоит одну строку, а ошибка в охране стоила бы каталога."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = (root / "assets" / name).resolve()
    try:
        path.relative_to((root / "assets").resolve())
    except ValueError:                             # pragma: no cover
        return None
    return path if path.is_file() else None


#: Что показать, пока бандл не собран. Отказ 404 здесь бесполезен: человек
#: увидел бы пустое окно и не узнал, чего не хватает.
NOT_BUILT = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Datum — окно не собрано</title>
<style>
 :root { color-scheme: dark }
 body { margin:0; height:100vh; display:grid; place-items:center;
        font:15px/1.6 ui-sans-serif,system-ui,sans-serif;
        background:#16181d; color:#c8ccd4 }
 main { max-width:36rem; padding:2rem }
 h1 { font-size:1.15rem; font-weight:600; color:#e6e9ef; margin:0 0 .75rem }
 code { background:#22252c; padding:.15rem .4rem; border-radius:4px; color:#8fd3a0 }
 pre { background:#22252c; padding:.8rem 1rem; border-radius:6px; overflow:auto }
 p { margin:.6rem 0 }
 .dim { color:#7d8492 }
</style></head><body><main>
<h1>Сервер работает, а окно ещё не собрано</h1>
<p>Бандл интерфейса не найден. Соберите его:</p>
<pre>npm run build</pre>
<p class="dim">Каталог, где сервер его ищет: <code>__DIR__</code>.
Другой путь задаётся ключом <code>--ui</code> или переменной
<code>DATUM_UI_DIR</code>.</p>
<p class="dim">Сам сервер при этом полностью работоспособен:
<code>/api</code>, <code>/ws</code> и <code>/obj</code> отвечают как обычно.</p>
</main></body></html>
"""
