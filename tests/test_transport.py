"""Offline checks for shared bounded retries on transient fetch failures."""

import os
import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import economics  # noqa: E402
import transport  # noqa: E402


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.test", code=code, msg="err",
        hdrs=None, fp=BytesIO(b""),
    )


class FakeResponse:
    def __init__(self, body: bytes = b'{"ok": true}'):
        self._body = body

    def read(self, limit: int = -1) -> bytes:
        if limit < 0:
            return self._body
        return self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFetchWithRetry(unittest.TestCase):
    def test_success_first_attempt_never_sleeps(self):
        sleeps = []
        calls = []

        def opener():
            calls.append(1)
            return FakeResponse()

        result = transport.fetch_with_retry(
            opener, sleep=sleeps.append, initial_delay=0.25, backoff=2.0,
        )
        self.assertEqual(result.read(), b'{"ok": true}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_retries_429_and_5xx_with_bounded_backoff_then_succeeds(self):
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                sleeps = []
                attempts = []

                def opener(code=code, attempts=attempts):
                    attempts.append(1)
                    if len(attempts) < 3:
                        raise http_error(code)
                    return FakeResponse()

                response = transport.fetch_with_retry(
                    opener, sleep=sleeps.append,
                    initial_delay=0.5, backoff=2.0,
                )
                self.assertEqual(len(attempts), 3)
                self.assertEqual(sleeps, [0.5, 1.0])
                self.assertIsNotNone(response)

    def test_exhausted_retries_raise_last_error(self):
        attempts = []

        def opener():
            attempts.append(1)
            raise http_error(503)

        with self.assertRaises(urllib.error.HTTPError):
            transport.fetch_with_retry(
                opener, sleep=lambda _: None, initial_delay=0.1,
            )
        self.assertEqual(len(attempts), transport.RETRY_ATTEMPTS)

    def test_client_errors_fail_fast_without_sleep(self):
        sleeps = []
        attempts = []

        def opener():
            attempts.append(1)
            raise http_error(403)

        with self.assertRaises(urllib.error.HTTPError):
            transport.fetch_with_retry(opener, sleep=sleeps.append)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sleeps, [])

    def test_network_errors_are_retryable(self):
        sleeps = []
        attempts = []

        def opener():
            attempts.append(1)
            if len(attempts) < 2:
                raise urllib.error.URLError("connection refused")
            return FakeResponse()

        transport.fetch_with_retry(opener, sleep=sleeps.append)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [transport.RETRY_INITIAL_DELAY_SECONDS])


class TestEconomicsFetchUsesRetry(unittest.TestCase):
    def test_fetch_retries_429_before_degrading_to_none(self):
        sleeps = []
        attempts = []

        def spy(request, timeout=None):
            attempts.append(1)
            if len(attempts) < 2:
                raise http_error(429)
            return FakeResponse()

        with patch.object(economics.urllib.request, "urlopen", spy), \
                patch.object(economics.transport, "_sleep",
                             side_effect=sleeps.append):
            self.assertEqual(economics.fetch("https://example.test/tvl"), {"ok": True})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [transport.RETRY_INITIAL_DELAY_SECONDS])

    def test_fetch_price_only_retries_503_before_reporting_unavailable(self):
        attempts = []

        def spy(request, timeout=None):
            attempts.append(1)
            if len(attempts) < 2:
                raise http_error(503)
            return FakeResponse()

        env = dict(os.environ)
        os.environ[economics.COINGECKO_DEMO_KEY_ENV] = "test-key"
        try:
            with patch.object(economics.urllib.request, "urlopen", spy), \
                    patch.object(economics.transport, "_sleep", lambda _: None):
                result = economics.fetch_price_only()
        finally:
            os.environ.clear()
            os.environ.update(env)
        self.assertEqual(result, {"price": {"ok": True}})
        self.assertEqual(len(attempts), 2)

    def test_fetch_does_not_retry_client_errors(self):
        attempts = []

        def spy(request, timeout=None):
            attempts.append(1)
            raise http_error(404)

        with patch.object(economics.urllib.request, "urlopen", spy), \
                patch.object(economics.transport, "_sleep", lambda _: None):
            self.assertIsNone(economics.fetch("https://example.test/tvl"))
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
