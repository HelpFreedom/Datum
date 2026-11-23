"""Граница доверия локального сервера.

Настоящая граница — привязка к петлевому интерфейсу: снаружи машины до сервера
не достучаться. Всё остальное здесь — защита в глубину от того, что уже внутри:
от страницы в браузере и от чужого процесса того же пользователя.

Четыре правила, каждое закрывает свой способ обойти петлевую привязку.

1. ПРОВЕРКА ЗАГОЛОВКА HOST. Страница с внешнего сайта может завести имя,
   которое разрешается в 127.0.0.1 (атака перепривязки DNS), и тогда браузер
   сочтёт наш сервер «своим» origin. Сравнивается ИМЯ, но не порт: для атаки
   нужно не-локальное имя, а игнорирование порта не ломает работу на нечётных
   портах. Отсутствующий Host пропускается: его не шлют клиенты HTTP/1.0, а
   браузер — то, ради чего правило и существует, — шлёт всегда.

2. ТОКЕН СЕССИИ на каждом запросе, кроме проверки живости. Пишется в файл
   рукопожатия с правами 0600, так что другой пользователь его не прочтёт.
   Сравнение через hmac.compare_digest: обычное сравнение строк выдаёт длину
   совпавшего префикса временем работы.

3. СОБСТВЕННЫЙ ЗАГОЛОВОК на всём, что меняет данные. Он превращает запрос из
   «простого» в требующий предварительной проверки CORS, на которую мы не
   отвечаем, — значит межсайтовая отправка формы до обработчика не дойдёт.

4. НИ ОДНОГО ЗАГОЛОВКА Access-Control-Allow-*. Именно их отсутствие заставляет
   правило одного источника работать за нас. Добавить их «чтобы фронтенд
   заработал» — значит снять всю защиту разом.

Модель заимствована у просмотрщика cadgen из рабочего процесса CAD: там она уже
отлажена на практике.
"""

from __future__ import annotations

import hmac
import re
import secrets
from dataclasses import dataclass, field

__all__ = ["ServerSecurity", "hostname_only", "host_is_allowed", "new_token",
           "CLIENT_HEADER", "TOKEN_HEADER", "ACTOR_HEADER", "ACTORS", "actor_of",
           "LOOPBACK_NAMES", "OPEN_PATHS",
           "ASSET_PATH", "path_is_open"]

CLIENT_HEADER = "x-datum-client"

#: Кто именно просит. Нужен не для охраны, а для ленты: человек должен видеть,
#: что из происходящего сделал он, а что — агент. Заголовок никому ничего не
#: разрешает, поэтому подделка им ничего не даёт.
ACTOR_HEADER = "x-datum-actor"
ACTORS = frozenset({"user", "agent", "external"})


def actor_of(headers) -> str:
    value = str(headers.get(ACTOR_HEADER, "") or "").strip().lower()
    return value if value in ACTORS else "external"

TOKEN_HEADER = "x-datum-token"

#: Имена, означающие «эта же машина». Используется в ДВУХ ролях —
#: допустимое значение заголовка Host и проверка адреса привязки, —
#: поэтому здесь только настоящие петлевые имена. В частности, НЕТ
#: «0.0.0.0»: это «все интерфейсы», а не петля, и если бы он тут лежал,
#: привязка к 0.0.0.0 считалась бы петлевой и отдавала 403 на каждый
#: запрос из сети — молчаливая ловушка вместо честного отказа.
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})

