"""Optional OpenAI-compatible backend, exercised against a local mock HTTP server."""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from solvent import llm_backend, nemotron

SECRET = "sk-test-SUPERSECRET-123"


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list[dict] = []
    redirect_to = ""

    def log_message(self, *a):  # keep test output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        )
        if self.mode.startswith("redirect"):
            code = int(self.mode.split(":")[1])
            self.send_response(code)
            self.send_header("Location", type(self).redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.mode == "slow":
            time.sleep(1.5)
        if self.mode == "500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "boom ' + SECRET.encode() + b'"}')
            return
        if self.mode == "garbage":
            payload = b"not json"
        else:
            payload = json.dumps(
                {
                    "choices": [{"message": {"content": "# Brief from local model"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                }
            ).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


class _Catcher(BaseHTTPRequestHandler):
    """Stands in for a different host a malicious redirect points at."""

    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def _record(self):
        type(self).seen.append({"method": self.command, "auth": self.headers.get("Authorization")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"choices": [{"message": {"content": "stolen"}}]}')

    do_GET = do_POST = _record


class LLMBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.catcher = HTTPServer(("127.0.0.1", 0), _Catcher)
        # "localhost" vs "127.0.0.1": a different origin from the backend.
        cls.catcher_url = f"http://localhost:{cls.catcher.server_address[1]}/steal"
        threading.Thread(target=cls.catcher.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.catcher.shutdown()
        cls.catcher.server_close()

    def setUp(self):
        _Handler.mode = "ok"
        _Handler.seen = []
        _Handler.redirect_to = self.catcher_url
        _Catcher.seen = []

    def _env(self, **extra):
        env = {"SOLVENT_LLM_BASE_URL": self.url, "SOLVENT_LLM_MODEL": "llama3.2"}
        env.update(extra)
        return mock.patch.dict("os.environ", env, clear=False)

    def test_disabled_by_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(llm_backend.enabled())
            text, _ = nemotron.complete("sys", "Research topic: x")
        self.assertIn("offline Nemotron stub", text)
        self.assertEqual(_Handler.seen, [])

    def test_defaults_to_ollama_url(self):
        with mock.patch.dict("os.environ", {"SOLVENT_LLM_MODEL": "qwen2.5"}, clear=True):
            self.assertTrue(llm_backend.enabled())
            self.assertEqual(llm_backend.base_url(), "http://localhost:11434/v1")
            self.assertEqual(llm_backend.model(), "qwen2.5")

    def test_success_uses_backend_and_sends_key_only_in_header(self):
        with self._env(SOLVENT_LLM_API_KEY=SECRET):
            text, usage = nemotron.complete("sys", "Research topic: chips")
        self.assertEqual(text, "# Brief from local model")
        self.assertEqual(usage["total_tokens"], 18)
        self.assertFalse(usage["estimated"])
        req = _Handler.seen[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["auth"], f"Bearer {SECRET}")
        self.assertEqual(req["body"]["model"], "llama3.2")
        self.assertNotIn(SECRET, json.dumps(req["body"]))

    def test_no_auth_header_without_key(self):
        with self._env():
            nemotron.complete("sys", "Research topic: chips")
        self.assertIsNone(_Handler.seen[0]["auth"])

    def test_wins_over_offline_config(self):
        with mock.patch.object(nemotron, "_force_offline", True), self._env():
            text, _ = nemotron.complete("sys", "Research topic: chips")
        self.assertEqual(text, "# Brief from local model")

    def _assert_falls_back(self, mode, **extra):
        _Handler.mode = mode
        with (
            self._env(SOLVENT_LLM_API_KEY=SECRET, **extra),
            self.assertLogs("solvent.llm_backend", level="WARNING") as logs,
        ):
            text, usage = nemotron.complete("sys", "Research topic: chips")
        self.assertIn("offline Nemotron stub", text)
        self.assertTrue(usage["estimated"])
        self.assertNotIn(SECRET, text)
        self.assertNotIn(SECRET, "\n".join(logs.output))
        return text

    def test_http_error_falls_back_without_leaking(self):
        text = self._assert_falls_back("500")
        self.assertIn("HTTP 500", text)

    def test_malformed_response_falls_back(self):
        self.assertIn("malformed", self._assert_falls_back("garbage"))

    def test_timeout_falls_back(self):
        start = time.monotonic()
        self._assert_falls_back("slow", SOLVENT_LLM_TIMEOUT="0.3")
        self.assertLess(time.monotonic() - start, 1.4)

    def test_unreachable_falls_back(self):
        _Handler.mode = "ok"
        with mock.patch.dict(
            "os.environ",
            {"SOLVENT_LLM_BASE_URL": "http://127.0.0.1:1/v1", "SOLVENT_LLM_API_KEY": SECRET},
        ):
            text, _ = nemotron.complete("sys", "Research topic: chips")
        self.assertIn("offline Nemotron stub", text)
        self.assertIn("unreachable", text)
        self.assertNotIn(SECRET, text)

    def test_bad_scheme_rejected(self):
        with mock.patch.dict("os.environ", {"SOLVENT_LLM_BASE_URL": "file:///etc"}):
            with self.assertRaises(llm_backend.LLMBackendError):
                llm_backend.complete("s", "u")

    def test_describe_never_includes_key(self):
        with self._env(SOLVENT_LLM_API_KEY=SECRET):
            self.assertNotIn(SECRET, llm_backend.describe())
            self.assertIn("api key set: yes", llm_backend.describe())

    def test_bad_timeout_uses_default(self):
        with mock.patch.dict("os.environ", {"SOLVENT_LLM_TIMEOUT": "abc"}):
            self.assertEqual(llm_backend.timeout(), llm_backend.DEFAULT_TIMEOUT)


    def test_redirects_are_refused_and_key_never_forwarded(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                _Handler.mode = f"redirect:{code}"
                _Catcher.seen = []
                with self._env(SOLVENT_LLM_API_KEY=SECRET):
                    with self.assertRaises(llm_backend.LLMBackendError) as ctx:
                        llm_backend.complete("s", "u")
                self.assertIn("redirect", str(ctx.exception))
                self.assertNotIn(SECRET, str(ctx.exception))
                self.assertEqual(_Catcher.seen, [], "redirect target must never be contacted")

    def test_redirect_falls_back_to_stub_without_leaking(self):
        text = self._assert_falls_back("redirect:302")
        self.assertIn("redirect", text)
        self.assertEqual(_Catcher.seen, [])


if __name__ == "__main__":
    unittest.main()
