"""Gemini REST client that turns one repository into a validated summary."""

import http.client
import json
import math
import time
import urllib.error
import urllib.request

from trending_site import summary

DEFAULT_MODEL = "gemini-3.5-flash-lite"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MIN_INTERVAL = 6.0
RETRIES = 4
OUTPUT_ATTEMPTS = 2
TIMEOUT = 60
MAX_RETRY_DELAY = 120.0
TYPE_PREFIX = "type.googleapis.com/google.rpc."


class AuthError(Exception):
    """Gemini rejects our key or configuration (4xx other than 429); the run fails.

    The message is in Portuguese, free of the key, and safe to show on the index.
    """


class Unavailable(Exception):
    """Gemini cannot be used right now (quota, outage); the run stops calling it."""


class SummaryFailed(Exception):
    """Gemini answered but no valid summary came out; the repo is at fault."""


def urllib_post(url: str, body: dict, headers: dict) -> tuple[int, dict]:
    """POSTs JSON and returns the status and decoded JSON body, error bodies included.

    Raises:
        urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException: when no
            complete HTTP response arrives.
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    try:
        decoded = json.loads(raw)
    except ValueError:
        decoded = {}
    return status, decoded if isinstance(decoded, dict) else {}


def _details(body: dict, kind: str) -> list[dict]:
    error = body.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    return [d for d in details or []
            if isinstance(d, dict) and d.get("@type") == TYPE_PREFIX + kind]


def _is_daily_quota(body: dict) -> bool:
    return any("PerDay" in str(violation.get("quotaId", ""))
               for detail in _details(body, "QuotaFailure")
               for violation in detail.get("violations") or [] if isinstance(violation, dict))


def _is_invalid_key(body: dict) -> bool:
    return any(detail.get("reason") == "API_KEY_INVALID" for detail in _details(body, "ErrorInfo"))


def _retry_delay(body: dict) -> float | None:
    for detail in _details(body, "RetryInfo"):
        delay = str(detail.get("retryDelay", ""))
        try:
            seconds = float(delay.removesuffix("s"))
        except ValueError:
            continue
        if math.isnan(seconds):
            continue
        # A bogus hint must neither crash `sleep` nor hold the run for hours.
        return min(max(seconds, 0.0), MAX_RETRY_DELAY)
    return None


def _message(body: dict) -> str:
    error = body.get("error")
    return str(error.get("message", "")) if isinstance(error, dict) else ""


def _status(body: dict) -> str:
    error = body.get("error")
    return str(error.get("status", "")) if isinstance(error, dict) else ""


class Client:
    """Summarizes repositories with Gemini, pacing and retrying requests.

    Args:
        api_key: the Gemini API key, sent only in a header.
        model: the Gemini model name.
        post: callable `(url, body, headers) -> (status, json_body)`; defaults to urllib.
        sleep: callable waiting the given seconds.
        clock: monotonic clock in seconds.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, post=None,
                 sleep=time.sleep, clock=time.monotonic):
        self._api_key = api_key
        self._url = ENDPOINT.format(model=model)
        self._post = post or urllib_post
        self._sleep = sleep
        self._clock = clock
        self._last_start: float | None = None

    def summarize(self, repo: dict) -> dict:
        """Summarizes one repository.

        Args:
            repo: a fetched repository, as `summary.material` expects.

        Returns:
            A summary that passes `summary.problems`.

        Raises:
            AuthError: the key or the request was rejected (any 4xx but 429, except a 400
                INVALID_ARGUMENT that is not an invalid key).
            Unavailable: quota is used up or Gemini keeps failing.
            SummaryFailed: the prompt was blocked, no valid output came out, or Gemini
                answered 400 INVALID_ARGUMENT for this repo's content.
        """
        body = {
            "systemInstruction": {"parts": [{"text": summary.PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": summary.material(repo)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": summary.RESPONSE_SCHEMA,
                "temperature": 0.2,
            },
        }
        reason = "no output"
        for _ in range(OUTPUT_ATTEMPTS):
            result, reason = self._parse(self._request(body))
            if result is not None:
                return result
        raise SummaryFailed(reason)

    def _wait_turn(self) -> None:
        if self._last_start is not None:
            wait = MIN_INTERVAL - (self._clock() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._clock()

    def _request(self, body: dict) -> dict:
        """Posts with pacing and retries; returns the body of a 200 response."""
        headers = {"x-goog-api-key": self._api_key}
        failure = "no response"
        for attempt in range(RETRIES + 1):
            self._wait_turn()
            try:
                status, reply = self._post(self._url, body, headers)
            except (OSError, http.client.HTTPException) as err:
                # URLError and timeouts are OSErrors; HTTPException is a cut or garbled reply.
                status, reply = None, {}
                failure = f"network error: {type(err).__name__}"
            else:
                if status == 200:
                    return reply
                failure = f"HTTP {status}: {_message(reply)}"
                if status in (401, 403) or (status == 400 and _is_invalid_key(reply)):
                    raise AuthError("chave do Gemini rejeitada")
                if status == 400 and _status(reply) == "INVALID_ARGUMENT":
                    raise SummaryFailed(failure)
                if status == 429 and _is_daily_quota(reply):
                    raise Unavailable("daily quota used up")
                if status != 429 and status < 500:
                    raise AuthError(
                        f"Gemini rejeitou o pedido: HTTP {status} {_status(reply)}".strip())
            if attempt < RETRIES:
                delay = _retry_delay(reply)
                self._sleep(10 * 2 ** attempt if delay is None else delay)
        raise Unavailable(f"gave up after {RETRIES + 1} attempts: {failure}")

    @staticmethod
    def _parse(reply: dict) -> tuple[dict | None, str]:
        """Returns `(summary, "")` or `(None, reason)` when the output is unusable and retryable."""
        block = (reply.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise SummaryFailed(f"prompt blocked: {block}")
        candidates = reply.get("candidates") or []
        if not candidates:
            raise SummaryFailed("no candidates in the response")
        finish = candidates[0].get("finishReason")
        if finish != "STOP":
            raise SummaryFailed(f"finishReason {finish}")
        try:
            text = candidates[0]["content"]["parts"][0]["text"]
            decoded = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError):
            return None, "output is not JSON"
        found = summary.problems(decoded)
        return (None, "; ".join(found)) if found else (decoded, "")