#: Пути без токена. Их всего два рода, и оба ничего не раскрывают.
#:
#: 1. Проверка живости.
#: 2. САМ БАНДЛ ОКНА. Токен нельзя добавить к переходу браузера на страницу —
#:    заголовков там взять неоткуда, — поэтому HTML и его файлы отдаются
#:    открыто. Секретов в них нет: токен окно получает не из страницы, а из
#:    preload Electron по IPC. Открытое в обычном браузере, окно просто мертво —
#:    оно не знает, чем подписать ни один запрос к /api.
#:
#: Окно живёт на ТОМ ЖЕ происхождении, что и сервер, и это не мелочь удобства.
#: Замер в живом Electron: окно на app:// не смогло выполнить ни одного запроса
#: к серверу — ни GET, ни WebSocket, — потому что сервер намеренно не отдаёт
#: заголовков Access-Control-*. Чтобы это обойти, понадобились бы ДВА
#: послабления: оболочка, подделывающая заголовки за сервер, и список
#: разрешённых Origin в самом сервере. Одно происхождение не требует ни одного.
OPEN_PATHS = frozenset({"/healthz", "/", "/index.html", "/favicon.ico"})

#: Файлы бандла. Имя нарочно узкое: ни косых черт, ни точек подряд, — поэтому
#: выйти за каталог через этот путь нельзя в принципе.
ASSET_PATH = re.compile(r"^/assets/[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)+$")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def path_is_open(path: str) -> bool:
    """Путь, доступный без токена."""
    return path in OPEN_PATHS or bool(ASSET_PATH.match(path or ""))


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hostname_only(host_header: str | None) -> str:
    """Имя из заголовка Host без порта. Понимает запись IPv6 в скобках."""
    value = str(host_header or "").strip()
    if value.startswith("["):
        end = value.find("]")
        return (value[1:end] if end != -1 else value).lower()
    index = value.rfind(":")
    if index != -1 and value[index + 1:].isdigit():
        return value[:index].lower()
    return value.lower()


def host_is_allowed(host_header: str | None) -> bool:
    """Защита от перепривязки DNS. Сравнивается имя, не порт.

    Правило безусловно. Настройки «сервер привязан не к петле, значит проверку
    можно снять» здесь сознательно нет: единственным её действием было бы
    ОТКЛЮЧЕНИЕ проверки, а `serve()` и так поднимается только на петле. Параметр,
    умеющий лишь ослабить охрану, — это риск без выгоды.
    """
    if not str(host_header or "").strip():
        return True                      # HTTP/1.0 без Host — не браузер
    return hostname_only(host_header) in LOOPBACK_NAMES


@dataclass(frozen=True, slots=True)
class ServerSecurity:
    token: str = field(default_factory=new_token)

    def token_ok(self, given: str | None) -> bool:
        if not given:
            return False
        # Сравниваем БАЙТЫ, а не строки. hmac.compare_digest на строках с
        # не-ASCII бросает TypeError, и присланный клиентом заголовок с
        # кириллицей давал бы 500 вместо честного отказа.
        try:
            a = given.encode("utf-8", "surrogatepass")
            b = self.token.encode("utf-8", "surrogatepass")
        except Exception:                          # pragma: no cover
            return False
        return hmac.compare_digest(a, b)

    def check(self, method: str, path: str, headers) -> tuple[bool, str]:
        """Пропустить запрос? Возвращает (можно, причина отказа)."""
        get = (lambda name: headers.get(name))

        if not host_is_allowed(get("host")):
            return False, (f"заголовок Host не локальный: {get('host')!r}. "
                           f"Похоже на перепривязку DNS")

        if path_is_open(path):
            return True, ""

        if not self.token_ok(get(TOKEN_HEADER)):
            return False, (f"нет или неверен заголовок {TOKEN_HEADER}. "
                           f"Токен выдаётся при запуске в файле рукопожатия")

        if method.upper() not in _SAFE_METHODS and get(CLIENT_HEADER) != "1":
            return False, (f"для изменяющих запросов обязателен заголовок "
                           f"{CLIENT_HEADER}: 1")

        return True, ""

    def origin_ok(self, origin: str | None) -> bool:
        """Источник соединения WebSocket. Пустой допускается: его не шлют
        не-браузерные клиенты, а браузер шлёт всегда."""
        if not origin:
            return True
        value = origin.split("://", 1)[-1]
        return hostname_only(value) in LOOPBACK_NAMES
