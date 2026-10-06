# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Tests for the forestry Area-Based Approach node (CHM + point metrics).

Covers the 22-metric contract without Qt or a worker pool: pure helpers,
metric-set validation, ports, and end-to-end barrier_aba runs on a synthetic
CHM mosaic (plus crafted point partials).
"""

import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def _find_aba() -> Path | None:
    """area_based.py in either layout (dev checkout or extensions repo)."""
    for base in (ROOT / "extensions" / "nodes", ROOT / "nodes"):
        candidate = base / "forestry" / "area_based.py"
        if candidate.is_file():
            return candidate
    return None


_ABA_PATH = _find_aba()
if _ABA_PATH is None:
    raise unittest.SkipTest("forestry nodes not present in this checkout")

EXPECTED_22 = {
    "h_mean", "h_max", "h_std", "h_cv", "h_kurtosis",
    "canopy_cover", "LAI", "rumple", "L_skewness",
    "p10", "p25", "p50", "p75", "p90", "p95", "p99",
    "density_0_2m", "density_2_10m", "density_above_10m",
    "Shannon_H", "VCI", "FHD",
}


def _load_aba():
    spec = importlib.util.spec_from_file_location(
        "aba_under_test", _ABA_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aba = _load_aba()


def _write_chm(path: Path, array: np.ndarray) -> None:
    import rasterio
    from affine import Affine

    rows, cols = array.shape
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, float(rows))
    with rasterio.open(
        str(path), "w", driver="GTiff", height=rows, width=cols,
        count=1, dtype=array.dtype, crs="EPSG:32621",
        transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(array, 1)


def _ctx(session: Path, chm: Path, metrics: list | None = None) -> dict:
    return {
        "session_dir": str(session),
        "chm_mosaic_path": str(chm),
        "grid_size": 20.0,
        "metrics_list": list(metrics) if metrics else list(aba.DEFAULT_METRICS_LIST),
        "canopy_threshold_m": 2.0,
        "lai_k": 0.5,
        "reliability_k": 2.0,
        "min_points_per_cell": 1,
        "include_geometry": True,
    }


class MetricSetTests(unittest.TestCase):
    def test_default_is_exactly_the_22(self) -> None:
        self.assertEqual(set(aba.DEFAULT_METRICS_LIST), EXPECTED_22)

    def test_available_has_l_skewness_and_percentiles(self) -> None:
        available = set(aba.available_metrics())
        self.assertIn("L_skewness", available)
        for p in ("p10", "p25", "p50", "p75", "p90", "p95", "p99"):
            self.assertIn(p, available)

    def test_available_has_no_dtm(self) -> None:
        for name in aba.available_metrics():
            self.assertFalse(
                name.startswith("dtm_") or name in ("soil_roughness", "twi_mean"),
                name,
            )

    def test_validate_rejects_dtm(self) -> None:
        with self.assertRaises(RuntimeError):
            aba._validate_metric_list(["h_mean", "dtm_elevation"])
        aba._validate_metric_list(list(aba.DEFAULT_METRICS_LIST))  # no raise

    def test_ports_chm_required_point_optional(self) -> None:
        from lynceus.nodes.ports import PortDef, PortType, port_metadata

        defs = [port_metadata(item) for item in aba.INPUTS]
        self.assertEqual(len(defs), 2)
        self.assertEqual(defs[0].port_type, PortType.CHM_MOSAIC)
        self.assertTrue(defs[0].required)
        second = defs[1]
        self.assertIsInstance(aba.INPUTS[1], PortDef)
        self.assertEqual(second.port_type, PortType.POINT_CLOUD)
        self.assertFalse(second.required)


class HelperMathTests(unittest.TestCase):
    def test_h_cv_zero_mean(self) -> None:
        self.assertEqual(aba._calc_h_cv(np.zeros(5)), 0.0)
        self.assertAlmostEqual(
            aba._calc_h_cv(np.array([8.0, 12.0])), 0.2, places=6
        )

    def test_h_kurtosis_edges(self) -> None:
        self.assertEqual(aba._calc_h_kurtosis(np.array([1.0, 2.0, 3.0])), 0.0)
        self.assertEqual(aba._calc_h_kurtosis(np.full(10, 5.0)), 0.0)

    def test_lai_caps(self) -> None:
        vals = np.full(10, 12.0)
        self.assertEqual(aba._calc_lai(vals, 2.0, 0.5), 5.0)  # full cover cap
        self.assertEqual(aba._calc_lai(np.zeros(10), 2.0, 0.5), 0.0)
        half = np.array([1.0] * 5 + [10.0] * 5)
        self.assertAlmostEqual(
            aba._calc_lai(half, 2.0, 0.5), -math.log(0.5) / 0.5, places=6
        )

    def test_rumple_flat(self) -> None:
        gx = np.zeros((4, 4))
        self.assertEqual(aba._calc_rumple(gx, gx, 1.0), 1.0)

    def test_hist_densities_are_fractions(self) -> None:
        h = np.zeros(40, dtype=np.uint32)
        h[0] = 10
        h[1] = 10
        h[2] = 20
        h[5] = 10
        self.assertAlmostEqual(aba._hist_metrics(h, "density_0_2m", 2), 0.4)
        self.assertAlmostEqual(aba._hist_metrics(h, "density_2_10m", 2), 0.6)
        self.assertAlmostEqual(aba._hist_metrics(h, "density_above_10m", 2), 0.0)
        self.assertAlmostEqual(
            aba._hist_metrics(h, "density_0_2m", 2)
            + aba._hist_metrics(h, "density_2_10m", 2)
            + aba._hist_metrics(h, "density_above_10m", 2),
            1.0,
        )

    def test_hist_vci_formula(self) -> None:
        h = np.zeros(40, dtype=np.uint32)
        h[0] = 20
        h[5] = 30
        # canopy (bin>=2): 30/50 -> VCI = 0.4
        self.assertAlmostEqual(aba._hist_metrics(h, "VCI", 2), 0.4)

    def test_hist_empty_is_nodata(self) -> None:
        h = np.zeros(40, dtype=np.uint32)
        for metric in ("density_0_2m", "VCI", "Shannon_H", "FHD"):
            self.assertEqual(
                aba._hist_metrics(h, metric, 2), aba.ABA_NODATA, metric
            )

    def test_hist_shannon_known(self) -> None:
        h = np.zeros(40, dtype=np.uint32)
        h[0] = 40
        h[5] = 60
        # strata [0,2):40, [2,10):60, [10,inf):0 -> props 0.4/0.6
        expected = -(0.4 * math.log(0.4) + 0.6 * math.log(0.6))
        self.assertAlmostEqual(
            aba._hist_metrics(h, "Shannon_H", 2), expected, places=9
        )


class TileTaskTests(unittest.TestCase):
    def _write_laz(self, path: Path, classes, zs) -> None:
        import laspy

        n = len(classes)
        header = laspy.LasHeader(point_format=1, version="1.2")
        header.scales = np.array([0.01, 0.01, 0.01])
        header.offsets = np.zeros(3)
        rng = np.random.default_rng(7)
        record = laspy.ScaleAwarePointRecord.zeros(
            n,
            point_format=header.point_format,
            scales=header.scales,
            offsets=header.offsets,
        )
        record.x = rng.uniform(0, 40, n)
        record.y = rng.uniform(0, 40, n)
        record.z = np.asarray(zs, dtype=float)
        record.classification = np.asarray(classes, dtype=np.uint8)
        with laspy.open(str(path), mode="w", header=header) as writer:
            writer.write_points(record)

    def _tile_ctx(self, root: Path, tile_id="t1"):
        session = root / "session"
        tile = {
            "tile_id": tile_id,
            "file": str(root / "tile.laz"),
            "core_x_min": 0.0, "core_x_max": 40.0,
            "core_y_min": 0.0, "core_y_max": 40.0,
        }
        ctx = {"session_dir": str(session), "grid_size": 20.0}
        return tile, ctx

    def test_classified_cloud_bins_points(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        classes = np.array([2] * 100 + [5] * 100)
        zs = np.array([0.0] * 100 + [10.0] * 100)
        self._write_laz(root / "tile.laz", classes, zs)
        tile, ctx = self._tile_ctx(root)
        out = aba.tile_aba(tile, ctx)
        self.assertNotIn("skipped", out)
        self.assertNotIn("warnings", out)
        with np.load(out["output"]) as data:
            self.assertGreater(len(data["keys"]), 0)
            self.assertEqual(int(data["hist"].sum()), 100)
            self.assertEqual(int(data["ground"].sum()), 100)

    def test_high_terrain_normalizes_by_ground_median(self) -> None:
        """Regression: absolute heights above HIST_BINS used to be discarded.

        Ground ~500 m, canopy 505-520 m, no per-tile DTM: heights normalize
        by the tile ground median so the 1 m strata bins still fill.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        rng = np.random.default_rng(21)
        ground_z = rng.uniform(499, 501, 800)
        veg_z = rng.uniform(505, 520, 1200)
        classes = np.array([2] * 800 + [5] * 1200)
        self._write_laz(
            root / "tile.laz", classes,
            np.concatenate([ground_z, veg_z]),
        )
        tile, ctx = self._tile_ctx(root)
        out = aba.tile_aba(tile, ctx)
        self.assertNotIn("warnings", out)
        with np.load(out["output"]) as data:
            hist = data["hist"]
            # Bins used: the normalized 5..20 m canopy band only — ground
            # (class 2) is excluded from vegetation, nothing out of range.
            used = set(int(b) for b in np.nonzero(hist.sum(axis=0))[0])
            self.assertTrue(used, "histogram is empty")
            self.assertLessEqual(used, set(range(5, 21)), used)
            self.assertEqual(int(hist.sum()), 1200)

    def test_no_ground_falls_back_absolute_with_warning(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self._write_laz(
            root / "tile.laz",
            np.full(200, 5, dtype=np.uint8),
            np.full(200, 10.0),
        )
        tile, ctx = self._tile_ctx(root)
        out = aba.tile_aba(tile, ctx)
        warnings = out.get("warnings") or []
        self.assertTrue(
            any("no ground points" in w for w in warnings), warnings
        )
        with np.load(out["output"]) as data:
            self.assertEqual(int(data["hist"].sum()), 200)

    def test_unclassified_cloud_counts_with_warning(self) -> None:
        """Class 1 has no ground reference: absolute heights + warning."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self._write_laz(
            root / "tile.laz",
            np.ones(200, dtype=np.uint8),
            np.full(200, 8.0),
        )
        tile, ctx = self._tile_ctx(root)
        out = aba.tile_aba(tile, ctx)
        self.assertNotIn("skipped", out)
        with np.load(out["output"]) as data:
            self.assertEqual(int(data["hist"].sum()), 200)
        warnings = out.get("warnings") or []
        self.assertTrue(
            any("no ground points" in w for w in warnings), warnings
        )

    def test_restricted_vegetation_classes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        classes = np.array([2] * 50 + [3] * 50 + [5] * 100)
        zs = np.array([0.0] * 50 + [10.0] * 150)
        self._write_laz(root / "tile.laz", classes, zs)
        tile, ctx = self._tile_ctx(root)
        ctx["vegetation_classes"] = "5"
        out = aba.tile_aba(tile, ctx)
        with np.load(out["output"]) as data:
            # Only class 5 binned as vegetation; class 3 ignored.
            self.assertEqual(int(data["hist"].sum()), 100)
            self.assertEqual(int(data["ground"].sum()), 50)

    def test_parse_vegetation_classes(self) -> None:
        auto = aba._parse_vegetation_classes("")
        self.assertNotIn(2, auto)
        self.assertNotIn(7, auto)
        self.assertNotIn(18, auto)
        for c in (0, 1, 3, 4, 5, 6):
            self.assertIn(c, auto)
        self.assertEqual(aba._parse_vegetation_classes("3,4,5"), {3, 4, 5})
        self.assertEqual(aba._parse_vegetation_classes("5, 2"), {5})
        self.assertEqual(aba._parse_vegetation_classes([3, 4]), {3, 4})
        with self.assertRaises(RuntimeError):
            aba._parse_vegetation_classes("abc")
        with self.assertRaises(RuntimeError):
            aba._parse_vegetation_classes({"weird": "dict"})


class BarrierEndToEndTests(unittest.TestCase):
    def _run(self, chm_array, metrics=None, partials=None) -> dict:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        session = Path(tmp.name)
        chm_path = session / "chm.tif"
        _write_chm(chm_path, chm_array)
        if partials:
            out_dir = session / "aba" / "partials"
            out_dir.mkdir(parents=True, exist_ok=True)
            for name, (keys, hist, ground) in partials.items():
                np.savez_compressed(
                    out_dir / name, keys=keys, hist=hist, ground=ground
                )
        return aba.barrier_aba(_ctx(session, chm_path, metrics))

    def test_flat_chm_without_points(self) -> None:
        out = self._run(np.full((40, 40), 12.0, dtype=np.float32))
        self.assertTrue(Path(out["file"]).exists())
        import geopandas as gpd

        gdf = gpd.read_file(out["file"])
        self.assertEqual(len(gdf), 4)  # 2x2 cells of 20 m
        row = gdf.iloc[0]
        self.assertAlmostEqual(row["h_mean"], 12.0)
        self.assertAlmostEqual(row["h_max"], 12.0)
        self.assertAlmostEqual(row["h_std"], 0.0)
        self.assertAlmostEqual(row["canopy_cover"], 1.0)
        self.assertAlmostEqual(row["LAI"], 5.0)  # full-cover cap
        self.assertAlmostEqual(row["rumple"], 1.0, places=6)
        for p in ("p10", "p50", "p99"):
            self.assertAlmostEqual(row[p], 12.0)
        self.assertAlmostEqual(row["L_skewness"], 0.0)
        for metric in ("density_0_2m", "VCI", "Shannon_H", "FHD"):
            self.assertEqual(row[metric], aba.ABA_NODATA, metric)
        self.assertTrue(
            any("point" in w.lower() for w in out.get("warnings", [])),
            out.get("warnings"),
        )
        for metric in EXPECTED_22:
            self.assertIn(metric, gdf.columns, metric)

    def test_gradient_chm_structural(self) -> None:
        cols = np.tile(np.arange(40, dtype=np.float32), (40, 1))
        out = self._run(cols)
        import geopandas as gpd

        gdf = gpd.read_file(out["file"])
        row = gdf.iloc[0]
        vals = np.arange(20, dtype=np.float64)  # first cell: cols 0..19
        self.assertAlmostEqual(row["h_mean"], vals.mean())
        self.assertAlmostEqual(row["h_max"], 19.0)
        self.assertGreater(row["rumple"], 1.0)
        self.assertAlmostEqual(row["canopy_cover"], 17.0 / 20.0)
        self.assertAlmostEqual(
            row["LAI"], -math.log(1.0 - 17.0 / 20.0) / 0.5, places=6
        )
        p10, p50, p90 = (
            np.percentile(vals, 10),
            np.percentile(vals, 50),
            np.percentile(vals, 90),
        )
        self.assertAlmostEqual(row["p10"], p10)
        self.assertAlmostEqual(
            row["L_skewness"], (p90 - 2 * p50 + p10) / (p90 - p10), places=6
        )

    def test_point_partials_metrics(self) -> None:
        gs = 20.0
        key = int(aba._cell_key(np.array([5.0]), np.array([35.0]), gs)[0])
        hist = np.zeros((1, 40), dtype=np.uint32)
        hist[0, 0] = 10
        hist[0, 1] = 10
        hist[0, 2] = 20
        hist[0, 5] = 10
        ground = np.array([100], dtype=np.uint32)
        out = self._run(
            np.full((40, 40), 12.0, dtype=np.float32),
            metrics=[
                "density_0_2m", "density_2_10m", "density_above_10m",
                "Shannon_H", "VCI", "FHD",
                "ground_density", "reliability_score", "h_mean",
            ],
            partials={
                "cell.npz": (
                    np.array([key], dtype=np.int64), hist, ground
                )
            },
        )
        import geopandas as gpd

        gdf = gpd.read_file(out["file"])
        self.assertEqual(len(gdf), 4)
        with_points = gdf[gdf["n_pointcloud_samples"] > 0]
        self.assertEqual(len(with_points), 1)
        row = with_points.iloc[0]
        self.assertAlmostEqual(row["density_0_2m"], 0.4)
        self.assertAlmostEqual(row["density_2_10m"], 0.6)
        self.assertAlmostEqual(row["density_above_10m"], 0.0)
        self.assertAlmostEqual(row["VCI"], 0.4)
        self.assertAlmostEqual(row["ground_density"], 100.0 / 400.0)
        self.assertAlmostEqual(
            row["reliability_score"], 1.0 - math.exp(-2.0 * 0.25), places=6
        )
        others = gdf[gdf["n_pointcloud_samples"] == 0].iloc[0]
        self.assertEqual(others["VCI"], aba.ABA_NODATA)

    def test_ground_only_partial_warns_no_veg(self) -> None:
        gs = 20.0
        key = int(aba._cell_key(np.array([5.0]), np.array([35.0]), gs)[0])
        hist = np.zeros((1, 40), dtype=np.uint32)  # no vegetation points
        ground = np.array([50], dtype=np.uint32)
        out = self._run(
            np.full((40, 40), 12.0, dtype=np.float32),
            metrics=["density_0_2m", "VCI", "ground_density", "h_mean"],
            partials={"cell.npz": (np.array([key]), hist, ground)},
        )
        warnings = out.get("warnings") or []
        self.assertTrue(
            any("no vegetation points" in w for w in warnings), warnings
        )
        import geopandas as gpd

        gdf = gpd.read_file(out["file"])
        row = gdf[gdf["n_pointcloud_samples"] > 0].iloc[0]
        self.assertEqual(row["VCI"], aba.ABA_NODATA)
        self.assertEqual(row["density_0_2m"], aba.ABA_NODATA)
        self.assertAlmostEqual(row["ground_density"], 50.0 / 400.0)

    def test_subset_metrics_list(self) -> None:
        out = self._run(
            np.full((40, 40), 12.0, dtype=np.float32),
            metrics=["h_mean", "VCI"],
        )
        import geopandas as gpd

        gdf = gpd.read_file(out["file"])
        self.assertIn("h_mean", gdf.columns)
        self.assertIn("VCI", gdf.columns)
        self.assertNotIn("h_max", gdf.columns)


class ForestryDisclaimerTests(unittest.TestCase):
    """Every forestry node ships GPL-3.0 with the experimental/AI gate.

    Reads the three standalone files via AST (never executes them): a
    future node without disclaimer or with a different license fails here.
    """

    NODES = ("area_based", "canopy_gaps", "height_strata")

    def _meta(self, stem: str) -> dict:
        import ast

        path = _ABA_PATH.parent / f"{stem}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        meta: dict = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and isinstance(
                    node.value, ast.Constant
                ):
                    meta[target.id] = node.value.value
        return meta

    def test_gpl_with_experimental_gate(self) -> None:
        for stem in self.NODES:
            with self.subTest(node=stem):
                meta = self._meta(stem)
                self.assertEqual(
                    meta.get("NODE_LICENSE"), "GPL-3.0-or-later"
                )
                eula = meta.get("NODE_EULA") or ""
                self.assertIn("EXPERIMENTAL", eula)
                self.assertIn("AI assistance", eula)
                self.assertIn("qualified professional", eula)
                self.assertIn("solely responsible", eula)
                disclaimer = meta.get("NODE_DISCLAIMER") or ""
                self.assertIn("AI-assisted", disclaimer)
                self.assertIn("qualified professional", disclaimer)
                self.assertIn("sole responsibility", disclaimer)


if __name__ == "__main__":
    unittest.main()
