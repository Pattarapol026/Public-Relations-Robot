"""Gemini text generation through the Google Gen AI SDK (verified with google-genai 2.29.0).

This client only turns text into text. It has no tools and no access to the
robot: whatever the model writes is shown and spoken, never executed.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

RETRY_PAUSE_S = 0.5
# After the network failed or the model did not answer in time, questions in the
# next seconds go straight to the knowledge base instead of each waiting out the
# whole timeout again (visitors once waited 17-25 s for a fallback answer).
UNREACHABLE_BACKOFF_S = 20.0
# The SDK passes its timeout to Google as the request's deadline, and Google
# refuses any deadline under 10 s ("Minimum allowed deadline is 10s", seen with
# the live API). The visitor's wait is bounded by the clock instead (``_generate``).
SERVER_MIN_DEADLINE_S = 10.0
# Most answers take about 1-2 s (median 1.4 s on 2026-11-04/05); a request still
# unanswered after this long gets a second one sent alongside it, and whichever
# answers first is used. The first is not given up on: dropping it at 4 s and
# retrying with the 1.5 s left almost never worked (32 timeouts in two days).
FIRST_ATTEMPT_S = 2.0
# One slow answer is not an outage: Google is skipped for UNREACHABLE_BACKOFF_S only
# when the network fails, or after this many timeouts in a row.
TIMEOUTS_BEFORE_BACKOFF = 2

# Why a request failed; the conversation layer logs it and falls back.
BACKUP_MODEL = "gemini-3.1-flash-lite"  # for the second request when the main model is overloaded
KIND_AUTH = "auth"  # key missing, wrong, or not allowed to use the model
KIND_QUOTA = "quota"  # rate limit or quota exhausted
KIND_NETWORK = "network"  # no internet, DNS, connection reset
KIND_TIMEOUT = "timeout"
KIND_SERVER = "server"  # Google-side 5xx
KIND_REQUEST = "request"  # the request itself was rejected (bad model name, ...)
KIND_EMPTY = "empty"  # no text came back (blocked by safety filters, or nothing generated)
KIND_UNAVAILABLE = "unavailable"  # the SDK is not installed


class GeminiError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "user" or "model"
    text: str


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float = 4.5,
        max_retries: int = 1,
        backup_model: str = BACKUP_MODEL,
    ) -> None:
        self._api_key = api_key
        self._model = model
        # The second request (sent when the first is slow or fails) goes to another model: when
        # one is overloaded (HTTP 503/504) asking the same one again waits just as long.
        self._backup_model = backup_model or model
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._client = None
        self._lock = threading.Lock()
        self._unreachable_until = 0.0
        self._timeouts_in_row = 0

    @property
    def model(self) -> str:
        return self._model

    def warm_up(self) -> None:
        """Load the SDK and create the client ahead of the first question (no request is sent)."""
        try:
            self._get_client()
        except Exception:  # the first real question will report the problem
            logger.exception("Could not prepare the Gemini client")

    def reset_backoff(self) -> None:
        """Try the network again on the next request (the Admin's "test AI" button)."""
        self._unreachable_until = 0.0

    def generate(
        self,
        system_instruction: str,
        turns: Sequence[ChatTurn],
        max_output_tokens: int = 400,
        temperature: float = 0.3,
    ) -> str:
        """Return the model's reply. Blocking; raises ``GeminiError``.

        The whole call, retries included, is bounded by ``timeout_s`` so a
        visitor never waits longer than that before the fallback answer. The
        bound is kept by the clock, not only by the SDK's own timeout, which
        does not cover every stage of a request (a slow name lookup, a reply
        that trickles in).
        """
        if time.monotonic() < self._unreachable_until:
            raise GeminiError(KIND_NETWORK, "skipped: Gemini was unreachable moments ago")
        try:
            text = self._generate(system_instruction, turns, max_output_tokens, temperature)
        except GeminiError as exc:
            self._timeouts_in_row = self._timeouts_in_row + 1 if exc.kind == KIND_TIMEOUT else 0
            if exc.kind == KIND_NETWORK or self._timeouts_in_row >= TIMEOUTS_BEFORE_BACKOFF:
                self._unreachable_until = time.monotonic() + UNREACHABLE_BACKOFF_S
                logger.warning(
                    "Gemini unreachable (%s); answering from the knowledge base for the next %.0f s",
                    exc.kind, UNREACHABLE_BACKOFF_S,
                )
            raise
        self._timeouts_in_row = 0
        return text

    def _generate(
        self, system_instruction: str, turns: Sequence[ChatTurn], max_output_tokens: int, temperature: float
    ) -> str:
        try:
            from google.genai import types
        except ImportError as exc:
            raise GeminiError(KIND_UNAVAILABLE, "google-genai is not installed") from exc

        contents = [
            types.Content(role=turn.role, parts=[types.Part.from_text(text=turn.text)])
            for turn in turns
        ]
        deadline = time.monotonic() + self._timeout_s
        results: queue.Queue = queue.Queue()
        pending = attempts = 0
        failure: BaseException | None = None

        def start() -> bool:
            """Send one more request, unless the time is (nearly) spent."""
            nonlocal pending, attempts
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                return False
            attempts += 1
            pending += 1
            model = self._model if attempts == 1 else self._backup_model
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                http_options=types.HttpOptions(timeout=int(max(remaining, SERVER_MIN_DEADLINE_S) * 1000)),
                # A reception answer needs no long reasoning: "minimal" answers faster.
                thinking_config=types.ThinkingConfig(thinking_level="minimal") if model.startswith("gemini-3") else None,
            )
            # Left to finish on a daemon thread if never needed: it cannot keep the program open.
            threading.Thread(
                target=lambda: results.put(self._attempt(contents, config, model)),
                name="gemini-request", daemon=True,
            ).start()
            return True

        if not start():
            raise GeminiError(KIND_TIMEOUT, f"no answer within {self._timeout_s:.0f} s")
        second_at = time.monotonic() + FIRST_ATTEMPT_S if self._max_retries else None
        while True:
            if pending == 0:  # every request sent so far failed in a way that may pass
                if attempts > self._max_retries or deadline - time.monotonic() <= RETRY_PAUSE_S + 0.5:
                    raise failure
                logger.warning("Gemini request failed (%s); retrying", failure)
                time.sleep(RETRY_PAUSE_S)
                second_at = None
                if not start():
                    raise failure
                continue
            wake = deadline if second_at is None else min(deadline, second_at)
            try:
                outcome, value = results.get(timeout=max(0.0, wake - time.monotonic()))
            except queue.Empty:
                if second_at is not None and time.monotonic() < deadline:
                    second_at = None
                    if attempts <= self._max_retries and start():
                        logger.warning(
                            "Gemini slow (no answer after %.1f s); sent a second request alongside the first",
                            FIRST_ATTEMPT_S,
                        )
                    continue
                raise GeminiError(KIND_TIMEOUT, f"no answer within {self._timeout_s:.0f} s") from None
            pending -= 1
            if outcome == _DONE:
                return value
            if outcome == _FINAL:
                raise value
            failure = value  # _TRANSIENT: wait for a request still out, or try again

    def _attempt(self, contents, config, model: str) -> tuple[str, object]:
        """One request: ``(_DONE, text)``, or the error as ``_FINAL`` / ``_TRANSIENT``. Never raises."""
        try:
            from google.genai import errors
            import httpx

            response = self._get_client().models.generate_content(
                model=model, contents=contents, config=config
            )
            text = (response.text or "").strip()
            if not text:
                return _FINAL, GeminiError(KIND_EMPTY, "the model returned no text")
            return _DONE, text
        except errors.ClientError as exc:
            # Google answers a wrong key with 400 "API key not valid", not 401.
            if exc.code in (401, 403) or "api key" in str(exc.message).lower():
                failure = GeminiError(KIND_AUTH, f"HTTP {exc.code}")
            elif exc.code == 429:
                failure = GeminiError(KIND_QUOTA, "HTTP 429")
            else:
                failure = GeminiError(KIND_REQUEST, f"HTTP {exc.code}: {exc.message}")
            return _FINAL, _caused(failure, exc)
        except errors.ServerError as exc:
            return _TRANSIENT, _caused(GeminiError(KIND_SERVER, f"HTTP {exc.code}"), exc)
        except httpx.TimeoutException as exc:
            return _TRANSIENT, _caused(GeminiError(KIND_TIMEOUT, type(exc).__name__), exc)
        except httpx.TransportError as exc:
            return _TRANSIENT, _caused(GeminiError(KIND_NETWORK, type(exc).__name__), exc)
        except BaseException as exc:  # anything unexpected reaches the caller as it was
            return _FINAL, exc

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from google import genai

                self._client = genai.Client(api_key=self._api_key)
            return self._client


# How one request ended (``GeminiClient._attempt``).
_DONE = "done"
_FINAL = "final"  # will not fix itself: no retry
_TRANSIENT = "transient"  # may pass: another request may succeed


def _caused(failure: GeminiError, cause: BaseException) -> GeminiError:
    failure.__cause__ = cause
    return failure
