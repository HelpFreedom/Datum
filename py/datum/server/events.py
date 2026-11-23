"""Шина событий сервера.

Через неё интерфейс узнаёт о происходящем: началась сборка, готова геометрия,
пришёл результат проверки. Требование «всё, что делает агент, видно сразу»
выполняется именно здесь — агент и человек ходят через один реестр операций,
а операции шлют события в одну шину.

Две особенности, без которых живое обновление врёт.

НУМЕРАЦИЯ И ПОВТОР. У каждого события свой номер, а последние события хранятся
в кольце. Клиент, переподключившись, называет последний виденный номер и
получает пропущенное. Без этого разрыв связи на секунду оставлял бы вьюпорт с
устаревшей геометрией, и человек этого не заметил бы.

КРУПНОЕ НЕ ХОДИТ ПО ШИНЕ. Меш в десятки мегабайт не отправляется в событии:
уходит только его отпечаток и ссылка. Содержимое клиент забирает отдельным
запросом и кеширует навсегда — отпечаток и есть ключ кеша.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["Event", "EventHub", "RING_SIZE"]

#: Сколько последних событий держим для повтора после разрыва связи.
RING_SIZE = 1000


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    kind: str
    data: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"seq": self.seq, "t": self.kind, "at": round(self.at, 3), **self.data}


class EventHub:
    """Рассылка событий подписчикам с кольцом для повтора."""

    #: Метка «лента кончилась». Кладётся в очередь каждого подписчика при
    #: остановке: иначе тот спит на `queue.get()` и не узнает, что пора уходить.
    CLOSED = object()

    def __init__(self, ring_size: int = RING_SIZE):
        self._seq = 0
        self._ring: deque[Event] = deque(maxlen=ring_size)
        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    # ------------------------------------------------------------ публикация
    def publish(self, kind: str, **data: Any) -> Event:
        """Опубликовать событие. Можно звать из любого места, включая не-async."""
        self._seq += 1
        event = Event(seq=self._seq, kind=kind, data=data)
        self._ring.append(event)
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:             # pragma: no cover
                # Подписчик не успевает. Ронять сервер из-за этого нельзя:
                # он нагонит повтором по номеру после переподключения.
                pass
        return event

    # ------------------------------------------------------------ подписка
    def subscribe(self, maxsize: int = 512) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Разбудить всех подписчиков и больше не принимать новых.

        Без этого сервер невозможно остановить, пока открыто окно: uvicorn при
        SIGTERM ждёт, когда закроются соединения, а обработчик WebSocket спит на
        `queue.get()` и ждать будет вечно. Проверено — сервер пережил SIGTERM и
        шесть секунд ожидания, и так накопились осиротевшие процессы.
        """
        self._closed = True
        for q in list(self._subs):
            try:
                q.put_nowait(self.CLOSED)
            except asyncio.QueueFull:             # pragma: no cover
                pass

    # ------------------------------------------------------------ повтор
    @property
    def seq(self) -> int:
        return self._seq

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def since(self, seq: int) -> tuple[list[Event], bool]:
        """События после указанного номера.

        Второе значение — «в кольце всё ещё есть то, что просили». Если оно
        False, клиент отстал больше, чем хранится: ему нужно перечитать
        состояние целиком, а не досылать разницу.
        """
        if not self._ring:
            return [], seq <= self._seq
        oldest = self._ring[0].seq
        complete = seq >= oldest - 1
        return [e for e in self._ring if e.seq > seq], complete

    def snapshot(self, limit: int = 50) -> list[dict]:
        return [e.as_dict() for e in list(self._ring)[-limit:]]
