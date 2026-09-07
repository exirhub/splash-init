"""Safe installer and repository checks; never install into the host system."""

import json
import os
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SeedDatabaseTests(unittest.TestCase):
    def test_repository_database_integrity(self):
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


if __name__ == "__main__":
    unittest.main()
