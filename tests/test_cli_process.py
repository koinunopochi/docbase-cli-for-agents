from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "docbase"


def run_cli(*args: str, **env_values: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("DOCBASE_DOMAIN", None)
    env.pop("DOCBASE_TEAM", None)
    env.pop("DOCBASE_API_TOKEN", None)
    env.update(env_values)
    return subprocess.run(
        [str(CLI), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class CliProcessContractTest(unittest.TestCase):
    def test_root_help_is_available_without_credentials(self) -> None:
        result = run_cli("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("--confirm", result.stdout)
        self.assertIn("Errors: JSON on stderr", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_mutation_help_exposes_confirmation_and_side_effect(self) -> None:
        result = run_cli("create-comment", "--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("--confirm", result.stdout)
        self.assertIn("without it no API request is made", result.stdout)

    def test_missing_configuration_is_structured_and_does_not_echo_secret(self) -> None:
        result = run_cli("profile")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "configuration_error")
        self.assertIn("DOCBASE_API_TOKEN", payload["error"]["message"])
        self.assertNotIn("secret-token", result.stderr)

    def test_invalid_argument_is_rejected_before_api_request(self) -> None:
        result = run_cli(
            "search-posts",
            "--query",
            "query",
            "--page",
            "0",
            DOCBASE_DOMAIN="example-team",
            DOCBASE_API_TOKEN="secret-token",
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "invalid_argument")
        self.assertNotIn("secret-token", result.stderr)

    def test_mutation_without_confirmation_is_rejected_before_network(self) -> None:
        result = run_cli(
            "create-comment",
            "--post-id",
            "12",
            "--body",
            "hello",
            DOCBASE_DOMAIN="example-team",
            DOCBASE_API_TOKEN="secret-token",
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "confirmation_required")
        self.assertEqual(payload["error"]["details"]["target"], {"post_id": 12})
        self.assertNotIn("secret-token", result.stderr)


if __name__ == "__main__":
    unittest.main()
