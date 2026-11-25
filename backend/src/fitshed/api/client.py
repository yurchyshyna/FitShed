"""Клієнт FitShed Booking API — споживач контракту.

Клієнт читає з відповідей лише ті поля, що записані у файлі очікувань
contracts/booking-client.expectations.json. Саме вони перевіряються контрактними тестами.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


@dataclass(frozen=True)
class ClassView:
    """Що клієнту потрібно знати про заняття."""

    class_id: str
    title: str
    status: str
    free_places: int
    booked: list[str]


@dataclass(frozen=True)
class BookingView:
    client_id: str
    result: str
    waitlist_position: int | None


class FitShedClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token  # access token користувача, від імені якого працює клієнт

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            problem = json.loads(error.read() or b"{}")
            raise ApiError(
                error.code, problem.get("code", "unknown"), problem.get("detail", "")
            ) from None

    @staticmethod
    def _class(data: dict) -> ClassView:
        return ClassView(
            data["class_id"],
            data["title"],
            data["status"],
            int(data["free_places"]),
            list(data["booked"]),
        )

    def create_class(
        self, title: str, trainer_id: str, start: str, end: str, capacity: int
    ) -> ClassView:
        return self._class(
            self._call(
                "POST",
                "/classes",
                {
                    "title": title,
                    "trainer_id": trainer_id,
                    "start": start,
                    "end": end,
                    "capacity": capacity,
                },
            )
        )

    def get_class(self, class_id: str) -> ClassView:
        return self._class(self._call("GET", f"/classes/{class_id}"))

    def book(self, class_id: str, client_id: str) -> BookingView:
        data = self._call(
            "POST", f"/classes/{class_id}/bookings", {"client_id": client_id}
        )
        return BookingView(
            data["client_id"], data["result"], data.get("waitlist_position")
        )

    def cancel_class(self, class_id: str, reason: str) -> ClassView:
        return self._class(
            self._call("POST", f"/classes/{class_id}/cancellation", {"reason": reason})
        )
