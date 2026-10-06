# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Tests for the Open-Meteo extension node (offline paths only).

The live API is never touched: an unreachable endpoint exercises the
empty-but-valid CSV fallback, which must still carry a provenance sidecar.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find_openmeteo() -> Path | None:
    # Extensions repo keeps nodes at the root; dev nests them.
    for base in (ROOT / "nodes", ROOT / "extensions" / "nodes"):
        candidate = base / "openmeteo" / "openmeteo_query.py"
        if candidate.is_file():
            return candidate
    return None


_OPENMETEO_PATH = _find_openmeteo()


def _load_openmeteo(path: Path):
    spec = importlib.util.spec_from_file_location("openmeteo_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenMeteoProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if _OPENMETEO_PATH is None:
            raise unittest.SkipTest("openmeteo node not present in this checkout")
        cls.openmeteo = _load_openmeteo(_OPENMETEO_PATH)

    def test_offline_fallback_stamps_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp)
            ctx = {
                "session_dir": str(session),
                "api_endpoint": "http://127.0.0.1:9/",
                "latitude": -34.0,
                "longitude": -58.0,
            }
            out = self.openmeteo.barrier_open_meteo_query(ctx)
            csv_path = Path(out["file"])
            self.assertTrue(csv_path.exists())
            sidecar = json.loads(
                csv_path.with_suffix(".meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                sidecar["provenance"]["schema"], "lynceus-provenance"
            )
            self.assertIn("weather_data.csv", sidecar["provenance"]["product"]["name"])


if __name__ == "__main__":
    unittest.main()
