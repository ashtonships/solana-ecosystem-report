"""Exercise the dispatch script offline without exposing account metadata."""

import base64
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import textwrap
import unittest
from unittest.mock import MagicMock, patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/source-preflight.yml"


def load_script():
    source = WORKFLOW.read_text().split("python3 - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0]
    namespace = {"__name__": "source_preflight_test"}
    exec(compile(textwrap.dedent(source), str(WORKFLOW), "exec"), namespace)
    return namespace


class SourcePreflightTests(unittest.TestCase):
    def payload(self, spelling="billing_periods"):
        year = datetime.now(timezone.utc).year
        return {spelling: [{"start_date": f"{year}-01-01", "end_date": f"{year + 1}-01-01",
                            "credits_used": 19.75, "credits_included": 2500}],
                "private_account_name": "DO-NOT-DISCLOSE"}

    def run_script(self, payload, error=None, today=None):
        namespace = load_script()
        if today is not None:
            namespace["datetime"] = MagicMock(wraps=datetime)
            namespace["datetime"].now.return_value = datetime.fromisoformat(today).replace(tzinfo=timezone.utc)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        opener = MagicMock()
        opener.open.return_value = response
        opener.open.side_effect = error
        printed = io.StringIO()
        encrypted_input = []

        def openssl(command, **kwargs):
            self.assertNotIn("DUNE_API_KEY", os.environ)
            self.assertNotIn("PREFLIGHT_PUBLIC_KEY", os.environ)
            self.assertTrue(kwargs["capture_output"])
            if command[1] == "pkey":
                return subprocess.CompletedProcess(command, 0, b"Public-Key: (4096 bit)\n", b"")
            self.assertEqual(command[1:4], ["pkeyutl", "-encrypt", "-pubin"])
            self.assertIn("rsa_padding_mode:oaep", command)
            self.assertIn("rsa_oaep_md:sha256", command)
            encrypted_input.append(kwargs["input"])
            return subprocess.CompletedProcess(command, 0, b"x" * 512, b"")

        with patch.dict(os.environ, {"DUNE_API_KEY": "SECRET-KEY-DO-NOT-PRINT",
                                     "PREFLIGHT_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\nfake"}), \
                patch("urllib.request.build_opener", return_value=opener) as build, \
                patch("subprocess.run", side_effect=openssl), redirect_stdout(printed):
            status = namespace["main"]()
        return status, printed.getvalue(), opener, response, build, encrypted_input

    def test_dispatch_boundary_and_private_success(self):
        workflow = WORKFLOW.read_text()
        triggers = workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("schedule:", triggers)
        self.assertNotIn("push:", triggers)
        self.assertIn("workflow_dispatch:", triggers)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("uses:", workflow)
        for spelling in ("billing_periods", "billingPeriods"):
            with self.subTest(spelling=spelling):
                status, output, opener, response, build, encrypted = self.run_script(self.payload(spelling))
                self.assertEqual(status, 0)
                opener.open.assert_called_once()
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://api.dune.com/api/v1/usage")
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.data, b"{}")
                self.assertEqual(opener.open.call_args.kwargs, {"timeout": 15})
                response.read.assert_called_once_with(16385)
                self.assertIsNone(build.call_args.args[0].redirect_request(None, None, 302, "", {}, "https://other.test"))
                summary = json.loads(encrypted[0])
                self.assertEqual(summary["credits_used"], 19.75)
                self.assertEqual(summary["credits_included"], 2500)
                self.assertLess(len(encrypted[0]), 400)
                self.assertEqual(set(summary), {"observed_on", "start_date", "end_date", "credits_used", "credits_included"})
                self.assertEqual(base64.b64decode(output.splitlines()[0].split(":", 1)[1]), b"x" * 512)
                for private in ("19.75", "2500", "DO-NOT-DISCLOSE", "SECRET-KEY", "BEGIN PUBLIC KEY"):
                    self.assertNotIn(private, output)

    def test_malformed_metadata_is_redacted(self):
        invalid = [b"x" * 16385, b"not json SECRET-KEY", [], {}, {"billing_periods": []}]
        for value in (True, -1, float("nan"), float("inf"), "19.75", None):
            payload = self.payload()
            payload["billing_periods"][0]["credits_used"] = value
            invalid.append(payload)
        payload = self.payload()
        payload["billing_periods"] *= 2
        invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload_type=type(payload).__name__):
                status, output, opener, _, _, encrypted = self.run_script(payload)
                self.assertEqual(status, 1)
                self.assertEqual(output, "Source preflight failed; no account metadata disclosed.\n")
                self.assertEqual(encrypted, [])
                opener.open.assert_called_once()

    def test_transport_error_is_redacted_without_retry(self):
        status, output, opener, _, _, encrypted = self.run_script({}, RuntimeError("SECRET-KEY metadata 2500"))
        self.assertEqual(status, 1)
        self.assertEqual(output, "Source preflight failed; no account metadata disclosed.\n")
        self.assertEqual(encrypted, [])
        opener.open.assert_called_once()

    def test_current_period_boundaries_fail_closed(self):
        period = {"start_date": "2026-09-01", "end_date": "2026-10-01",
                  "credits_used": 0, "credits_included": 2500}
        for today, expected in (("2026-08-31", 1), ("2026-09-01", 0),
                                ("2026-09-30", 0), ("2026-10-01", 1)):
            with self.subTest(today=today):
                status, output, _, _, _, encrypted = self.run_script(
                    {"billing_periods": [period]}, today=today,
                )
                self.assertEqual(status, expected)
                self.assertEqual(bool(encrypted), expected == 0)
                self.assertNotIn("2500", output)
        for changed in ({"start_date": "invalid"}, {"start_date": "2026-10-01"},
                        {"end_date": "2026-08-01"}, {"start_date": True}):
            status, output, _, _, _, encrypted = self.run_script(
                {"billing_periods": [{**period, **changed}]}, today="2026-09-05",
            )
            self.assertEqual(status, 1)
            self.assertEqual(encrypted, [])
            self.assertEqual(output, "Source preflight failed; no account metadata disclosed.\n")


if __name__ == "__main__":
    unittest.main()
