"""Імітація брокера з семантикою черги RabbitMQ для автоматичних тестів.

Відтворює лише те, що важливо для перевірок:
  - повідомлення залишається в брокері, доки споживач не надішле ACK;
  - якщо споживач «впав» до ACK, непідтверджене повідомлення повертається в чергу
    з ознакою redelivered = True;
  - reject_to_dead_letter переносить повідомлення в чергу помилок (як DLX у RabbitMQ).
Мережу, порядок між кількома споживачами й переповнення черги не моделює.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Message:
    body: bytes
    redelivered: bool = False


@dataclass
class InMemoryDelivery:
    broker: "InMemoryBroker"
    tag: int
    body: bytes
    redelivered: bool

    def ack(self) -> None:
        self.broker._unacked.pop(self.tag)

    def reject_to_dead_letter(self) -> None:
        message = self.broker._unacked.pop(self.tag)
        self.broker.dead_letter.append(message.body)


@dataclass
class InMemoryBroker:
    queue: deque = field(default_factory=deque)
    dead_letter: list = field(default_factory=list)
    _unacked: dict = field(default_factory=dict)
    _next_tag: int = 1

    def publish(self, body: bytes) -> bool:
        """Повертає True — аналог publisher confirm: брокер прийняв і зберіг повідомлення."""
        self.queue.append(_Message(body))
        return True

    def get(self) -> InMemoryDelivery | None:
        if not self.queue:
            return None
        message = self.queue.popleft()
        tag, self._next_tag = self._next_tag, self._next_tag + 1
        self._unacked[tag] = message
        return InMemoryDelivery(self, tag, message.body, message.redelivered)

    def consumer_connection_lost(self) -> None:
        """Споживач зник без ACK: брокер повертає всі непідтверджені повідомлення в чергу."""
        for tag in sorted(self._unacked, reverse=True):
            message = self._unacked.pop(tag)
            message.redelivered = True
            self.queue.appendleft(message)

    @property
    def unacked(self) -> int:
        return len(self._unacked)
