"""Адаптер сховища в пам'яті. Використовується в тестах і для локального запуску."""
import copy

from fitshed.booking.domain import GroupClass, GroupClassNotFoundError, GroupClassRepository


class InMemoryGroupClassRepository(GroupClassRepository):
    """Зберігає копії агрегатів у словнику.

    Копіювання імітує справжню базу: зміни в отриманому об'єкті не потрапляють
    у сховище, доки не викликано save().
    """

    def __init__(self) -> None:
        self._items: dict[str, GroupClass] = {}

    def get(self, class_id: str) -> GroupClass:
        try:
            return copy.deepcopy(self._items[class_id])
        except KeyError:
            raise GroupClassNotFoundError(f"Заняття {class_id} не знайдено") from None

    def save(self, group_class: GroupClass) -> None:
        stored = copy.deepcopy(group_class)
        stored.pull_events()  # події не зберігаються разом з агрегатом
        self._items[group_class.id] = stored
