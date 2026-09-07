"""Safe installer and repository checks; never install into the host system."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "helpers" / "manage.py"


def create_database(path, *, route_to_balancer=True):
    """An artificial seed; no production data or network connections."""
    config = {
        "outbounds": [
            {"tag": "direct", "protocol": "freedom", "settings": {}},
            {
                "tag": "TH-1",
                "protocol": "socks",
                "settings": {"servers": [{
                    "address": "192.0.2.77", "port": 1080,
                    "users": [{"user": "fixture-user", "pass": "fixture-password"}],
                }]},
            },
        ],
        "routing": {
            "rules": [{"type": "field", "balancerTag": "ADMOB-BALANCER"}]
            if route_to_balancer else [{"type": "field", "outboundTag": "direct"}],
            "balancers": [{
                "tag": "ADMOB-BALANCER", "selector": ["TH-1"],
                "strategy": {"type": "random"},
            }],
        },
    }
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO settings VALUES (?, ?)",
                           ("xrayTemplateConfig", json.dumps(config)))
    return config


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def invoke(self, *args, extra_env=None):
        self.assertTrue(HELPER.is_file(), "Installer helper must be included")
        env = os.environ.copy()
        for key in ("PROXYFLEET_OUTBOUNDS_URL", "PROXYFLEET_OUTBOUNDS_TOKEN"):
            env.pop(key, None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(HELPER), *map(str, args)],
                              capture_output=True, text=True, env=env, timeout=10)

    def test_seed_validation_is_read_only_and_does_not_expose_credentials(self):
        database = self.directory / "seed.db"
        create_database(database)
        original = database.read_bytes()
        result = self.invoke("validate-seed", database)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(database.read_bytes(), original)
        for private_value in ("fixture-user", "fixture-password", "192.0.2.77"):
            self.assertNotIn(private_value, result.stdout + result.stderr)

    def test_seed_without_an_admob_routing_reference_is_rejected_without_changes(self):
        database = self.directory / "seed.db"
        create_database(database, route_to_balancer=False)
        original = database.read_bytes()
        result = self.invoke("validate-seed", database)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(database.read_bytes(), original)

    def test_invalid_database_is_rejected(self):
        database = self.directory / "invalid.db"
        database.write_bytes(b"This is not an SQLite database")
        result = self.invoke("validate-seed", database)
        self.assertNotEqual(result.returncode, 0)

    def test_backup_contains_committed_wal_data_and_is_private(self):
        database = self.directory / "source.db"
        create_database(database)
        backup = self.directory / "backup.db"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO settings VALUES ('recent-setting', 'committed')")
            connection.commit()
            self.assertTrue(Path(str(database) + "-wal").exists())
            result = self.invoke("backup-db", database, backup)
            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(backup) as copied:
                self.assertEqual(copied.execute(
                    "SELECT value FROM settings WHERE key='recent-setting'"
                ).fetchone(), ("committed",))
                self.assertEqual(copied.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_backup_refuses_to_overwrite_existing_backup(self):
        database = self.directory / "source.db"
        create_database(database)
        backup = self.directory / "backup.db"
        backup.write_bytes(b"retained recovery copy")
        result = self.invoke("backup-db", database, backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(backup.read_bytes(), b"retained recovery copy")

    def configure(self, path, extra_env=None):
        return self.invoke("configure-env", "--env-file", path,
                           "--example", ROOT / "vendor/proxyfleet-xui-sync/proxyfleet-xui-sync.env.example",
                           "--xray-binary", "/fixture/xray-linux-arm64", extra_env=extra_env)

    def test_new_environment_uses_explicit_feed_and_private_permissions(self):
        path = self.directory / "sync.env"
        result = self.configure(path, {
            "PROXYFLEET_OUTBOUNDS_URL": "https://example.test/outbounds",
            "PROXYFLEET_OUTBOUNDS_TOKEN": "private-fixture-token",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text()
        self.assertIn("https://example.test/outbounds", text)
        self.assertIn("/fixture/xray-linux-arm64", text)
        self.assertNotIn("private-fixture-token", result.stdout + result.stderr)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_environment_keeps_values_and_adds_only_missing_defaults(self):
        path = self.directory / "sync.env"
        original = (
            "# operator-managed configuration\n"
            "PROXYFLEET_OUTBOUNDS_URL=https://example.test/existing\n"
            "PROXYFLEET_OUTBOUNDS_TOKEN=existing-secret\n"
            "MIN_CHANGES=9\n"
            "XRAY_BINARY=/keep/my/xray\n"
        )
        path.write_text(original)
        result = self.configure(path, {
            "PROXYFLEET_OUTBOUNDS_URL": "https://example.test/unwanted-replacement",
            "PROXYFLEET_OUTBOUNDS_TOKEN": "unwanted-secret",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        text = path.read_text()
        self.assertTrue(text.startswith(original))
        self.assertNotIn("unwanted-replacement", text)
        self.assertNotIn("unwanted-secret", text)
        self.assertNotIn("/fixture/xray-linux-arm64", text)
        self.assertIn("REQUIRE_READY=", text)
        again = self.configure(path)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(path.read_text(), text, "Repeated update must be idempotent")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_environment_rejects_control_character_injection(self):
        for variable in ("PROXYFLEET_OUTBOUNDS_URL", "PROXYFLEET_OUTBOUNDS_TOKEN"):
            with self.subTest(variable=variable):
                path = self.directory / f"{variable}.env"
                result = self.configure(path, {variable: "https://example.test/x\nDRY_RUN=true"})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(path.exists(), "Invalid inputs must not leave a partial config")

    def test_environment_rejects_non_http_feed(self):
        path = self.directory / "sync.env"
        result = self.configure(path, {"PROXYFLEET_OUTBOUNDS_URL": "file:///etc/passwd"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())

    def status_fixture(self):
        database = self.directory / "x-ui.db"
        config = create_database(database)
        runtime = self.directory / "config.json"
        runtime.write_text(json.dumps(config))
        state = self.directory / "state.json"
        state.write_text("{}")
        environment = self.directory / "status.env"
        environment.write_text(
            f'XUI_DB="{database}"\nXRAY_RUNTIME_CONFIG="{runtime}"\nSTATE_FILE="{state}"\n'
        )
        return environment, runtime, state

    def test_matching_installed_database_and_runtime_are_ready(self):
        environment, _, _ = self.status_fixture()
        result = self.invoke("status", "--env-file", environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["configuration_ready"])
        self.assertNotIn("fixture-password", result.stdout + result.stderr)

    def test_missing_runtime_is_pending_and_not_reported_as_ready(self):
        environment, runtime, _ = self.status_fixture()
        runtime.unlink()
        result = self.invoke("status", "--env-file", environment)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)["configuration_ready"])

    def test_deferred_topology_update_is_pending(self):
        environment, _, state = self.status_fixture()
        state.write_text(json.dumps({"restart_candidate": "pending-change"}))
        result = self.invoke("status", "--env-file", environment)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)["configuration_ready"])

    def test_dry_run_is_not_reported_as_ready(self):
        environment, _, _ = self.status_fixture()
        with environment.open("a") as handle:
            handle.write("DRY_RUN=true\n")
        result = self.invoke("status", "--env-file", environment)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)["configuration_ready"])

    def test_mismatched_runtime_selector_is_pending(self):
        environment, runtime, _ = self.status_fixture()
        config = json.loads(runtime.read_text())
        config["routing"]["balancers"][0]["selector"] = ["TH-99"]
        runtime.write_text(json.dumps(config))
        result = self.invoke("status", "--env-file", environment)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)["configuration_ready"])

    def test_invalid_state_or_runtime_shape_fails_cleanly(self):
        environment, runtime, state = self.status_fixture()
        original_runtime = runtime.read_text()
        for target in (runtime, state):
            with self.subTest(target=target.name):
                runtime.write_text(original_runtime)
                state.write_text("{}")
                target.write_text("[]")
                result = self.invoke("status", "--env-file", environment)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)

    def test_vendored_files_match_recorded_upstream_checksums(self):
        manifest = json.loads((ROOT / "vendor/proxyfleet-xui-sync.source.json").read_text())
        self.assertRegex(manifest["commit"], r"^[a-f0-9]{40}$")
        for filename, expected in manifest["files"].items():
            with self.subTest(filename=filename):
                content = (ROOT / "vendor/proxyfleet-xui-sync" / filename).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected)


class InstallerOrchestrationTests(unittest.TestCase):
    """Source functions only; every host-mutating operation is replaced."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.commands = self.directory / "bin"
        self.commands.mkdir()
        self.log = self.directory / "calls.log"
        for command, body in {
            "python3": 'printf "status\\n" >> "$CALL_LOG"\nexit "${MOCK_STATUS:-0}"\n',
            "systemctl": 'printf "systemctl:%s\\n" "$*" >> "$CALL_LOG"\nexit 0\n',
        }.items():
            script = self.commands / command
            script.write_text("#!/bin/sh\n" + body)
            script.chmod(0o755)

    def invoke(self, shell, *args, status=0, fail_step=""):
        env = os.environ.copy()
        env.update({
            "PATH": str(self.commands) + os.pathsep + env.get("PATH", ""),
            "INSTALL_SCRIPT": str(ROOT / "install.sh"),
            "FIXTURE_DIRECTORY": str(self.directory),
            "CALL_LOG": str(self.log),
            "MOCK_STATUS": str(status),
            "MOCK_FAIL_STEP": fail_step,
        })
        return subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c",
                               'source "$INSTALL_SCRIPT"\n' + shell, "fixture", *args],
                              capture_output=True, text=True, env=env, timeout=10)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    MAIN_STUBS = r'''
record() {
  printf '%s\n' "$1" >> "$CALL_LOG"
  if [[ "$1" == "$MOCK_FAIL_STEP" ]]; then return 47; fi
}
preflight() { record preflight; }
acquire_lock() { record lock; }
initialize_paths() {
  record paths
  WORK_DIR="$FIXTURE_DIRECTORY/work"
  mkdir -p "$WORK_DIR"
  XUI_DB_PATH="$FIXTURE_DIRECTORY/x-ui.db"
  XUI_BINARY="$FIXTURE_DIRECTORY/x-ui"
  SYNC_ENV_FILE="$FIXTURE_DIRECTORY/sync.env"
  KEEP_WORK_DIR=0
}
configure_persistent_dns() { record dns; }
install_prerequisites() { record prerequisites; }
prepare_bundle() { record bundle; BUNDLE_DIR="$FIXTURE_DIRECTORY"; BUNDLE_REF=local; }
verify_existing_xui() { record verify-existing; }
ensure_xui() { record ensure-xui; }
apply_host_defaults() { record host-defaults; }
configure_sync_env() { record environment; }
install_updater() { record updater; }
install_sync() { record sync; }
main "$@"
'''

    def test_sourcing_does_not_install_or_run_commands(self):
        result = self.invoke("printf 'sourced safely\\n'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "sourced safely\n")
        self.assertEqual(self.calls(), [])

    def test_update_only_preserves_host_and_xui(self):
        result = self.invoke(self.MAIN_STUBS, "--update-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("verify-existing", calls)
        self.assertNotIn("dns", calls)
        self.assertNotIn("ensure-xui", calls)
        self.assertNotIn("host-defaults", calls)
        self.assertLess(calls.index("environment"), calls.index("sync"))
        self.assertLess(calls.index("sync"), calls.index("status"))

    def test_normal_install_configures_xui_before_starting_sync(self):
        result = self.invoke(self.MAIN_STUBS)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("dns", calls)
        self.assertIn("host-defaults", calls)
        self.assertLess(calls.index("ensure-xui"), calls.index("environment"))
        self.assertLess(calls.index("environment"), calls.index("sync"))

    def test_sync_failure_is_not_reported_as_success(self):
        result = self.invoke(self.MAIN_STUBS, "--update-only", fail_step="sync")
        self.assertEqual(result.returncode, 47, result.stderr)
        self.assertNotIn("status", self.calls())
        self.assertNotIn("initialization complete", result.stdout.lower())

    def test_pending_sync_keeps_distinct_exit_status(self):
        result = self.invoke(self.MAIN_STUBS, "--update-only", status=2)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("SYNC PENDING", result.stdout)
        self.assertNotIn("initialization complete", result.stdout.lower())

    def test_lock_failure_stops_before_host_changes(self):
        result = self.invoke(self.MAIN_STUBS, fail_step="lock")
        self.assertEqual(result.returncode, 47, result.stderr)
        self.assertNotIn("dns", self.calls())
        self.assertNotIn("ensure-xui", self.calls())
        self.assertNotIn("sync", self.calls())

    def test_help_and_invalid_argument_never_reach_preflight(self):
        help_result = self.invoke(self.MAIN_STUBS, "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Usage:", help_result.stdout)
        self.assertEqual(self.calls(), [])
        invalid_result = self.invoke(self.MAIN_STUBS, "--unexpected")
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_existing_database_and_binary_are_never_reseeded_or_upgraded(self):
        database = self.directory / "x-ui.db"
        database.write_bytes(b"existing operator data")
        binary = self.directory / "x-ui"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        result = self.invoke(r'''
WORK_DIR="$FIXTURE_DIRECTORY"
XUI_DB_PATH="$FIXTURE_DIRECTORY/x-ui.db"
XUI_BINARY="$FIXTURE_DIRECTORY/x-ui"
prepare_seed() { printf 'unexpected seed\n' >&2; return 49; }
seed_database() { printf 'unexpected seed write\n' >&2; return 49; }
download_file() { printf 'unexpected upgrade\n' >&2; return 49; }
wait_for_xui() { return 0; }
ensure_xui
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(database.read_bytes(), b"existing operator data")
        self.assertIn("systemctl:start x-ui", self.calls())

    def test_custom_existing_database_is_refused_before_host_changes(self):
        (self.directory / "sync.env").write_text('XUI_DB="/custom/operator.db"\n')
        result = self.invoke(self.MAIN_STUBS)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("dns", self.calls())
        self.assertNotIn("ensure-xui", self.calls())
        self.assertNotIn("sync", self.calls())

    def test_downloaded_bundle_runs_its_pinned_installation_functions(self):
        pinned = self.directory / "install.sh"
        pinned.write_text("run_installation() { record pinned-installation; }\n")
        stubs = self.MAIN_STUBS.replace("BUNDLE_REF=local", "BUNDLE_REF=" + "a" * 40)
        result = self.invoke(stubs, "--update-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pinned-installation", self.calls())
        self.assertNotIn("sync", self.calls(), "Floating bootstrap functions must not run")

    def test_seed_recovery_backup_survives_failed_restart_and_cleanup(self):
        # All paths are temporary, service calls are functions, and owner flags
        # are stripped so CI never requires root or changes a system directory.
        (self.commands / "python3").unlink()
        database = self.directory / "x-ui.db"
        seed = self.directory / "seed.db"
        create_database(database)
        create_database(seed)
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO settings VALUES ('owner-data', 'keep-me')")
        result = self.invoke(r'''
WORK_DIR="$FIXTURE_DIRECTORY/work"
mkdir -p "$WORK_DIR"
XUI_DB_PATH="$FIXTURE_DIRECTORY/x-ui.db"
XUI_BACKUP_DIR="$FIXTURE_DIRECTORY/backups"
BUNDLE_DIR="${INSTALL_SCRIPT%/*}"
KEEP_WORK_DIR=0
trap cleanup EXIT
install() {
  local arguments=()
  while (( $# )); do
    case "$1" in -o|-g) shift 2 ;; *) arguments+=("$1"); shift ;; esac
  done
  command install "${arguments[@]}"
}
systemctl() {
  printf 'systemctl:%s\n' "$*" >> "$CALL_LOG"
  [[ "$1" != is-active ]]
}
pgrep() { return 1; }
wait_for_xui() { return 1; }
seed_database "$FIXTURE_DIRECTORY/seed.db"
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "work").exists())
        backups = list((self.directory / "backups").glob("pre-seed.*/x-ui.db"))
        self.assertEqual(len(backups), 1, result.stderr)
        for path in (database, backups[0]):
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute(
                    "SELECT value FROM settings WHERE key='owner-data'"
                ).fetchone(), ("keep-me",))
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)


