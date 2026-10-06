# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Node - Area-Based Approach (ABA) forest metrics on a regular grid.

Tile task: reads the tile's classified LAZ, normalizes heights by the tile
ground median and aggregates vegetation/ground points into GLOBAL grid cells
(world-coordinate formula, consistent across tiles). Writes a compact
partial (.npz) per tile - embarrassingly parallel.

Barrier task: merges partials, computes CHM raster metrics (vectorized) and
point-derived diversity/density metrics from the merged histograms. Writes
one GeoPackage of grid cells plus a metadata JSON.

Optional inputs (resolved from real canvas edges):
- Classify Ground upstream -> point-based metrics enabled
Without it only the CHM metric set runs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from lynceus.nodes._point_source import point_src
from lynceus.nodes.ports import PortDef, PortType

NODE_ID = "forestry.area_based"
NODE_NAME = "Area-Based Approach"
NODE_CATEGORY = "Raster"
NODE_SUBCATEGORY = "Analysis"
NODE_AUTHOR = "Taritolay, Nicolás Daniel"
NODE_FOLDER = "forestry"
NODE_LICENSE = "GPL-3.0-or-later"
NODE_REPOSITORY = "https://github.com/Danico19827/LynceusScan-Extensions"
NODE_HOMEPAGE = "https://danico19827.github.io/LynceusScan-Web/extensions/"
NODE_EULA = (
    "This is an EXPERIMENTAL forestry node developed with AI assistance. "
    "Its algorithms and precision are UNVERIFIED. Results must be evaluated "
    "by a qualified professional before any operational, commercial or "
    "safety-related use. This software is provided as-is, WITHOUT WARRANTY "
    "OF ANY KIND (GPL-3.0-or-later). By using this node you accept that YOU "
    "are solely responsible for any use, sharing or consequence of its "
    "products."
)
NODE_DISCLAIMER = (
    "Experimental AI-assisted node, precision unverified: validate with a "
    "qualified professional before operational use. Any use or sharing of "
    "these products is the sole responsibility of whoever uses or shares "
    "them."
)

NODE_DESCRIPTION = (
    "<b>Area-Based Approach</b> -- Forest Structure Metrics on a Regular Grid<br><br>"
    "Computes vegetation metrics per grid cell by combining "
    "CHM raster data with classified point cloud histograms.<br><br>"
    "<b>Process:</b> Each tile bins its classified points into global grid cells "
    "using world-coordinate formulas (no global bounds needed). The barrier "
    "merges all tile partials and computes selected metrics from CHM pixels "
    "and point histograms. Results are "
    "written as a GeoPackage with one feature per grid cell.<br><br>"
    "<b>Tips:</b> By default the 22 CHM + point metrics are computed (percentiles, "
    "classic stats, structural, eco-physiological and stratum densities); "
    "each input feeds its own metric families (CHM, point cloud). Missing point input "
    "yields NODATA (-9999.0) on point-based metrics. "
    "Add <i>canopy_threshold_m</i> "
    "to control which height counts as 'canopy' for cover, LAI and VCI. "
    "Connect <b>Classify Ground</b> upstream for point-based density metrics."
)

# Input ports: CHM mosaic is required; the point cloud is optional and feeds
# the point-based density/diversity metrics (densities, Shannon_H, VCI, FHD)
# plus the ground quality flags. Without it only the CHM metric set runs.
INPUTS = (
    PortType.CHM_MOSAIC,
    PortDef(PortType.POINT_CLOUD, required=False),
)
OUTPUTS = (PortType.GRID_METRICS, PortType.VECTOR)

# NODATA value for metrics whose optional input is disconnected or unavailable.
ABA_NODATA = -9999.0

# Tile warnings are tile-agnostic strings so the controller deduplicates them
# into a single status-bar message (node warnings stay in English by design).
ABA_WARN_NO_CLASS_DIM = (
    "Tiles lack the classification dimension; point-based metrics need a "
    "classified point cloud (Classify Ground) and stay NODATA otherwise."
)
ABA_WARN_NO_POINTS = (
    "Tiles hold no vegetation or ground points (unclassified cloud?); "
    "point-based metrics stay NODATA."
)
ABA_WARN_NO_GROUND = (
    "Some tiles hold no ground points; vegetation heights cannot be "
    "normalized and point-based metrics may degrade."
)

# Default metric set: the 22 CHM + point metrics (percentiles, classic
# stats, structural, eco-physiological, stratum densities). Ground quality
# flags stay available on request via metrics_list.
DEFAULT_METRICS_LIST = [
    "h_mean", "h_max", "h_std", "h_cv", "h_kurtosis",
    "canopy_cover", "LAI", "rumple", "L_skewness",
    "p10", "p25", "p50", "p75", "p90", "p95", "p99",
    "density_0_2m", "density_2_10m", "density_above_10m",
    "Shannon_H", "VCI", "FHD",
]

