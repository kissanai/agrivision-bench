"""Adapter for a diagnosis service that speaks its own HTTP shape.

Most real systems are not a chat completions endpoint. They are a service
somebody built, with its own upload convention and its own response body::

    {"status": "ok",
     "result": {"crop": "tomato",
                "top": {"label": "late blight", "kind": "disease", "p": 0.91}},
     "alternatives": [...]}

Rather than asking every such team to write Python, this adapter posts the
image and reads the answer out of the response with **dotted paths**::

    python -m agribench run --adapter http \\
      -O url=https://api.example.com/diagnose \\
      -O map='{"subject": "result.crop",
               "primary_issue": "result.top.label",
               "primary_category": "result.top.kind",
               "answered": "result.top.p"}' \\
      -O answered_threshold=0.55

A path is dot-separated; integer segments index into lists
(``alternatives.0.label``). A path that does not resolve yields ``None``, which
becomes an empty string -- a missing optional field is not an error.

Three upload shapes cover essentially everything, chosen with ``payload=``:

``multipart``
    ``multipart/form-data`` with the image under ``file_field`` (default
    ``image``). The default, and what most upload endpoints expect.
``binary``
    The raw image bytes as the body, with the image's own content type.
``json_base64``
    ``{"image": "<base64>"}``, plus any extra ``fields``, as JSON.

Configuration and secrets
-------------------------
``BENCH_ENDPOINT_URL`` supplies the URL and ``BENCH_ENDPOINT_TOKEN`` becomes an
``Authorization: Bearer`` header, so no secret needs to appear on a command
line. The token is scrubbed from error text and :meth:`describe` reports only
that it was present.

Deciding ``answered``
---------------------
This adapter fronts real systems, and real systems are where abstention gets
interesting. Three ways to express it, in precedence order:

1. ``answered`` maps to a boolean field -- the system says so itself. Best.
2. ``answered`` maps to a numeric confidence and ``answered_threshold`` is set
   -- the gate is applied here. Record the threshold; it is part of the result.
3. Neither is mapped -- fall back to the shared rule, that a
   ``primary_category`` which is missing, empty, or ``"unknown"`` means
   declined.

Do not use option 2 to tune a system's precision after seeing its score. That
turns a measurement into a fit. If a threshold is not the one the system ships
with, say so when publishing.

Stdlib only: :mod:`urllib.request` plus a hand-rolled multipart encoder.
"""

from __future__ import annotations

import base64
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from agribench.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterResponseError,
    Case,
    Prediction,
    TransientAdapterError,
    is_answered_category,
)

__all__ = ["HTTPEndpointAdapter", "resolve_path"]

#: Field-by-field defaults: assume the service already speaks the frozen names.
DEFAULT_FIELD_MAP: dict[str, str] = {
    "subject": "subject",
    "subject_type": "subject_type",
    "primary_issue": "primary_issue",
    "primary_category": "primary_category",
    "secondary_issues": "secondary_issues",
}

_PAYLOAD_MODES = frozenset({"multipart", "binary", "json_base64"})
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})
_AUTH_STATUS = frozenset({401, 403})


def resolve_path(data: Any, path: str) -> Any:
    """Walk a dotted path through nested mappings and sequences.

    Args:
        data: The decoded response body.
        path: Dot-separated path. Integer segments index into sequences, so
            ``"results.0.label"`` reads the first element's ``label``. An empty
            path returns ``data`` unchanged.

    Returns:
        The value at that path, or ``None`` if any segment does not resolve.
        Absence is never an error here: an optional field the service omitted
        should read as empty, not fail the case.
    """
    if not path:
        return data
    current = data
    for segment in path.split("."):
        if current is None:
            return None
        if isinstance(current, Mapping):
            if segment not in current:
                return None
            current = current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                index = int(segment)
            except ValueError:
                return None
            if not -len(current) <= index < len(current):
                return None
            current = current[index]
        else:
            return None
    return current


