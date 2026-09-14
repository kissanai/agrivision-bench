"""Adapter for any OpenAI-shaped ``/v1/chat/completions`` endpoint.

One adapter covers most of the field, because most of the field speaks the same
wire format. Hosted routers, first-party APIs, and anything you run yourself::

    # a hosted router
    BENCH_BASE_URL=https://openrouter.ai/api/v1 BENCH_MODEL=<vendor/model> \\
    BENCH_API_KEY=... python -m agribench run --adapter openai

    # a local server, no key needed
    BENCH_BASE_URL=http://localhost:11434/v1 BENCH_MODEL=<local-model> \\
    python -m agribench run --adapter openai

vLLM, Ollama, LM Studio, llama.cpp's server, and the first-party OpenAI API all
work through this path unchanged. Only the base URL and the model id differ.

Configuration comes from the environment so that a command line -- which lands
in shell history, CI logs, and screenshots -- never carries a secret:

==================  ==========================================================
``BENCH_BASE_URL``  Endpoint root, e.g. ``https://host/api/v1``. Required.
``BENCH_MODEL``     Model identifier as the endpoint spells it. Required.
``BENCH_API_KEY``   Bearer token. Optional -- local servers usually need none.
==================  ==========================================================

The key is read once, never logged, never written to a run record, and scrubbed
out of any provider error text before that text is raised. ``describe()``
reports only that a key was present.

Stdlib only: HTTP goes through :mod:`urllib.request`. The core of this
benchmark installs with zero dependencies, and a scoring path that cannot be
reproduced from a bare CPython install is not much of a scoring path.

===========================================================================
STRUCTURED OUTPUT  --  and what happens when a server will not do it
===========================================================================

The request asks for the frozen ``SCHEMA`` via ``response_format`` in strict
``json_schema`` mode. That is how the published numbers were produced and it is
the only mode that guarantees every system answered the identical question.

Not every server supports it. When one rejects the strict schema, the default
``structured="auto"`` degrades -- ``json_schema`` to ``json_object`` to plain
text -- and this is a **disclosed deviation, not a silent fallback**:

* the downgrade is announced once on stderr,
* the mode actually used is stamped on every result record as
  ``structured_mode``,
* :meth:`describe` reports the final mode in the run's metadata.

In the degraded modes the frozen ``PROMPT`` is still sent byte-for-byte as the
user message; the schema instruction is added as a *separate system message*
so the pinned prompt text is never edited. Pass ``structured="json_schema"`` to
refuse to degrade at all and fail the run instead, which is the right setting
when producing numbers for publication.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from agribench.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterResponseError,
    Case,
    Prediction,
    TransientAdapterError,
)
from agribench.contract import PROMPT, SCHEMA

__all__ = ["OpenAICompatibleAdapter"]

#: Modes tried in order when ``structured="auto"``.
_MODE_CHAIN: tuple[str, ...] = ("json_schema", "json_object", "text")

#: HTTP statuses worth retrying. Everything else is a real answer, including a
#: real rejection, and hammering it would only waste the user's money.
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})

#: Statuses that mean "your credentials are wrong", which no retry will fix.
_AUTH_STATUS = frozenset({401, 403})

_JSON_INSTRUCTION = (
    "Reply with a single JSON object and nothing else -- no prose, no code "
    "fence. It must conform to this JSON Schema: "
)


class OpenAICompatibleAdapter(Adapter):
    """Call an OpenAI-shaped chat completions endpoint with the frozen prompt.

    Args:
        base_url: Endpoint root. Defaults to ``$BENCH_BASE_URL``. May be given
            with or without a trailing slash, and either as the API root
            (``.../v1``) or as the full completions URL.
        model: Model identifier. Defaults to ``$BENCH_MODEL``.
        api_key: Bearer token. Defaults to ``$BENCH_API_KEY``. Omit for local
            servers that do not authenticate.
        timeout: Per-request socket timeout in seconds.
        max_retries: Retries *after* the first attempt, for transient failures
            only. Total attempts are ``max_retries + 1``.
        backoff: Base seconds for exponential backoff. Waits are
            ``backoff * 2**attempt`` plus jitter, capped at ``max_backoff``, and
            a ``Retry-After`` header always wins.
        max_backoff: Ceiling on a single backoff wait, in seconds.
        temperature: Sampling temperature. ``None`` (default) omits the field
            so the provider's default applies.
        max_tokens: Completion cap. ``None`` (default) omits the field, which
            is what the published runs did -- a client-side ceiling truncates
            reasoning models mid-answer and looks like a model failure.
        structured: ``"auto"`` (default), ``"json_schema"``, ``"json_object"``,
            or ``"text"``. See the module docstring on degradation.
        image_detail: Optional ``detail`` hint on the image part, e.g.
            ``"high"``. Omitted when ``None``.
        extra_headers: Extra request headers, e.g. a router's attribution
            headers. Never put a secret here -- it is not scrubbed from
            ``describe()`` output.
        extra_body: Extra top-level request-body keys, merged last, for
            provider-specific parameters.
        system_type: Reported run type. Defaults to ``"single_call"``; set
            ``"system"`` if this endpoint fronts a pipeline rather than a model.

    Raises:
        AdapterConfigError: If ``base_url`` or ``model`` is missing, or
            ``structured`` is not a recognised mode.
    """

    name = "openai-compatible"
    system_type = "single_call"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 4,
        backoff: float = 2.0,
        max_backoff: float = 45.0,
        temperature: float | None = None,
        max_tokens: int | None = None,
        structured: str = "auto",
        image_detail: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        system_type: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("BENCH_BASE_URL") or "").strip()
        self.model = (model or os.environ.get("BENCH_MODEL") or "").strip()
        self._api_key = (api_key or os.environ.get("BENCH_API_KEY") or "").strip()
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.backoff = max(0.0, float(backoff))
        self.max_backoff = max(0.0, float(max_backoff))
        self.temperature = None if temperature is None else float(temperature)
        self.max_tokens = None if max_tokens is None else int(max_tokens)
        self.image_detail = image_detail
        self.extra_headers = dict(extra_headers or {})
        self.extra_body = dict(extra_body or {})
        if system_type is not None:
            self.system_type = str(system_type)

        structured = str(structured).strip().lower()
        if structured not in {"auto", *_MODE_CHAIN}:
            raise AdapterConfigError(
                f"structured must be auto, json_schema, json_object, or text; "
                f"got {structured!r}"
            )
        self.structured = structured

        # Current mode, shared across worker threads. Degradation is sticky:
        # once a server has rejected strict schemas it will reject them for
        # every case, and re-discovering that 563 more times is pure waste.
        self._mode = _MODE_CHAIN[0] if structured == "auto" else structured
        self._lock = threading.Lock()
        self._warned: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def setup(self) -> None:
        """Validate configuration before the case loop starts.

        One clear error now beats the same error 564 times in a row.
        """
        if not self.base_url:
            raise AdapterConfigError(
                "no endpoint configured -- set BENCH_BASE_URL "
                "(e.g. https://openrouter.ai/api/v1, or "
                "http://localhost:11434/v1 for a local server)"
            )
        if not self.model:
            raise AdapterConfigError(
                "no model configured -- set BENCH_MODEL to the identifier this "
                "endpoint uses"
            )

    def describe(self) -> dict[str, Any]:
        """Run metadata. Reports that a key existed, never what it was."""
        with self._lock:
            mode = self._mode
        return {
            "adapter": self.name,
            "system_type": self.system_type,
            "endpoint_host": _host_of(self.base_url),
            "model": self.model,
            "api_key_present": bool(self._api_key),
            "structured_output": self.structured,
            "structured_mode_final": mode,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout,
            "max_retries": self.max_retries,
        }

    # -- the plug ----------------------------------------------------------

    def predict(self, case: Case) -> Prediction:
        """Send the frozen prompt plus the image and parse the reply.

        Raises:
            AdapterConfigError: Missing configuration, unreadable image, or
                rejected credentials. Non-retryable.
            TransientAdapterError: Timeouts, connection failures, 429s, and
                5xx responses that outlived ``max_retries``.
            AdapterResponseError: The endpoint replied but the reply was not a
                usable diagnosis -- a refusal, an empty completion, or a body
                no lenient parse could turn into a JSON object.
        """
        data_uri = _data_uri(case)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            mode = self._current_mode()
            try:
                payload = self._request(data_uri, mode)
            except _SchemaRejected as exc:
                # The server dislikes this response_format, not this image.
                # Step down and retry immediately without burning an attempt.
                if not self._degrade(mode, str(exc)):
                    raise AdapterConfigError(
                        f"endpoint rejected structured output in {mode!r} mode "
                        f"and degradation is disabled (structured="
                        f"{self.structured!r}): {exc}"
                    ) from exc
                continue
            except TransientAdapterError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(self._wait_for(exc, attempt))
                continue

            return Prediction.from_payload(self._extract_object(payload, mode))

        # Only reachable if the mode chain is exhausted by repeated 400s.
        raise AdapterResponseError(
            f"gave up after {self.max_retries + 1} attempts: {last_error}"
        )

    # -- mode handling -----------------------------------------------------

    def _current_mode(self) -> str:
        with self._lock:
            return self._mode

    def _degrade(self, from_mode: str, reason: str) -> bool:
        """Step down the mode chain. Returns ``False`` if there is nowhere to go."""
        if self.structured != "auto":
            return False
        with self._lock:
            if self._mode != from_mode:
                return True  # another thread already stepped down
            index = _MODE_CHAIN.index(from_mode)
            if index + 1 >= len(_MODE_CHAIN):
                return False
            self._mode = _MODE_CHAIN[index + 1]
            new_mode = self._mode
            first_time = from_mode not in self._warned
            self._warned.add(from_mode)
        if first_time:
            print(
                f"agribench: endpoint rejected {from_mode!r} structured output "
                f"({_short(reason)}); falling back to {new_mode!r}. This is a "
                f"disclosed deviation and is recorded as structured_mode on "
                f"every result.",
                file=sys.stderr,
            )
        return True

    def structured_mode(self) -> str:
        """The response-format mode currently in use. Stamped on each record."""
        return self._current_mode()

    # -- request/response --------------------------------------------------

    def _endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _messages(self, data_uri: str, mode: str) -> list[dict[str, Any]]:
        image_part: dict[str, Any] = {"url": data_uri}
        if self.image_detail:
            image_part["detail"] = self.image_detail

        messages: list[dict[str, Any]] = []
        if mode != "json_schema":
            # The frozen PROMPT is never edited. When the server cannot be told
            # the shape through response_format, it is told here instead.
            messages.append(
                {
                    "role": "system",
                    "content": _JSON_INSTRUCTION
                    + json.dumps(SCHEMA, separators=(",", ":")),
                }
            )
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": image_part},
                ],
            }
        )
        return messages

    def _body(self, data_uri: str, mode: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(data_uri, mode),
        }
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agri_diagnosis",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        body.update(self.extra_body)
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "agribench/0.1 (+openai-compatible)",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self.extra_headers)
        return headers

    def _request(self, data_uri: str, mode: str) -> dict[str, Any]:
        """One HTTP round trip. Classifies every failure into an error type."""
        payload = json.dumps(self._body(data_uri, mode)).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - scheme validated below
            self._endpoint(),
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        if request.type not in {"http", "https"}:
            raise AdapterConfigError(
                f"base_url must be http or https, got {request.type!r}"
            )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc, mode)
            raise  # unreachable; _raise_for_status always raises
        except urllib.error.URLError as exc:
            raise TransientAdapterError(
                f"could not reach {_host_of(self.base_url)}: {self._scrub(exc.reason)}"
            ) from exc
        except TimeoutError as exc:
            raise TransientAdapterError(
                f"request timed out after {self.timeout}s"
            ) from exc
        except OSError as exc:
            raise TransientAdapterError(f"transport error: {self._scrub(exc)}") from exc

        try:
            decoded = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise AdapterResponseError(
                f"endpoint returned a non-JSON body: {_short(raw.decode('utf-8', 'replace'))}"
            ) from exc
        if not isinstance(decoded, dict):
            raise AdapterResponseError(
                f"expected a JSON object envelope, got {type(decoded).__name__}"
            )
        return decoded

    def _raise_for_status(self, exc: urllib.error.HTTPError, mode: str) -> None:
        """Turn an HTTP error into the right adapter error. Always raises."""
        status = exc.code
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a body we cannot read is not fatal
            detail = ""
        detail = self._scrub(_short(detail))

        if status in _AUTH_STATUS:
            raise AdapterConfigError(
                f"endpoint rejected the credentials (HTTP {status}). Check "
                f"BENCH_API_KEY for {_host_of(self.base_url)}. Detail: {detail}"
            ) from exc
        if status in _RETRY_STATUS:
            error = TransientAdapterError(f"HTTP {status} from provider: {detail}")
            error.retry_after = _retry_after(exc)  # type: ignore[attr-defined]
            raise error from exc
        if status in {400, 404, 422, 501} and mode != "text" and _is_schema_complaint(detail):
            raise _SchemaRejected(f"HTTP {status}: {detail}") from exc
        if status == 404:
            raise AdapterConfigError(
                f"HTTP 404 from {self._endpoint()} -- check BENCH_BASE_URL and "
                f"that model {self.model!r} exists on this endpoint. Detail: {detail}"
            ) from exc
        raise AdapterResponseError(f"HTTP {status} from provider: {detail}") from exc

    def _wait_for(self, exc: Exception, attempt: int) -> float:
        """Backoff seconds before the next attempt. ``Retry-After`` wins."""
        hinted = getattr(exc, "retry_after", None)
        if hinted is not None:
            return min(float(hinted), self.max_backoff)
        window = min(self.backoff * (2.0**attempt), self.max_backoff)
        return window + random.uniform(0.0, min(1.0, window * 0.25))  # noqa: S311

    # -- parsing -----------------------------------------------------------

    def _extract_object(self, envelope: dict[str, Any], mode: str) -> dict[str, Any]:
        """Pull the diagnosis object out of a chat-completions envelope."""
        if "error" in envelope and envelope.get("error"):
            raise AdapterResponseError(
                f"provider error: {self._scrub(_short(json.dumps(envelope['error'])))}"
            )
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AdapterResponseError("response contained no choices")
        message = choices[0].get("message") or {}
        if message.get("refusal"):
            raise AdapterResponseError(f"model refused: {_short(message['refusal'])}")

        text = _message_text(message)
        if not text.strip():
            finish = choices[0].get("finish_reason")
            raise AdapterResponseError(
                f"empty completion (finish_reason={finish!r}). If this is a "
                f"reasoning model, an over-tight max_tokens is the usual cause."
            )

        obj = _loads_lenient(text)
        if obj is None:
            raise AdapterResponseError(
                f"could not parse a JSON object out of the completion "
                f"(mode={mode}): {_short(text)}"
            )
        return obj

    # -- secrets -----------------------------------------------------------

    def _scrub(self, text: Any) -> str:
        """Remove the API key from any text that might be raised or logged."""
        rendered = str(text)
        if self._api_key and self._api_key in rendered:
            rendered = rendered.replace(self._api_key, "***REDACTED***")
        return rendered


class _SchemaRejected(Exception):
    """Internal: the endpoint refused this ``response_format``, not this image."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _data_uri(case: Case) -> str:
    """Encode the case image as a ``data:`` URI for the image part."""
    encoded = base64.b64encode(case.read_bytes()).decode("ascii")
    return f"data:{case.media_type()};base64,{encoded}"