PROCESSING_SPECS = {
    "tile_task": "tile_aba",
    "barrier_task": "barrier_aba",
    "input_ports": ("chm_mosaic", "point_cloud"),
    "output_ports": ("grid_metrics", "vector"),
    "requires": {"classified": True},
    "output_globs": (
        "aba/partials/*.npz",
        "area_based_metrics.gpkg",
        "area_based_metrics.csv",
        "area_based_metrics.meta.json",
        "area_based_summary.csv",
        "area_based_metrics.fgb",
        "area_based_metrics.parquet",
        "aba/**/*.npz",
    ),
    "qml": {
        "area_based_metrics.gpkg": {
            "field": "h_mean",
            "name": "Mean Height",
            "classes": [
                (0.0, 4.0, "#d7191c", "0 \u2013 4 m"),
                (4.0, 8.0, "#fdae61", "4 \u2013 8 m"),
                (8.0, 12.0, "#ffffbf", "8 \u2013 12 m"),
                (12.0, 16.0, "#a6d96a", "12 \u2013 16 m"),
                (16.0, 999.0, "#1a9641", "16+ m"),
            ],
        },
    },
    "config_schema": {
        "grid_size": {
            "type": "float",
            "default": 20.0,
            "minimum": 1.0,
            "maximum": 500.0,
            "description": "Grid cell size in meters.",
            "impact": "Smaller cells give finer spatial resolution but increase output size and processing time. Larger cells aggregate more points per cell, improving statistical robustness at the cost of spatial detail.",
            "group": "Grid",
        },
        "metrics_list": {
            "type": "list",
            "default": list(DEFAULT_METRICS_LIST),
            "description": "Metrics to compute per grid cell.",
            "impact": "By default the 22 CHM + point metrics are computed (percentiles p10-p99, classic stats, structural, eco-physiological, stratum densities). Restrict to a subset when needed; point-based metrics without an upstream point cloud are filled with NODATA (-9999.0). Ground quality flags (ground_density, reliability_score) stay available on request.",
            "group": "Metrics",
        },
        "canopy_threshold_m": {
            "type": "float",
            "default": 2.0,
            "minimum": 0.0,
            "maximum": 50.0,
            "description": "Minimum height (meters) to consider a point as canopy.",
            "impact": "Used by canopy_cover and LAI calculations. Lower values include more of the understory as 'canopy', increasing cover estimates. Higher values restrict to dominant canopy only.",
            "group": "Vegetation",
        },
        "lai_k": {
            "type": "float",
            "default": 0.5,
            "minimum": 0.1,
            "maximum": 2.0,
            "description": "Beer-Lambert extinction coefficient (k) for LAI estimation.",
            "impact": "Controls the relationship between canopy cover and LAI. Lower k values produce higher LAI estimates for the same cover. Typical range for forests: 0.4-0.7.",
            "group": "Vegetation",
        },
        "vegetation_classes": {
            "type": "str",
            "default": "",
            "description": "LAS classes counted as vegetation (comma-separated).",
            "impact": "Empty (default) counts every class except ground (2) and noise (7, 18). List specific classes (e.g. 3,4,5) to restrict vegetation to them; class 2 is always excluded and counted as ground instead.",
            "group": "Vegetation",
        },
        "reliability_k": {
            "type": "float",
            "default": 2.0,
            "minimum": 0.1,
            "maximum": 10.0,
            "description": "Steepness for the ground-density reliability sigmoid.",
            "impact": "Higher values create a sharper transition: cells need more ground points to achieve high reliability. Lower values are more lenient.",
            "group": "Vegetation",
        },
        "min_points_per_cell": {
            "type": "int",
            "default": 1,
            "minimum": 1,
            "maximum": 1000,
            "description": "Minimum point cloud samples per cell to produce metrics.",
            "impact": "Cells with fewer points are flagged as low-confidence in has_pointcloud_data. Increase to filter out statistically unreliable cells from final outputs.",
            "group": "Quality",
        },
        "include_geometry": {
            "type": "bool",
            "default": True,
            "description": "Include square polygon geometry in the output GeoPackage.",
            "impact": "Disable to output attribute-only tables (smaller files). Useful when integrating with tabular workflows that don't need spatial features.",
            "group": "Output",
        },
    },
}

# Per-cell height histogram: 1 m bins up to HIST_BINS meters.
HIST_BINS = 40


