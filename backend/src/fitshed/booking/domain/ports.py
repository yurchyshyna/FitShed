"""Порти: операції, які потрібні домену від зовнішнього світу.

Домен лише оголошує, ЩО йому потрібно. ЯК це зроблено (пам'ять, PostgreSQL),
вирішують адаптери в пакеті adapters.
"""
from abc import ABC, abstractmethod

from .group_class import GroupClass


class GroupClassRepository(ABC):
    """Сховище агрегатів «Групове заняття»."""

    @abstractmethod
    def get(self, class_id: str) -> GroupClass:
        """Повертає заняття або кидає GroupClassNotFoundError."""

    @abstractmethod
    def save(self, group_class: GroupClass) -> None:
        """Зберігає новий або змінений агрегат цілком."""