def _host_of(url: str) -> str:
    """Hostname of a URL, for error messages that should not echo a full URL."""
    try:
        return urlsplit(url).netloc or url
    except ValueError:
        return url


def _short(text: Any, limit: int = 300) -> str:
    """One-line, length-capped rendering of provider text."""
    rendered = " ".join(str(text).split())
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    """Parse a ``Retry-After`` header, seconds form only."""
    try:
        value = exc.headers.get("Retry-After")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _is_schema_complaint(detail: str) -> bool:
    """Whether a 4xx body looks like "I don't support that response_format"."""
    lowered = detail.lower()
    needles = (
        "response_format",
        "json_schema",
        "structured output",
        "structured_output",
        "json mode",
        "schema",
    )
    return any(needle in lowered for needle in needles)


def _message_text(message: dict[str, Any]) -> str:
    """Flatten ``message.content``, which may be a string or a list of parts."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _loads_lenient(text: str) -> dict[str, Any] | None:
    """Recover a JSON object from a completion that may be wrapped in prose.

    Tries, in order: the whole string, the contents of a fenced code block, and
    the first balanced ``{...}`` span. Returns ``None`` if none of those yield
    a JSON object.

    This tolerance exists only for the degraded ``json_object`` and ``text``
    modes. In strict ``json_schema`` mode the first attempt always succeeds, so
    nothing published depends on the guesswork below.
    """
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(text: str) -> list[str]:
    candidates = [text.strip()]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
    return candidates