def _auto_vegetation_classes() -> set[int]:
    """Default vegetation set: every LAS class except ground and noise.

    Derived from the canonical constants (GROUND_CLASS, NOISE_LOW_CLASS,
    NOISE_HIGH_CLASS) so codebase-wide changes propagate automatically.
    """
    from lynceus.nodes._point_source import NOISE_HIGH_CLASS, NOISE_LOW_CLASS
    from lynceus.processing.raster import GROUND_CLASS

    return {
        c
        for c in range(256)
        if c not in (GROUND_CLASS, NOISE_LOW_CLASS, NOISE_HIGH_CLASS)
    }


def _parse_vegetation_classes(value) -> set[int]:
    """Vegetation class allowlist from the vegetation_classes config.

    Empty/missing means automatic: every LAS class except ground (2) and
    noise (7, 18). An explicit comma-separated list (e.g. "3,4,5")
    restricts vegetation to those classes; ground (2) is always excluded
    and counted separately.
    """
    from lynceus.processing.raster import GROUND_CLASS

    if value is None or (isinstance(value, str) and not value.strip()):
        return _auto_vegetation_classes()
    if isinstance(value, int):
        tokens = [value]
    elif isinstance(value, str):
        tokens = [tok.strip() for tok in value.split(",")]
    elif isinstance(value, (list, tuple)):
        tokens = list(value)
    else:
        raise RuntimeError(
            f"Invalid vegetation_classes {value!r}: use comma-separated "
            "LAS class numbers (e.g. 3,4,5) or leave empty for automatic."
        )
    try:
        classes = {
            int(tok) for tok in tokens if str(tok).strip() != ""
        }
    except (TypeError, ValueError):
        raise RuntimeError(
            f"Invalid vegetation_classes {value!r}: use comma-separated "
            "LAS class numbers (e.g. 3,4,5) or leave empty for automatic."
        )
    classes.discard(GROUND_CLASS)
    return classes

# Cell-key packing into int64 (world coordinates -> index).
_KEY_OFFSET = 1 << 20
_KEY_SPAN = 1 << 21


def get_config_defaults() -> dict:
    return {
        k: v["default"] for k, v in PROCESSING_SPECS["config_schema"].items()
    }


# ---------------------------------------------------------------------------
# Global grid: pure world-coordinate formula (no global bounds).
# ---------------------------------------------------------------------------

def _cell_key(x: "np.ndarray", y: "np.ndarray", gs: float) -> "np.ndarray":
    """Stable int64 global cell key for coordinate arrays."""
    import numpy as np

    col = np.floor(x / gs).astype(np.int64) + _KEY_OFFSET
    row = np.floor(y / gs).astype(np.int64) + _KEY_OFFSET
    return row * _KEY_SPAN + col


def _key_to_cell(key: int, gs: float) -> tuple[float, float, float, float]:
    """Bounding box (x_min, y_min, x_max, y_max) of a cell key."""
    col = key % _KEY_SPAN - _KEY_OFFSET
    row = key // _KEY_SPAN - _KEY_OFFSET
    return (
        col * gs,
        row * gs,
        (col + 1) * gs,
        (row + 1) * gs,
    )


# ---------------------------------------------------------------------------
# Tile task: parallel point binning into the global cell grid.
# ---------------------------------------------------------------------------

def _write_empty_partial(out_dir: Path, out_path: Path) -> None:
    """Writes an empty partial: this tile contributes no point data."""
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path, keys=np.zeros(0, dtype=np.int64),
        hist=np.zeros((0, HIST_BINS), dtype=np.uint32),
        ground=np.zeros(0, dtype=np.uint32),
    )