class HTTPEndpointAdapter(Adapter):
    """POST an image to an arbitrary endpoint and map the response back.

    Args:
        url: Target URL. Defaults to ``$BENCH_ENDPOINT_URL``.
        method: HTTP method. Defaults to ``POST``.
        payload: ``"multipart"``, ``"binary"``, or ``"json_base64"``.
        file_field: Form field name for the image in ``multipart`` mode, or the
            JSON key in ``json_base64`` mode.
        fields: Extra form or JSON fields sent with every request. Values may
            contain ``{case_id}``, ``{image_sha256}``, and ``{filename}``
            placeholders.
        map: Dotted paths for response fields, merged over
            :data:`DEFAULT_FIELD_MAP`. Recognised keys are the six
            :class:`~agribench.adapters.base.Prediction` fields; ``answered``
            is optional and special (see the module docstring).
        root: Dotted path applied before every mapping, so a service that nests
            everything under ``data`` needs ``root=data`` rather than a prefix
            on each entry.
        answered_threshold: When ``map["answered"]`` resolves to a number,
            answer only at or above this value.
        headers: Extra request headers.
        token: Bearer token. Defaults to ``$BENCH_ENDPOINT_TOKEN``.
        timeout: Per-request socket timeout in seconds.
        max_retries: Retries after the first attempt, transient failures only.
        backoff: Base seconds for exponential backoff.
        max_backoff: Ceiling on a single backoff wait, in seconds.
        system_type: Reported run type. Defaults to ``"system"``, since a
            bespoke endpoint is usually a pipeline rather than one model call.

    Raises:
        AdapterConfigError: For an unknown payload mode, an unknown mapping
            key, or a non-mapping ``map``/``fields``.
    """

    name = "http"
    system_type = "system"

    def __init__(
        self,
        *,
        url: str | None = None,
        method: str = "POST",
        payload: str = "multipart",
        file_field: str = "image",
        fields: Mapping[str, Any] | None = None,
        map: Mapping[str, str] | None = None,  # noqa: A002 - CLI-facing name
        field_map: Mapping[str, str] | None = None,
        root: str = "",
        answered_threshold: float | None = None,
        headers: Mapping[str, str] | None = None,
        token: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        backoff: float = 2.0,
        max_backoff: float = 30.0,
        system_type: str | None = None,
    ) -> None:
        self.url = (url or os.environ.get("BENCH_ENDPOINT_URL") or "").strip()
        self.method = str(method).upper()
        self.payload = str(payload).strip().lower()
        if self.payload not in _PAYLOAD_MODES:
            raise AdapterConfigError(
                f"payload must be one of {sorted(_PAYLOAD_MODES)}, got {payload!r}"
            )
        self.file_field = str(file_field)
        self.fields = dict(_as_mapping("fields", fields))
        self.root = str(root or "")
        self.answered_threshold = (
            None if answered_threshold is None else float(answered_threshold)
        )
        self.headers = dict(_as_mapping("headers", headers))
        self._token = (token or os.environ.get("BENCH_ENDPOINT_TOKEN") or "").strip()
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.backoff = max(0.0, float(backoff))
        self.max_backoff = max(0.0, float(max_backoff))
        if system_type is not None:
            self.system_type = str(system_type)

        merged = dict(DEFAULT_FIELD_MAP)
        merged.update(_as_mapping("field_map", field_map))
        merged.update(_as_mapping("map", map))
        allowed = {*DEFAULT_FIELD_MAP, "answered"}
        unknown = sorted(set(merged) - allowed)
        if unknown:
            raise AdapterConfigError(
                f"unknown map key(s) {unknown}; expected any of {sorted(allowed)}"
            )
        self.field_map = {key: str(value) for key, value in merged.items()}
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def setup(self) -> None:
        if not self.url:
            raise AdapterConfigError(
                "no endpoint configured -- set BENCH_ENDPOINT_URL or pass "
                "-O url=https://your-service/diagnose"
            )
        scheme = urlsplit(self.url).scheme
        if scheme not in {"http", "https"}:
            raise AdapterConfigError(
                f"url must be http or https, got {scheme or 'no scheme'}"
            )

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "system_type": self.system_type,
            "endpoint_host": urlsplit(self.url).netloc or self.url,
            "method": self.method,
            "payload": self.payload,
            "field_map": dict(self.field_map),
            "root": self.root,
            "answered_threshold": self.answered_threshold,
            "token_present": bool(self._token),
            "timeout_s": self.timeout,
            "max_retries": self.max_retries,
        }

    # -- the plug ----------------------------------------------------------

    def predict(self, case: Case) -> Prediction:
        """POST the image, map the response, and return a prediction.

        Raises:
            AdapterConfigError: Unreadable image or rejected credentials.
            TransientAdapterError: Timeouts, connection failures, and retryable
                statuses that outlived ``max_retries``.
            AdapterResponseError: A reply that was not usable JSON.
        """
        body, content_type = self._encode(case)

        for attempt in range(self.max_retries + 1):
            try:
                decoded = self._request(body, content_type)
            except TransientAdapterError:
                if attempt >= self.max_retries:
                    raise
                time.sleep(self._wait(attempt))
                continue
            return self._to_prediction(decoded)

        raise AdapterResponseError("retry loop exited without a response")

    # -- request encoding --------------------------------------------------

    def _render_fields(self, case: Case) -> dict[str, str]:
        context = {
            "case_id": case.case_id,
            "image_sha256": case.image_sha256,
            "filename": case.path.name,
        }
        rendered: dict[str, str] = {}
        for key, value in self.fields.items():
            text = str(value)
            try:
                rendered[key] = text.format(**context)
            except (KeyError, IndexError):
                rendered[key] = text  # a literal brace, not a placeholder
        return rendered

    def _encode(self, case: Case) -> tuple[bytes, str]:
        image = case.read_bytes()
        fields = self._render_fields(case)

        if self.payload == "binary":
            return image, case.media_type()
        if self.payload == "json_base64":
            document: dict[str, Any] = dict(fields)
            document[self.file_field] = base64.b64encode(image).decode("ascii")
            return json.dumps(document).encode("utf-8"), "application/json"
        return _multipart(
            file_field=self.file_field,
            filename=case.path.name,
            content=image,
            content_type=case.media_type(),
            fields=fields,
        )

    def _headers(self, content_type: str) -> dict[str, str]:
        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "agribench/0.1 (+http-endpoint)",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        headers.update(self.headers)
        return headers

    def _request(self, body: bytes, content_type: str) -> Any:
        request = urllib.request.Request(  # noqa: S310 - scheme validated in setup
            self.url,
            data=body,
            headers=self._headers(content_type),
            method=self.method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc)
            raise  # unreachable
        except urllib.error.URLError as exc:
            raise TransientAdapterError(
                f"could not reach {urlsplit(self.url).netloc}: {self._scrub(exc.reason)}"
            ) from exc
        except TimeoutError as exc:
            raise TransientAdapterError(f"request timed out after {self.timeout}s") from exc
        except OSError as exc:
            raise TransientAdapterError(f"transport error: {self._scrub(exc)}") from exc

        text = raw.decode("utf-8", "replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AdapterResponseError(
                f"endpoint returned a non-JSON body: {_short(text)}"
            ) from exc

    def _raise_for_status(self, exc: urllib.error.HTTPError) -> None:
        try:
            detail = self._scrub(_short(exc.read().decode("utf-8", "replace")))
        except Exception:  # noqa: BLE001
            detail = ""
        if exc.code in _AUTH_STATUS:
            raise AdapterConfigError(
                f"endpoint rejected the credentials (HTTP {exc.code}). Check "
                f"BENCH_ENDPOINT_TOKEN. Detail: {detail}"
            ) from exc
        if exc.code in _RETRY_STATUS:
            raise TransientAdapterError(f"HTTP {exc.code} from endpoint: {detail}") from exc
        raise AdapterResponseError(f"HTTP {exc.code} from endpoint: {detail}") from exc

    def _wait(self, attempt: int) -> float:
        window = min(self.backoff * (2.0**attempt), self.max_backoff)
        return window + random.uniform(0.0, min(1.0, window * 0.25))  # noqa: S311

    # -- response mapping --------------------------------------------------

    def _to_prediction(self, decoded: Any) -> Prediction:
        root = resolve_path(decoded, self.root)
        if root is None:
            raise AdapterResponseError(
                f"root path {self.root!r} did not resolve in the response"
            )

        payload = {
            key: resolve_path(root, path)
            for key, path in self.field_map.items()
            if key != "answered"
        }
        answered = self._answered(root, payload)
        return Prediction.from_payload(payload, answered=answered)

    def _answered(self, root: Any, payload: Mapping[str, Any]) -> bool:
        """Apply the three-step answered rule from the module docstring."""
        path = self.field_map.get("answered")
        if path:
            raw = resolve_path(root, path)
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)) and self.answered_threshold is not None:
                return float(raw) >= self.answered_threshold
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered in {"true", "yes", "answered", "ok"}:
                    return True
                if lowered in {"false", "no", "declined", "abstain", "abstained"}:
                    return False
            if raw is not None and not isinstance(raw, (int, float, str, bool)):
                raise AdapterResponseError(
                    f"answered path {path!r} resolved to "
                    f"{type(raw).__name__}, which is not a verdict"
                )
        return is_answered_category(payload.get("primary_category"))

    # -- secrets -----------------------------------------------------------

    def _scrub(self, text: Any) -> str:
        rendered = str(text)
        if self._token and self._token in rendered:
            rendered = rendered.replace(self._token, "***REDACTED***")
        return rendered


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _as_mapping(label: str, value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AdapterConfigError(f"{label} must be a JSON object: {exc}") from exc
    if not isinstance(value, Mapping):
        raise AdapterConfigError(
            f"{label} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _short(text: Any, limit: int = 300) -> str:
    rendered = " ".join(str(text).split())
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _multipart(
    *,
    file_field: str,
    filename: str,
    content: bytes,
    content_type: str,
    fields: Mapping[str, str],
) -> tuple[bytes, str]:
    """Encode a ``multipart/form-data`` body. Stdlib has no builder for this."""
    boundary = f"----agribench{uuid.uuid4().hex}"
    marker = f"--{boundary}".encode()
    chunks: list[bytes] = []

    for key, value in fields.items():
        chunks.append(marker + b"\r\n")
        disposition = f'Content-Disposition: form-data; name="{_quote(key)}"'
        chunks.append(disposition.encode("utf-8") + b"\r\n\r\n")
        chunks.append(str(value).encode("utf-8") + b"\r\n")

    chunks.append(marker + b"\r\n")
    disposition = (
        f'Content-Disposition: form-data; name="{_quote(file_field)}"; '
        f'filename="{_quote(filename)}"'
    )
    chunks.append(disposition.encode("utf-8") + b"\r\n")
    chunks.append(f"Content-Type: {content_type}".encode() + b"\r\n\r\n")
    chunks.append(content + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())

    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _quote(value: str) -> str:
    """Escape a value for a ``Content-Disposition`` parameter (RFC 7578 §5.1)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")
