"""Помилки домену «Розклад і запис»."""


class DomainError(Exception):
    """Базова помилка домену."""


class InvalidValueError(DomainError):
    """Значення не може існувати (наприклад, заняття закінчується раніше, ніж починається)."""


class InvariantViolationError(DomainError):
    """Операція порушила б бізнес-інваріант агрегату."""


class ForbiddenTransitionError(DomainError):
    """Операція не дозволена в поточному стані заняття."""


class GroupClassNotFoundError(DomainError):
    """Заняття з таким ідентифікатором немає в сховищі."""
