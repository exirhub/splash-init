#!/usr/bin/env python3
"""Read-only setup checks and atomic local configuration for Splash Init."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import tempfile
from urllib.parse import urlsplit


TH_TAG = re.compile(r"TH-\d+\Z")
ENV_KEY = re.compile(r"[A-Z_][A-Z0-9_]*\Z")


def read_environment(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        key, separator, value = line.partition("=")
        if not separator or not ENV_KEY.fullmatch(key):
            raise ValueError("Environment file must contain simple KEY=value assignments.")
        try:
            # Parse data only. Never source or execute an environment file.
            result[key] = " ".join(shlex.split(value, comments=False, posix=True))
        except ValueError as error:
            raise ValueError("Invalid quoting in the environment file.") from error
    return result


def quoted(value):
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Configuration values cannot contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def configure_environment(env_file, example, xray_binary):
    destination = Path(env_file)
    defaults = read_environment(example)
    defaults["XRAY_BINARY"] = xray_binary
    existing = destination.exists()
    current = read_environment(destination) if existing else {}
    if not existing:
        for key in ("PROXYFLEET_OUTBOUNDS_URL", "PROXYFLEET_OUTBOUNDS_TOKEN"):
            if key in os.environ:
                defaults[key] = os.environ[key]
    values = {**defaults, **current}
    for value in values.values():
        quoted(value)
    parsed = urlsplit(values.get("PROXYFLEET_OUTBOUNDS_URL", ""))
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("ProxyFleet URL must be an HTTP(S) endpoint; use the separate token setting.")
    if not Path(values.get("XUI_DB", "")).is_absolute():
        raise ValueError("XUI_DB must be an absolute database path.")
    text = destination.read_text() if existing else "# Managed by Splash Init. Existing values are preserved on updates.\n"
    missing = [(key, value) for key, value in defaults.items() if key not in current]
    if missing:
        text = text.rstrip("\n") + "\n" + "\n".join(f"{key}={quoted(value)}" for key, value in missing) + "\n"
        atomic_write(destination, text)
    else:
        destination.chmod(0o600)
    print("Sync configuration preserved and missing defaults added." if existing else "Sync configuration created with mode 0600.")


def database_connection(path):
    path = Path(path).resolve(strict=True)
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)


def template_config(path):
    with database_connection(path) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("The x-ui database integrity check failed.")
        row = connection.execute("SELECT value FROM settings WHERE key = ?", ("xrayTemplateConfig",)).fetchone()
    if not row:
        raise ValueError("The database has no xrayTemplateConfig setting.")
    try:
        config = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("The x-ui template is not valid JSON.") from error
    if not isinstance(config, dict) or not isinstance(config.get("outbounds"), list):
        raise ValueError("The x-ui template needs an outbounds array.")
    if not isinstance(config.get("routing"), dict) or not isinstance(config["routing"].get("rules"), list):
        raise ValueError("The x-ui template needs routing.rules.")
    return config


def check_routing(config, tag):
    if not any(isinstance(rule, dict) and rule.get("balancerTag") == tag for rule in config["routing"]["rules"]):
        raise ValueError("The template has no routing rule targeting the configured ProxyFleet balancer. Routing was not changed.")


def managed_pool(config, tag):
    if not isinstance(config, dict) or not isinstance(config.get("outbounds"), list) or not isinstance(config.get("routing"), dict):
        raise ValueError("Configuration needs outbounds and routing objects.")
    tags = [item.get("tag") for item in config.get("outbounds", [])
            if isinstance(item, dict) and TH_TAG.fullmatch(str(item.get("tag", "")))]
    balancers = config.get("routing", {}).get("balancers", [])
    if not isinstance(balancers, list):
        raise ValueError("routing.balancers must be an array.")
    matches = [item for item in balancers if isinstance(item, dict) and item.get("tag") == tag]
    if len(matches) > 1:
        raise ValueError("Duplicate managed balancer tags in the template.")
    selector = matches[0].get("selector", []) if matches else []
    if not isinstance(selector, list) or not all(isinstance(item, str) for item in selector):
        raise ValueError("The managed balancer selector must be an array.")
    return tags, selector


def validate_seed(path, tag):
    config = template_config(path)
    check_routing(config, tag)
    tags, selector = managed_pool(config, tag)
    print(json.dumps({"database": "valid", "managed_outbounds": len(tags), "selector_entries": len(selector), "balancer_routing": True}))


def backup_database(source, backup):
    destination = Path(backup)
    if Path(source).resolve() == destination.resolve():
        raise ValueError("Backup destination must differ from the live database.")
    if destination.exists():
        raise ValueError("Backup destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".xui-backup-", dir=destination.parent)
    os.close(descriptor)
    try:
        with database_connection(source) as incoming, sqlite3.connect(temporary) as outgoing:
            incoming.backup(outgoing)
            if outgoing.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite backup verification failed.")
        os.chmod(temporary, 0o600)
        # Refuse to overwrite an existing backup, including a racing installer.
        os.link(temporary, destination)
        print("Consistent SQLite backup created with mode 0600.")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def report_status(env_file):
    environment = read_environment(env_file)
    tag = environment.get("XUI_BALANCER_TAG", "ADMOB-BALANCER")
    config = template_config(environment.get("XUI_DB", "/etc/x-ui/x-ui.db"))
    check_routing(config, tag)
    tags, selector = managed_pool(config, tag)
    pending = []
    if not tags or len(tags) != len(set(tags)) or len(selector) != len(tags) or set(selector) != set(tags):
        pending.append("managed pool and balancer selector are not ready")
    if environment.get("DRY_RUN", "false").lower() in ("true", "1", "yes", "on"):
        pending.append("dry-run is enabled")
    state_path = Path(environment.get("STATE_FILE", "/var/lib/proxyfleet-xui-sync/state.json"))
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Cannot read the synchronizer state file.") from error
        if not isinstance(state, dict):
            raise ValueError("The synchronizer state must be a JSON object.")
        if state.get("restart_candidate"):
            pending.append("a topology update is waiting for the normal stability/cooldown checks")
    runtime_path = Path(environment.get("XRAY_RUNTIME_CONFIG", "/usr/local/x-ui/bin/config.json"))
    if not runtime_path.exists():
        pending.append("Xray runtime configuration is not available yet")
    else:
        try:
            runtime = json.loads(runtime_path.read_text())
            runtime_tags, runtime_selector = managed_pool(runtime, tag)
            if set(runtime_tags) != set(tags) or len(runtime_selector) != len(selector) or set(runtime_selector) != set(selector):
                pending.append("Xray runtime pool does not match the database yet")
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("Cannot validate the Xray runtime configuration.") from error
    print(json.dumps({"configuration_ready": not pending, "managed_outbounds": len(tags),
                      "selector_entries": len(selector), "pending": pending,
                      "note": "Checks installed configuration only; not feed freshness or end-to-end connectivity."}))
    return 2 if pending else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-seed")
    validate.add_argument("path")
    validate.add_argument("--balancer-tag", default="ADMOB-BALANCER")
    backup = commands.add_parser("backup-db")
    backup.add_argument("source")
    backup.add_argument("destination")
    configure = commands.add_parser("configure-env")
    configure.add_argument("--env-file", required=True)
    configure.add_argument("--example", required=True)
    configure.add_argument("--xray-binary", required=True)
    status = commands.add_parser("status")
    status.add_argument("--env-file", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-seed":
            validate_seed(args.path, args.balancer_tag)
        elif args.command == "backup-db":
            backup_database(args.source, args.destination)
        elif args.command == "configure-env":
            configure_environment(args.env_file, args.example, args.xray_binary)
        elif args.command == "status":
            return report_status(args.env_file)
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        # Avoid printing environment values, credentials or database contents.
        print("ERROR: " + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