class SeedDatabaseTests(unittest.TestCase):
    def test_repository_database_integrity(self):
        if not any((ROOT / name).exists() for name in ("x-ui.db", "x-ui-ads.db")):
            if os.environ.get("REQUIRE_SPLASH_SEEDS") != "1":
                self.skipTest("Binary repository seeds are validated in GitHub CI")
        for filename in ("x-ui.db", "x-ui-ads.db"):
            with self.subTest(filename=filename):
                path = ROOT / filename
                if not path.exists():
                    if os.environ.get("REQUIRE_SPLASH_SEEDS") == "1":
                        self.fail(f"Required repository seed is missing: {filename}")
                    continue
                with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                    connection.execute("PRAGMA query_only = ON")
                    self.assertEqual(connection.execute("PRAGMA quick_check").fetchall(), [("ok",)])
                    row = connection.execute(
                        "SELECT value FROM settings WHERE key = 'xrayTemplateConfig'"
                    ).fetchone()
                    self.assertIsNotNone(row, "Seed must contain an Xray template")
                    config = json.loads(row[0])
                    self.assertIsInstance(config, dict)
                    outbounds = config.get("outbounds", [])
                    routing = config.get("routing", {})
                    self.assertIsInstance(outbounds, list)
                    self.assertIsInstance(routing, dict)
                    rules = routing.get("rules", [])
                    balancers = routing.get("balancers", [])
                    # Counts only: never print database contents, credentials or addresses.
                    print(json.dumps({
                        "seed": filename,
                        "sqlite": "ok",
                        "outbounds": len(outbounds),
                        "managed_th_outbounds": sum(
                            str(item.get("tag", "")).startswith("TH-")
                            for item in outbounds if isinstance(item, dict)
                        ),
                        "admob_balancer": any(
                            item.get("tag") == "ADMOB-BALANCER"
                            for item in balancers if isinstance(item, dict)
                        ),
                        "admob_routing_references": sum(
                            item.get("balancerTag") == "ADMOB-BALANCER"
                            for item in rules if isinstance(item, dict)
                        ),
                    }, sort_keys=True))
                original = path.read_bytes()
                result = subprocess.run(
                    [sys.executable, str(HELPER), "validate-seed", str(path)],
                    text=True, capture_output=True, timeout=10,
                )
                if filename == "x-ui-ads.db":
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0,
                                        "The generic template must not silently acquire ad routing")
                self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
