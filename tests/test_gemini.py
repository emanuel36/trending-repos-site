"""Tests for the Gemini client."""

import http.client
import json
import unittest

from trending_site import gemini, summary

KEY = "secret-key-123"
REPO = {"name": "a/b", "description": "d", "language": "Python", "homepage": "", "readme": "r"}
SUMMARY = {"what": "Faz algo. Mais.", "who": "Devs", "alternatives": None,
           "maturity": "beta", "translated_from": None}


def ok(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def error(code, status, *details):
    return code, {"error": {"code": code, "status": status, "details": list(details)}}


def quota(quota_id):
    return {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}]}


class GeminiTest(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.calls = []
        self.sleeps = []
        self.now = 1000.0
        self.client = gemini.Client(KEY, post=self.post, sleep=self.sleep, clock=lambda: self.now)

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def test_returns_valid_summary(self):
        self.responses = [ok(SUMMARY)]
        self.assertEqual(self.client.summarize(REPO), SUMMARY)
        url, body, headers = self.calls[0]
        self.assertEqual(headers["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, url)
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/"
                              f"{gemini.DEFAULT_MODEL}:generateContent")
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": summary.PROMPT}]})
        self.assertEqual(body["contents"],
                         [{"role": "user", "parts": [{"text": summary.material(REPO)}]}])
        config = body["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["responseSchema"], summary.RESPONSE_SCHEMA)
        self.assertEqual(config["temperature"], 0.2)

    def test_spaces_calls(self):
        self.responses = [ok(SUMMARY), ok(SUMMARY)]
        self.client.summarize(REPO)
        self.client.summarize(REPO)
        self.assertEqual(self.sleeps, [6.0])

    def test_retries_rate_limit_then_succeeds(self):
        self.responses = [error(429, "RESOURCE_EXHAUSTED", quota("GenerateRequestsPerMinute")),
                          ok(SUMMARY)]
        self.assertEqual(self.client.summarize(REPO), SUMMARY)
        self.assertIn(10, self.sleeps)

    def test_retry_delay_from_error_is_used(self):
        retry = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17.5s"}
        self.responses = [error(429, "RESOURCE_EXHAUSTED", retry), ok(SUMMARY)]
        self.client.summarize(REPO)
        self.assertEqual(self.sleeps, [17.5])

    def test_retry_delay_is_clamped(self):
        for delay, expected in (("-5s", 0), ("86400s", gemini.MAX_RETRY_DELAY), ("nans", 10)):
            with self.subTest(delay=delay):
                self.sleeps.clear()
                retry = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
                self.responses = [error(429, "RESOURCE_EXHAUSTED", retry), ok(SUMMARY)]
                self.client.summarize(REPO)
                self.assertIn(expected, self.sleeps)
                self.assertTrue(all(0 <= s <= gemini.MAX_RETRY_DELAY for s in self.sleeps))

    def test_network_errors_are_retried(self):
        self.responses = [TimeoutError("slow"), ok(SUMMARY)]
        self.assertEqual(self.client.summarize(REPO), SUMMARY)
        self.assertEqual(self.sleeps, [10])

    def test_broken_http_responses_are_retried(self):
        self.responses = [http.client.IncompleteRead(b""), ok(SUMMARY)]
        self.assertEqual(self.client.summarize(REPO), SUMMARY)
        self.assertEqual(self.sleeps, [10])

    def test_broken_http_responses_exhaust_to_unavailable(self):
        self.responses = [http.client.BadStatusLine("")] * 5
        with self.assertRaises(gemini.Unavailable) as caught:
            self.client.summarize(REPO)
        self.assertIn("BadStatusLine", str(caught.exception))

    def test_backoff_doubles(self):
        self.responses = [error(503, "UNAVAILABLE")] * 3 + [ok(SUMMARY)]
        self.client.summarize(REPO)
        self.assertEqual(self.sleeps, [10, 20, 40])

    def test_daily_quota_raises_unavailable_without_retry(self):
        self.responses = [error(429, "RESOURCE_EXHAUSTED",
                                quota("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))]
        with self.assertRaises(gemini.Unavailable):
            self.client.summarize(REPO)
        self.assertEqual(len(self.calls), 1)

    def test_server_errors_exhaust_to_unavailable(self):
        self.responses = [error(503, "UNAVAILABLE")] * 5
        with self.assertRaises(gemini.Unavailable) as caught:
            self.client.summarize(REPO)
        self.assertEqual(len(self.calls), 5)
        self.assertNotIn(KEY, str(caught.exception))

    def test_invalid_key_raises_auth_error(self):
        info = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}
        self.responses = [error(400, "INVALID_ARGUMENT", info)]
        with self.assertRaises(gemini.AuthError) as caught:
            self.client.summarize(REPO)
        self.assertEqual(str(caught.exception), "chave do Gemini rejeitada")
        self.assertNotIn(KEY, str(caught.exception))

    def test_other_client_error_raises_auth_error(self):
        self.responses = [error(404, "NOT_FOUND")]
        with self.assertRaises(gemini.AuthError) as caught:
            self.client.summarize(REPO)
        self.assertEqual(str(caught.exception), "Gemini rejeitou o pedido: HTTP 404 NOT_FOUND")
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_invalid_argument_blames_the_repo_in_any_order(self):
        bad = error(400, "INVALID_ARGUMENT")
        for responses, expected in (([bad, ok(SUMMARY)], [gemini.SummaryFailed, SUMMARY]),
                                    ([ok(SUMMARY), bad], [SUMMARY, gemini.SummaryFailed])):
            with self.subTest(expected=expected):
                self.responses = responses
                client = gemini.Client(KEY, post=self.post, sleep=self.sleep,
                                       clock=lambda: self.now)
                outcomes = []
                for _ in range(2):
                    try:
                        outcomes.append(client.summarize(REPO))
                    except gemini.SummaryFailed:
                        outcomes.append(gemini.SummaryFailed)
                self.assertEqual(outcomes, expected)

    def test_bad_request_with_other_status_raises_auth_error(self):
        self.responses = [error(400, "FAILED_PRECONDITION")]
        with self.assertRaises(gemini.AuthError) as caught:
            self.client.summarize(REPO)
        self.assertEqual(str(caught.exception),
                         "Gemini rejeitou o pedido: HTTP 400 FAILED_PRECONDITION")

    def test_invalid_key_after_a_success_raises_auth_error(self):
        info = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}
        self.responses = [ok(SUMMARY), error(400, "INVALID_ARGUMENT", info)]
        self.client.summarize(REPO)
        with self.assertRaises(gemini.AuthError):
            self.client.summarize(REPO)

    def test_other_client_error_after_a_success_raises_auth_error(self):
        self.responses = [ok(SUMMARY), error(404, "NOT_FOUND")]
        self.client.summarize(REPO)
        with self.assertRaises(gemini.AuthError):
            self.client.summarize(REPO)

    def test_forbidden_raises_auth_error(self):
        self.responses = [error(403, "PERMISSION_DENIED")]
        with self.assertRaises(gemini.AuthError):
            self.client.summarize(REPO)

    def test_invalid_output_retried_then_fails(self):
        self.responses = [ok(dict(SUMMARY, maturity="alpha"))] * 2
        with self.assertRaises(gemini.SummaryFailed):
            self.client.summarize(REPO)
        self.assertEqual(len(self.calls), 2)

    def test_invalid_output_then_valid_succeeds(self):
        self.responses = [ok("not json"), ok(SUMMARY)]
        self.assertEqual(self.client.summarize(REPO), SUMMARY)

    def test_blocked_prompt_fails(self):
        self.responses = [(200, {"promptFeedback": {"blockReason": "SAFETY"}})]
        with self.assertRaises(gemini.SummaryFailed):
            self.client.summarize(REPO)
        self.assertEqual(len(self.calls), 1)

    def test_finish_reason_other_than_stop_fails(self):
        status, body = ok(SUMMARY)
        body["candidates"][0]["finishReason"] = "MAX_TOKENS"
        self.responses = [(status, body)]
        with self.assertRaises(gemini.SummaryFailed):
            self.client.summarize(REPO)


if __name__ == "__main__":
    unittest.main()
