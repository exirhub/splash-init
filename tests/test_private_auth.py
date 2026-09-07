"""Private-repository bootstrap checks with fake HTTP; never touch the host."""

import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "ghs_PRIVATE.FIXTURE/+~123-=="
COMMIT = "a" * 40

# The fake transport records only artificial test data. No real network is used.
CURL_STUB = r'''
import json, os, pathlib, shutil, sys
from urllib.parse import urlparse, unquote
arguments = sys.argv[1:]
configuration = ""
if "--config" in arguments:
    config_path = arguments[arguments.index("--config") + 1]
    configuration = sys.stdin.read() if config_path == "-" else pathlib.Path(config_path).read_text()
url = next((item for item in arguments if item.startswith("https://")), "")
output = arguments[arguments.index("--output") + 1]
with open(os.environ["CURL_LOG"], "a") as log:
    log.write(json.dumps({"args": arguments, "config": configuration, "url": url,
                          "exported_token": os.environ.get("SPLASH_GITHUB_TOKEN")}) + "\n")
status = os.environ.get("MOCK_HTTP_STATUS", "200")
destination = pathlib.Path(output)
if status != "200":
    destination.write_text('{"message":"not authorized"}')
elif os.environ.get("MOCK_EMPTY_RESPONSE"):
    destination.write_bytes(b"")
else:
    path = unquote(urlparse(url).path)
    if "/commits/" in path:
        destination.write_text(json.dumps({"sha": "a" * 40}))
    elif "/contents/" in path:
        relative = path.split("/contents/", 1)[1]
        special = os.environ.get("MOCK_INSTALLER_PAYLOAD") if relative == "install.sh" else None
        if special:
            shutil.copyfile(special, destination)
        elif relative.endswith(".db"):
            shutil.copyfile(os.environ["FIXTURE_SEED"], destination)
        else:
            shutil.copyfile(pathlib.Path(os.environ["FIXTURE_REPO"]) / relative, destination)
    else:
        destination.write_text("public-download\n")
if "--write-out" in arguments or "-w" in arguments:
    sys.stdout.write(status)
sys.exit(int(os.environ.get("MOCK_CURL_EXIT", "0")))
'''


class PrivateGitHubTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.commands = self.directory / "bin"
        self.commands.mkdir()
        fake_curl = self.commands / "curl"
        fake_curl.write_text(f"#!{sys.executable}\n" + CURL_STUB)
        fake_curl.chmod(0o755)
        self.log = self.directory / "http.jsonl"
        self.work = self.directory / "work"
        self.work.mkdir()
        self.seed = self.directory / "fixture.db"
        config = {
            "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {}}],
            "routing": {"rules": [{"type": "field", "outboundTag": "direct"}]},
        }
        with sqlite3.connect(self.seed) as database:
            database.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            database.execute("INSERT INTO settings VALUES (?, ?)",
                             ("xrayTemplateConfig", json.dumps(config)))

    def environment(self, *, token=TOKEN, extra_env=None, source=None):
        environment = os.environ.copy()
        environment.pop("SPLASH_GITHUB_TOKEN", None)
        environment.pop("SPLASH_REF", None)
        environment.update({
            "PATH": str(self.commands) + os.pathsep + environment.get("PATH", ""),
            "INSTALL_SCRIPT": str(source or ROOT / "install.sh"),
            "FIXTURE_DIRECTORY": str(self.directory),
            "FIXTURE_REPO": str(ROOT),
            "FIXTURE_SEED": str(self.seed),
            "CURL_LOG": str(self.log),
            "TMPDIR": str(self.directory),
        })
        if token is not None:
            environment["SPLASH_GITHUB_TOKEN"] = token
        environment.update(extra_env or {})
        return environment

    def invoke(self, shell, *, token=TOKEN, extra_env=None, source=None, trace=False):
        environment = self.environment(token=token, extra_env=extra_env, source=source)
        arguments = ["/bin/bash", "--noprofile", "--norc"]
        if trace:
            arguments.append("-x")
        arguments += ["-c", 'source "$INSTALL_SCRIPT"\n'
                      'WORK_DIR="$FIXTURE_DIRECTORY/work"\n'
                      'ensure_github_dns() { return 0; }\n' + shell]
        # A new session has no controlling terminal. Missing-token tests must
        # fail promptly instead of accidentally prompting a CI/operator terminal.
        return subprocess.run(arguments, text=True, capture_output=True,
                              env=environment, timeout=15, start_new_session=True)

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] \
            if self.log.exists() else []

    def assert_private_request(self, request):
        self.assertTrue(request["url"].startswith(
            "https://api.github.com/repos/exirhub/splash-init/"))
        self.assertIn("Authorization: Bearer " + TOKEN, request["config"])
        self.assertNotIn(TOKEN, json.dumps(request["args"]))
        self.assertIsNone(request["exported_token"])
        self.assertIn(request["args"][0], ("-q", "--disable"))
        self.assertNotIn("--location", request["args"])
        self.assertNotIn("--location-trusted", request["args"])

    def test_authenticated_download_keeps_token_out_of_arguments_and_trace(self):
        result = self.invoke(
            'download_splash_file install.sh main "$WORK_DIR/install.sh"', trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / "install.sh").read_bytes(), (ROOT / "install.sh").read_bytes())
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        requests = self.requests()
        self.assertEqual(len(requests), 1)
        self.assert_private_request(requests[0])
        self.assertIn("application/vnd.github.raw+json",
                      requests[0]["config"] + " ".join(requests[0]["args"]))

    def test_ref_resolution_authenticates_and_returns_immutable_commit(self):
        result = self.invoke('resolve_ref')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), COMMIT)
        self.assertEqual(len(self.requests()), 1)
        self.assert_private_request(self.requests()[0])
        self.assertIn("/commits/main", self.requests()[0]["url"])

    def test_complete_bundle_and_binary_seed_use_one_pinned_private_ref(self):
        isolated = self.directory / "isolated"
        isolated.mkdir()
        entry = isolated / "install.sh"
        shutil.copyfile(ROOT / "install.sh", entry)
        result = self.invoke('prepare_bundle\nprepare_seed', source=entry)
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = self.requests()
        contents = [item for item in requests if "/contents/" in item["url"]]
        self.assertEqual(len(contents), 8, "Seven runtime files and one seed must be fetched")
        for request in requests:
            self.assert_private_request(request)
        for request in contents:
            self.assertIn("ref=" + COMMIT, request["url"])
        self.assertTrue(any("/contents/x-ui.db?" in item["url"] for item in contents))
        self.assertFalse(any("x-ui-ads.db" in item["url"] for item in contents))
        self.assertEqual((self.work / "bundle/x-ui.db").read_bytes(), self.seed.read_bytes())

    def test_public_3x_ui_download_has_no_github_credential(self):
        result = self.invoke('download_file '
                             'https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh '
                             '"$WORK_DIR/public.sh"')
        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.requests()[0]
        self.assertNotIn(TOKEN, json.dumps(request))
        self.assertNotIn("Authorization", request["config"])
        self.assertIsNone(request["exported_token"])
        self.assertIn(request["args"][0], ("-q", "--disable"))

    def test_private_header_is_refused_for_unrelated_host_or_repository(self):
        for url in (
            "https://attacker.invalid/repos/exirhub/splash-init/contents/install.sh",
            "https://api.github.com/repos/someone/other/contents/install.sh",
            "https://api.github.com@attacker.invalid/repos/exirhub/splash-init/contents/install.sh",
        ):
            with self.subTest(url=url):
                result = self.invoke('github_api_file "$BAD_URL" "$WORK_DIR/rejected"',
                                     extra_env={"BAD_URL": url})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.requests(), [])
                self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_no_token_without_terminal_fails_before_any_http_request(self):
        result = self.invoke('download_splash_file install.sh main "$WORK_DIR/install.sh"',
                             token=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SPLASH_GITHUB_TOKEN", result.stdout + result.stderr)
        self.assertEqual(self.requests(), [])

    def test_invalid_token_is_rejected_without_header_injection_or_disclosure(self):
        for token in ("bad-token\nheader=Injected", "bad\"token", "bad token",
                      "bad\\token", "bad'token", "bad\rtoken", "bad\ttoken", "bad=token"):
            with self.subTest(token=repr(token)):
                result = self.invoke('download_splash_file install.sh main "$WORK_DIR/install.sh"',
                                     token=token)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.requests(), [])
                self.assertNotIn(token, result.stdout + result.stderr)

    def test_standard_bearer_token_characters_are_preserved(self):
        for token in ("github_pat_letters_123", "ghs_a.b/c+d~e-f", "payload=="):
            with self.subTest(token=token):
                result = self.invoke('download_splash_file install.sh main "$WORK_DIR/install.sh"',
                                     token=token)
                self.assertEqual(result.returncode, 0, result.stderr)
                request = self.requests()[-1]
                self.assertIn("Authorization: Bearer " + token, request["config"])
                self.assertNotIn(token, json.dumps(request["args"]))
                self.assertIsNone(request["exported_token"])
                self.assertNotIn(token, result.stdout + result.stderr)

    def test_api_errors_and_empty_responses_leave_no_partial_download(self):
        for response in ("401", "403", "404", "302", "empty", "transport"):
            with self.subTest(response=response):
                target = self.work / "install.sh"
                target.unlink(missing_ok=True)
                options = {"MOCK_HTTP_STATUS": response}
                if response == "empty":
                    options = {"MOCK_EMPTY_RESPONSE": "1"}
                if response == "transport":
                    options = {"MOCK_CURL_EXIT": "7"}
                result = self.invoke('download_splash_file install.sh main "$WORK_DIR/install.sh"',
                                     extra_env=options)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(target.exists(), "Failed private downloads must not be executable")
                self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_cleanup_removes_private_bundle_and_does_not_persist_credential(self):
        result = self.invoke('trap cleanup EXIT\n'
                             'download_splash_file install.sh main "$WORK_DIR/install.sh"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.work.exists())
        for path in self.directory.rglob("*"):
            if path.is_file() and path != self.log:
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), str(path))

    def child_installer(self):
        """Record inherited test auth, then perform one real nested helper call."""
        child = self.directory / "child.sh"
        child.write_text(r'''#!/bin/bash
set -Eeuo pipefail
printf '%s\n' "$SPLASH_GITHUB_TOKEN" > "$CHILD_LOG"
if (( $# )); then printf '%s\n' "$@" >> "$CHILD_LOG"; fi
source "$FIXTURE_REPO/install.sh"
WORK_DIR="$(dirname -- "$0")"
download_splash_file helpers/manage.py main "$WORK_DIR/helper.py"
''')
        return child

    def assert_bootstrap_requests(self, result, *, expected_arguments):
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertEqual((self.directory / "child.log").read_text().splitlines(),
                         [TOKEN, *expected_arguments])
        requests = self.requests()
        self.assertEqual(len(requests), 2, "Bootstrap and child helper must both authenticate")
        for request in requests:
            self.assert_private_request(request)
            self.assertFalse(Path(request["args"][request["args"].index("--output") + 1]).exists(),
                             "Bootstrap temp files must be cleaned on exit")

    def test_rendered_updater_keeps_auth_transient_and_updates_nested_downloads(self):
        rendered = self.invoke('render_updater')
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertNotIn(TOKEN, rendered.stdout + rendered.stderr)
        updater = self.directory / "updater.sh"
        # Only bypass the privilege guard in an isolated fixture. The updater
        # itself performs downloads only; its installed child is replaced above.
        updater.write_text(rendered.stdout.replace('[[ $EUID -eq 0 ]]', '[[ 1 -eq 1 ]]'))
        environment = self.environment(extra_env={
            "MOCK_INSTALLER_PAYLOAD": str(self.child_installer()),
            "CHILD_LOG": str(self.directory / "child.log"),
        })
        result = subprocess.run(["/bin/bash", str(updater)], text=True,
                                capture_output=True, env=environment, timeout=15,
                                start_new_session=True)
        self.assert_bootstrap_requests(result, expected_arguments=["--update-only"])
        self.assertNotIn(TOKEN, updater.read_text(), "The reusable updater cannot retain a PAT")

    def test_aws_bootstrap_authenticates_and_passes_same_token_to_child(self):
        isolated = self.directory / "aws"
        isolated.mkdir()
        source = isolated / "aws.sh"
        source.write_text((ROOT / "aws.sh").read_text().replace(
            '[[ $EUID -eq 0 ]]', '[[ 1 -eq 1 ]]'))
        result = self.invoke('main --update-only', source=source, extra_env={
            "MOCK_INSTALLER_PAYLOAD": str(self.child_installer()),
            "CHILD_LOG": str(self.directory / "child.log"),
        })
        self.assert_bootstrap_requests(result, expected_arguments=["--update-only"])

    def run_with_hidden_token(self, script, environment):
        """A real controlling terminal verifies read -s, without sudo or SSH."""
        pid, terminal = pty.fork()
        if pid == 0:
            os.execve("/bin/bash", ["/bin/bash", str(script)], environment)
        output = bytearray()
        sent = False
        completed = False
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([terminal], [], [], 0.05)
                if ready:
                    try:
                        data = os.read(terminal, 65536)
                    except OSError:
                        data = b""
                    output.extend(data)
                if not sent and b"GitHub token:" in output:
                    # Never send until the terminal's echo really is disabled.
                    if not (termios.tcgetattr(terminal)[3] & termios.ECHO):
                        os.write(terminal, (TOKEN + "\n").encode())
                        sent = True
                ended, status = os.waitpid(pid, os.WNOHANG)
                if ended:
                    completed = True
                    self.assertTrue(sent, "README must ask for the token via hidden input")
                    return subprocess.CompletedProcess(
                        [str(script)], os.waitstatus_to_exitcode(status),
                        output.decode(errors="replace"), "")
            self.fail("Hidden-token bootstrap did not finish")
        finally:
            if not completed:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(terminal)

    def test_readme_paste_block_prompts_once_and_authenticates_nested_downloads(self):
        readme = (ROOT / "README.md").read_text()
        body = readme.split("sudo bash <<'SPLASH_INSTALL'\n", 1)[1].split("\nSPLASH_INSTALL", 1)[0]
        snippet = self.directory / "readme.sh"
        snippet.write_text(body + "\n")
        # An unexpected package installation fails immediately, including on a
        # test host missing its ordinary CA bundle. No host writes are allowed.
        apt = self.commands / "apt-get"
        apt.write_text("#!/bin/sh\necho 'Unexpected host package installation' >&2\nexit 97\n")
        apt.chmod(0o755)
        environment = self.environment(token=None, extra_env={
            "MOCK_INSTALLER_PAYLOAD": str(self.child_installer()),
            "CHILD_LOG": str(self.directory / "child.log"),
        })
        result = self.run_with_hidden_token(snippet, environment)
        self.assert_bootstrap_requests(result, expected_arguments=[])


if __name__ == "__main__":
    unittest.main()