def tile_aba(tile: dict, ctx: dict) -> dict:
    """Read one LAZ tile and produce per-cell partial aggregates.

    The source is the LAZ classified by an upstream node if it exists;
    otherwise it falls to the raw LAZ and AUTO-DETECTS class 2 (already
    classified clouds).
    Heights are normalized by the tile ground median (self-contained and
    deterministic: no per-tile DTM exists without ordering). Without ground
    points the absolute heights are kept with a warning.
    If the file has no class 2 this tile contributes no partials (skipped).
    """
    import numpy as np

    session = Path(ctx["session_dir"])
    out_dir = session / "aba" / "partials"
    out_path = out_dir / f"{tile['tile_id']}.npz"
    if out_path.exists():
        return {"tile_id": tile["tile_id"], "node": NODE_ID, "output": str(out_path)}

    gs = float(ctx.get("grid_size", 20.0))
    src = point_src(tile, ctx)  # cleaned/classified cloud or raw; class-2 auto-detection
    veg_list = sorted(_parse_vegetation_classes(ctx.get("vegetation_classes", "")))

    import laspy

    from lynceus.processing.raster import GROUND_CLASS, core_mask
    from lynceus.processing.tiler import _laz_backend

    x_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    z_parts: list[np.ndarray] = []
    gx_parts: list[np.ndarray] = []
    gy_parts: list[np.ndarray] = []
    gz_parts: list[np.ndarray] = []

    with laspy.open(src, laz_backend=_laz_backend()) as reader:
        if "classification" not in reader.header.point_format.dimension_names:
            # No classification dimension: ground cannot be separated from
            # vegetation; only CHM-based metrics run downstream.
            _write_empty_partial(out_dir, out_path)
            return {"tile_id": tile["tile_id"], "node": NODE_ID,
                    "skipped": True, "warnings": [ABA_WARN_NO_CLASS_DIM],
                    "output": str(out_path)}

        for chunk in reader.chunk_iterator(2_000_000):
            # float32: half the memory/bandwidth; for cell keys the sub-cm
            # jitter only affects points glued to a cell border.
            x = np.asarray(chunk.x, dtype=np.float32)
            y = np.asarray(chunk.y, dtype=np.float32)
            z = np.asarray(chunk.z, dtype=np.float32)
            cls = np.asarray(chunk.classification, dtype=np.uint8)
            keep = core_mask(x, y, tile)
            x, y, z, cls = x[keep], y[keep], z[keep], cls[keep]

            grd = cls == GROUND_CLASS
            cand = np.isin(cls, veg_list)
            gx_parts.append(x[grd])
            gy_parts.append(y[grd])
            gz_parts.append(z[grd])
            x_parts.append(x[cand])
            y_parts.append(y[cand])
            z_parts.append(z[cand])

    vx = np.concatenate(x_parts) if x_parts else np.zeros(0, dtype=np.float32)
    vy = np.concatenate(y_parts) if y_parts else np.zeros(0, dtype=np.float32)
    vz = np.concatenate(z_parts) if z_parts else np.zeros(0, dtype=np.float32)
    gx = np.concatenate(gx_parts) if gx_parts else np.zeros(0, dtype=np.float32)
    gy = np.concatenate(gy_parts) if gy_parts else np.zeros(0, dtype=np.float32)
    gz = np.concatenate(gz_parts) if gz_parts else np.zeros(0, dtype=np.float32)

    if len(vx) == 0 and len(gx) == 0:
        # No ground or vegetation points (e.g. unclassified cloud): empty
        # partial (never fails) with a deduplicated warning downstream.
        _write_empty_partial(out_dir, out_path)
        return {"tile_id": tile["tile_id"], "node": NODE_ID,
                "skipped": True, "warnings": [ABA_WARN_NO_POINTS],
                "output": str(out_path)}

    tile_warnings: list[str] = []
    if len(gz):
        z_ref = float(np.median(gz))
        vz = vz - z_ref
    else:
        # No ground reference in this tile: keep absolute heights (legacy
        # behavior) and warn; out-of-range values still fall out downstream.
        tile_warnings.append(ABA_WARN_NO_GROUND)
    veg = vz > 0.0
    vx, vy, vz = vx[veg], vy[veg], vz[veg]

    out_dir.mkdir(parents=True, exist_ok=True)

    # Vegetation: 1 m histogram per global cell — vectorized through a
    # composite (cell, bin) key: no Python loop per cell (orders of
    # magnitude faster with thousands of cells).
    veg_keys = _cell_key(vx, vy, gs)
    order = np.argsort(veg_keys)  # order between equal keys is irrelevant
    sorted_keys = veg_keys[order]
    sorted_z = vz[order]
    u_keys, _ = np.unique(sorted_keys, return_index=True)

    # Same semantics as np.histogram(range=(0, HIST_BINS)): out-of-range
    # values are discarded and the upper edge is inclusive (falls in the
    # last bin).
    in_range = (sorted_z >= 0.0) & (sorted_z <= float(HIST_BINS))
    bins = np.floor(sorted_z[in_range]).astype(np.int64)
    np.clip(bins, 0, HIST_BINS - 1, out=bins)
    pair = sorted_keys[in_range] * HIST_BINS + bins
    uniq_pairs, counts = np.unique(pair, return_counts=True)

    hist = np.zeros((len(u_keys), HIST_BINS), dtype=np.uint32)
    hist[
        np.searchsorted(u_keys, uniq_pairs // HIST_BINS),
        uniq_pairs % HIST_BINS,
    ] = counts.astype(np.uint32)

    # Ground: count per global cell.
    g_keys = _cell_key(gx, gy, gs)
    g_uniq, g_counts = np.unique(g_keys, return_counts=True)

    # Union of vegetation+ground keys, aligning both vectors.
    all_keys = np.union1d(u_keys, g_uniq)
    ground_aligned = np.zeros(len(all_keys), dtype=np.uint32)
    pos_g = np.searchsorted(all_keys, g_uniq)
    ground_aligned[pos_g] = g_counts.astype(np.uint32)
    pos_v = np.searchsorted(all_keys, u_keys)
    hist_aligned = np.zeros((len(all_keys), HIST_BINS), dtype=np.uint32)
    hist_aligned[pos_v] = hist

    np.savez_compressed(
        out_path, keys=all_keys.astype(np.int64),
        hist=hist_aligned, ground=ground_aligned,
    )
    result = {
        "tile_id": tile["tile_id"],
        "node": NODE_ID,
        "cells": int(len(all_keys)),
        "output": str(out_path),
    }
    if tile_warnings:
        result["warnings"] = tile_warnings
    return result


# ---------------------------------------------------------------------------
# Metric catalog.
# ---------------------------------------------------------------------------

CHM_SCALAR_METRICS = {
    "h_mean", "h_max", "h_std", "h_cv", "h_kurtosis",
    "canopy_cover", "LAI", "rumple", "L_skewness",
}
POINT_METRICS = {
    "density_0_2m", "density_2_10m", "density_above_10m",
    "Shannon_H", "VCI", "FHD",
}
GROUND_METRICS = {"ground_density", "reliability_score"}
PERCENTILE_METRIC_PREFIX = "p"


def available_metrics() -> list[str]:
    names = set(CHM_SCALAR_METRICS) | POINT_METRICS | GROUND_METRICS
    names.update(f"p{p}" for p in range(1, 100))
    return sorted(names)


def _validate_metric_list(metrics_list: list) -> None:
    known = set(available_metrics())
    unknown = [m for m in metrics_list if m not in known]
    if unknown:
        raise RuntimeError(
            f"Unknown ABA metric(s): {', '.join(map(str, unknown))}. "
            f"Available: {', '.join(available_metrics())}"
        )


def _calc_h_cv(vals):
    import numpy as np

    mean_val = float(np.mean(vals))
    return float(np.std(vals) / mean_val) if mean_val > 0 else 0.0


def _calc_h_kurtosis(vals):
    import numpy as np

    n = len(vals)
    if n < 4:
        return 0.0
    mean = float(np.mean(vals))
    std = float(np.std(vals))
    if std == 0:
        return 0.0
    return float(np.mean((vals - mean) ** 4) / std ** 4)


def _calc_canopy_cover(vals, threshold_m: float) -> float:
    import numpy as np

    return float(np.sum(vals > threshold_m) / len(vals))


def _calc_lai(vals, threshold_m: float, k: float) -> float:
    import numpy as np

    ccf = float(np.sum(vals > threshold_m) / len(vals))
    if ccf >= 1.0:
        return 5.0
    return float(-np.log(1.0 - ccf) / k) if ccf > 0 else 0.0


def _calc_rumple(gx_cell, gy_cell, pixel_size: float) -> float:
    import numpy as np

    if gx_cell.size <= 1:
        return 1.0
    area_surface = float(
        np.sum(np.sqrt(1.0 + gx_cell ** 2 + gy_cell ** 2)) * pixel_size ** 2
    )
    area_flat = gx_cell.size * pixel_size ** 2
    return float(area_surface / area_flat) if area_flat > 0 else 1.0


def _hist_metrics(hist_row: "np.ndarray", metric: str,
                   canopy_bin: int) -> float:
    """Point-histogram metrics per cell as vegetation fractions.

    Stratum densities are fractions of the cell's vegetation points;
    VCI follows VCI = 1 - canopy/total with the canopy threshold bin.
    Cells without vegetation points yield NODATA.
    """
    import numpy as np

    h = hist_row.astype(np.float64)
    n_veg = float(h.sum())
    if n_veg <= 0:
        return float(ABA_NODATA)
    if metric == "density_0_2m":
        return float(h[0:2].sum() / n_veg)
    if metric == "density_2_10m":
        return float(h[2:10].sum() / n_veg)
    if metric == "density_above_10m":
        return float(h[10:].sum() / n_veg)
    if metric == "Shannon_H":
        # Fixed strata [0,2), [2,10), [10,inf): comparable between plots.
        props = np.array([h[0:2].sum(), h[2:10].sum(), h[10:].sum()])
        props = props[props > 0]
        props = props / props.sum()
        return float(-np.sum(props * np.log(props)))
    if metric == "VCI":
        canopy = float(h[canopy_bin:].sum())
        return float(1.0 - canopy / n_veg)
    # FHD: Shannon entropy over 1 m bins.
    nz = int(np.nonzero(h)[0].max()) + 1
    h = h[:nz]
    total = h.sum()
    if total <= 0 or nz <= 1:
        return float(ABA_NODATA)
    props = h[h > 0] / total
    return float(-np.sum(props * np.log(props)))


# ---------------------------------------------------------------------------
# Barrier task: merge partials, compute raster metrics, and write GeoPackage.
# ---------------------------------------------------------------------------

def barrier_aba(ctx: dict) -> dict:
    """Merge tile partials and compute area-based metrics per cell."""
    import csv
    import datetime

    import numpy as np

    session = Path(ctx["session_dir"])
    chm_path = Path(ctx.get("chm_mosaic_path", str(session / "chm_mosaic.tif")))
    if not chm_path.exists():
        raise RuntimeError("Area-Based Approach requires a CHM mosaic upstream")

    gs = float(ctx.get("grid_size", 20.0))
    metrics_list = list(
        ctx.get("metrics_list") or get_config_defaults()["metrics_list"]
    )
    _validate_metric_list(metrics_list)
    canopy_threshold = float(ctx.get("canopy_threshold_m", 2.0))
    canopy_bin = max(0, int(np.floor(canopy_threshold)))
    lai_k = float(ctx.get("lai_k", 0.5))
    reliability_k = float(ctx.get("reliability_k", 2.0))
    min_points_per_cell = int(ctx.get("min_points_per_cell", 1))
    include_geometry = bool(ctx.get("include_geometry", True))

    percentiles_map = {
        m: int(m[1:]) for m in metrics_list
        if m.startswith(PERCENTILE_METRIC_PREFIX) and m[1:].isdigit()
    }
    needs_L_skewness = "L_skewness" in metrics_list
    needs_rumple = "rumple" in metrics_list
    point_requested = [m for m in metrics_list if m in POINT_METRICS]
    ground_requested = [m for m in metrics_list if m in GROUND_METRICS]

    # --- 1. Point partials (tile task) ---------------------------------
    keys_parts: list[np.ndarray] = []
    hist_parts: list[np.ndarray] = []
    ground_parts: list[np.ndarray] = []
    partials_dir = session / "aba" / "partials"
    for f in sorted(partials_dir.glob("*.npz")) if partials_dir.exists() else []:
        data = np.load(f)
        if len(data["keys"]):
            keys_parts.append(data["keys"])
            hist_parts.append(data["hist"])
            ground_parts.append(data["ground"])

    point_uniq = np.zeros(0, dtype=np.int64)
    hist_merged = None
    ground_merged = None
    if keys_parts:
        all_keys = np.concatenate(keys_parts)
        uniq, inverse = np.unique(all_keys, return_inverse=True)
        hist_cat = np.concatenate(hist_parts).astype(np.uint64)
        ground_cat = np.concatenate(ground_parts).astype(np.uint64)
        hist_merged = np.zeros((len(uniq), HIST_BINS), dtype=np.uint64)
        np.add.at(hist_merged, inverse, hist_cat)
        ground_merged = np.zeros(len(uniq), dtype=np.uint64)
        np.add.at(ground_merged, inverse, ground_cat)
        point_uniq = uniq
    point_data_available = (
        hist_merged is not None
        and int(hist_merged.sum() + (ground_merged.sum() if ground_merged is not None else 0)) > 0
    )
    warnings: list[str] = []
    if (point_requested or ground_requested) and not point_data_available:
        # Without a classified cloud (upstream or auto-detected class 2) the
        # point and ground metrics stay NODATA and the rest runs on CHM.
        warnings.append(
            "No classified point cloud found; point-based and ground "
            "metrics set to -9999.0 (NODATA). Running with CHM-only metrics."
        )
    elif point_requested and (
        hist_merged is None or int(hist_merged.sum()) == 0
    ):
        # Partials exist (e.g. ground-only cells) but carry no vegetation
        # points: densities and diversity metrics stay NODATA silently
        # otherwise, so warn explicitly.
        warnings.append(
            "Point data holds no vegetation points; density and diversity "
            "metrics set to -9999.0 (NODATA)."
        )

    # --- 2. CHM: valid pixels grouped per cell -------------------------
    import rasterio

    with rasterio.open(str(chm_path)) as src:
        chm = src.read(1)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
    pixel_size = abs(transform.a)

    valid = np.isfinite(chm)
    if nodata is not None:
        valid &= chm != nodata
    valid &= chm >= 0.0
    rr, cc = np.nonzero(valid)
    chm_vals = chm[rr, cc].astype(np.float64)

    col_keys = np.floor(
        (transform.c + (np.arange(chm.shape[1]) + 0.5) * transform.a) / gs
    ).astype(np.int64) + _KEY_OFFSET
    row_keys = np.floor(
        (transform.f + (np.arange(chm.shape[0]) + 0.5) * transform.e) / gs
    ).astype(np.int64) + _KEY_OFFSET
    pix_keys = row_keys[rr] * _KEY_SPAN + col_keys[cc]

    sort_order = np.argsort(pix_keys, kind="stable")
    pix_keys = pix_keys[sort_order]
    chm_vals = chm_vals[sort_order]
    chm_uniq, chm_starts = np.unique(pix_keys, return_index=True)
    chm_ends = np.append(chm_starts[1:], len(pix_keys))

    cells = np.union1d(chm_uniq, point_uniq)
    n_cells = len(cells)

    # Position of each source within the universe of cells.
    chm_pos = np.searchsorted(cells, chm_uniq)
    chm_slice_by_cell: dict[int, tuple[int, int]] = {}
    for i, (s, e) in enumerate(zip(chm_starts, chm_ends)):
        chm_slice_by_cell[int(chm_pos[i])] = (int(s), int(e))
    pt_pos = (
        np.searchsorted(cells, point_uniq)
        if hist_merged is not None else np.zeros(0, dtype=np.int64)
    )

    # --- 3. Precomputations -------------------------------------------------
    rumple_gx = rumple_gy = None
    if needs_rumple:
        med = float(np.nanmedian(np.where(valid, chm, np.nan))) if valid.any() else 0.0
        filled = np.where(valid, chm, med).astype(np.float64)
        rumple_gy, rumple_gx = np.gradient(filled, pixel_size)

    # Pixel indices per cell for rumple windows (from sorted rr/cc).
    # For windows we use cell bounds -> pixel indices via the transform.
    inv_a = 1.0 / transform.a
    inv_e = 1.0 / transform.e

    def pixel_window(x_min: float, y_min: float, x_max: float, y_max: float):
        j0 = max(0, int(np.floor((x_min - transform.c) * inv_a)))
        j1 = min(chm.shape[1], int(np.ceil((x_max - transform.c) * inv_a)))
        i1 = max(0, int(np.floor((y_min - transform.f) * inv_e)))
        i0 = min(chm.shape[0], int(np.ceil((y_max - transform.f) * inv_e)))
        return max(i0, 0), max(i1, i0), max(j0, 0), max(j1, j0)

    # --- 4. Per-cell metrics ------------------------------------------------
    geometries = []
    records: list[dict] = []

    for idx in range(n_cells):
        key = int(cells[idx])
        x_min, y_min, x_max, y_max = _key_to_cell(key, gs)
        col_i = key % _KEY_SPAN - _KEY_OFFSET
        row_i = key // _KEY_SPAN - _KEY_OFFSET

        rec: dict = {
            "cell_col": col_i,
            "cell_row": row_i,
            "x_min": round(x_min, 3),
            "y_min": round(y_min, 3),
            "x_max": round(x_max, 3),
            "y_max": round(y_max, 3),
            "cell_area_m2": gs * gs,
        }

        sl = chm_slice_by_cell.get(idx)
        vals = chm_vals[sl[0]:sl[1]] if sl else np.zeros(0)
        rec["n_valid_pixels"] = int(len(vals))
        i0, i1, j0, j1 = pixel_window(x_min, y_min, x_max, y_max)

        h_idx = None
        if hist_merged is not None:
            hits = np.searchsorted(pt_pos, idx)
            if hits < len(pt_pos) and pt_pos[hits] == idx:
                h_idx = hits

        h_vec = hist_merged[h_idx] if h_idx is not None else None
        g_count = int(ground_merged[h_idx]) if h_idx is not None else 0
        n_pointcloud_samples = g_count + (
            int(h_vec.sum()) if h_vec is not None else 0
        )
        rec["has_pointcloud_data"] = bool(
            n_pointcloud_samples >= min_points_per_cell
        )
        rec["n_pointcloud_samples"] = n_pointcloud_samples

        p_needed: set[int] = set()
        if percentiles_map and len(vals):
            p_needed.update(percentiles_map.values())
        if needs_L_skewness and len(vals):
            p_needed.update((10, 50, 90))
        p_vals = None
        if p_needed:
            p_sorted = sorted(p_needed)
            p_computed = np.percentile(vals, p_sorted)
            p_vals = dict(zip(p_sorted, p_computed))

        for metric in metrics_list:
            if metric in percentiles_map:
                rec[metric] = (
                    float(p_vals[percentiles_map[metric]]) if p_vals else ABA_NODATA
                )
                continue
            if metric == "L_skewness":
                if p_vals and (p_vals[90] - p_vals[10]) > 0:
                    rec[metric] = float(
                        (p_vals[90] - 2 * p_vals[50] + p_vals[10])
                        / (p_vals[90] - p_vals[10])
                    )
                else:
                    rec[metric] = 0.0
                continue
            if metric in POINT_METRICS:
                rec[metric] = (
                    _hist_metrics(h_vec, metric, canopy_bin)
                    if h_vec is not None else ABA_NODATA
                )
                continue
            if metric == "ground_density":
                rec[metric] = (
                    g_count / rec["cell_area_m2"]
                    if h_idx is not None
                    and (rec["has_pointcloud_data"] or g_count)
                    else ABA_NODATA
                )
                continue
            if metric == "reliability_score":
                if h_idx is None:
                    rec[metric] = ABA_NODATA
                else:
                    gd = g_count / rec["cell_area_m2"]
                    rec[metric] = float(1.0 - np.exp(-reliability_k * gd))
                continue
            # CHM scalars
            if metric in CHM_SCALAR_METRICS and len(vals):
                if metric == "h_mean":
                    rec[metric] = float(np.mean(vals))
                elif metric == "h_max":
                    rec[metric] = float(np.max(vals))
                elif metric == "h_std":
                    rec[metric] = float(np.std(vals))
                elif metric == "h_cv":
                    rec[metric] = _calc_h_cv(vals)
                elif metric == "h_kurtosis":
                    rec[metric] = _calc_h_kurtosis(vals)
                elif metric == "canopy_cover":
                    rec[metric] = _calc_canopy_cover(vals, canopy_threshold)
                elif metric == "LAI":
                    rec[metric] = _calc_lai(vals, canopy_threshold, lai_k)
                elif metric == "rumple" and rumple_gx is not None:
                    rec[metric] = _calc_rumple(
                        rumple_gx[i0:i1, j0:j1],
                        rumple_gy[i0:i1, j0:j1],
                        pixel_size,
                    )
                else:
                    rec[metric] = ABA_NODATA
            elif metric in CHM_SCALAR_METRICS:
                rec[metric] = ABA_NODATA

        geometries.append((x_min, y_min, x_max, y_max))
        records.append(rec)

    # Final sanitization: no non-finite value survives (all to ABA_NODATA).
    import math

    for rec in records:
        for col, val in rec.items():
            if isinstance(val, float) and not math.isfinite(val):
                rec[col] = ABA_NODATA

    # --- 5. GeoPackage or CSV + metadata --------------------------------------
    import geopandas as gpd

    from lynceus.processing import provenance

    prov_doc = provenance.build_provenance(ctx, "area_based_metrics.gpkg")

    if include_geometry:
        from shapely.geometry import box as shp_box

        geoms = [shp_box(*b) for b in geometries]
        gdf = gpd.GeoDataFrame(records, geometry=geoms, crs=crs)
        gpkg_path = session / "area_based_metrics.gpkg"
        gdf.to_file(gpkg_path, driver="GPKG")
        provenance.embed_gpkg_metadata(gpkg_path, prov_doc)
        output_file = str(gpkg_path)
    else:
        csv_path = session / "area_based_metrics.csv"
        import csv

        if records:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
        output_file = str(csv_path)

    metadata = {
        "version": "aba_lynceus_v1",
        "generated_at": datetime.datetime.now().isoformat(),
        "node_id": NODE_ID,
        "grid_size_m": gs,
        "n_cells": n_cells,
        "metrics_computed": metrics_list,
        "default_metric": metrics_list[0] if metrics_list else None,
        "bookkeeping_cols": [
            "cell_col", "cell_row",
            "x_min", "y_min", "x_max", "y_max",
            "cell_area_m2", "n_valid_pixels",
            "has_pointcloud_data", "n_pointcloud_samples",
        ],
        "nodata": ABA_NODATA,
        "point_data_available": bool(point_data_available),
    }
    meta_path = provenance.write_sidecar(output_file, prov_doc, metadata)

    summary_path = session / "area_based_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["cell", "metrics"])
        writer.writerow([f"{n_cells} cells", ", ".join(metrics_list)])

    return {
        "file": output_file,
        "kind": "ABA",
        "stats_csv": str(summary_path),
        "meta_json": str(meta_path),
        "node": NODE_ID,
        "warnings": warnings,
    }
