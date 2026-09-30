"""Thin Redfish client tuned for test code.

Design choices:
- Never raises on HTTP status. Negative tests need to inspect 4xx responses,
  so callers assert on ``resp.status_code`` explicitly.
- Retries only on *connection* failures (BMC rebooting, QEMU slirp hiccups),
  never on HTTP errors, so a flaky endpoint cannot hide behind retries.
- Supports both session (X-Auth-Token) and Basic auth, because privilege tests
  need several independent identities at once.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TASK_TERMINAL_STATES = {"Completed", "Exception", "Killed", "Cancelled", "Interrupted"}


class RedfishError(RuntimeError):
    pass


@dataclass
class RedfishClient:
    base_url: str
    username: str
    password: str
    verify_tls: bool = False
    timeout: float = 30.0
    _session: requests.Session = field(init=False, repr=False)
    _token: str | None = field(default=None, init=False, repr=False)
    _session_uri: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._session = requests.Session()
        self._session.verify = self.verify_tls
        retry = Retry(total=3, connect=3, read=0, status=0, backoff_factor=1.0)
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))

    # ------------------------------------------------------------------ auth
    def login(self) -> None:
        """Create a Redfish session and use its token for later requests."""
        resp = self._session.post(
            f"{self.base_url}/redfish/v1/SessionService/Sessions",
            json={"UserName": self.username, "Password": self.password},
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 201):
            raise RedfishError(f"login failed: HTTP {resp.status_code} {resp.text[:200]}")
        self._token = resp.headers.get("X-Auth-Token")
        self._session_uri = resp.headers.get("Location") or resp.json().get("@odata.id")
        if not self._token:
            raise RedfishError("login succeeded but no X-Auth-Token header was returned")
        self._session.headers["X-Auth-Token"] = self._token
        self._session.auth = None

    def use_basic_auth(self) -> None:
        self._session.headers.pop("X-Auth-Token", None)
        self._session.auth = (self.username, self.password)

    def logout(self) -> None:
        if self._session_uri:
            self.delete(self._session_uri)
        self._session.headers.pop("X-Auth-Token", None)
        self._token = self._session_uri = None

    # ------------------------------------------------------------ HTTP verbs
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base_url}{path}"

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        return self._session.request(method, self._url(path), **kwargs)

    def get(self, path: str, **kw: Any) -> requests.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> requests.Response:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> requests.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> requests.Response:
        return self.request("DELETE", path, **kw)

    # -------------------------------------------------------------- helpers
    def get_json(self, path: str) -> dict[str, Any]:
        resp = self.get(path)
        if resp.status_code != 200:
            raise RedfishError(f"GET {path}: HTTP {resp.status_code}")
        return resp.json()

    def members(self, collection_path: str) -> Iterator[dict[str, Any]]:
        """Yield every expanded member of a collection, following pagination."""
        next_link: str | None = collection_path
        while next_link:
            page = self.get_json(next_link)
            for ref in page.get("Members", []):
                yield self.get_json(ref["@odata.id"])
            next_link = page.get("Members@odata.nextLink")

    def wait_task(self, task_uri: str, timeout: float = 600, poll: float = 5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.get_json(task_uri)
            if task.get("TaskState") in TASK_TERMINAL_STATES:
                return task
            time.sleep(poll)
        raise TimeoutError(f"task {task_uri} did not finish within {timeout}s")

    def wait_until(self, predicate, timeout: float, poll: float = 5, what: str = "condition"):
        """Poll ``predicate()`` until it returns a truthy value, tolerating connection errors."""
        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except (requests.ConnectionError, requests.Timeout, RedfishError) as exc:
                last_exc = exc
            time.sleep(poll)
        raise TimeoutError(
            f"timed out after {timeout}s waiting for {what} (last error: {last_exc})"
        )


def extended_info(resp: requests.Response) -> list[dict[str, Any]]:
    """Return the ``@Message.ExtendedInfo`` entries of a Redfish error body, if any."""
    try:
        body = resp.json()
    except ValueError:
        return []
    return body.get("error", {}).get("@Message.ExtendedInfo", []) or body.get(
        "@Message.ExtendedInfo", []
    )


def message_ids(resp: requests.Response) -> list[str]:
    """Short MessageIds, e.g. ``Base.1.13.0.PropertyUnknown`` -> ``PropertyUnknown``."""
    return [m.get("MessageId", "").rsplit(".", 1)[-1] for m in extended_info(resp)]
