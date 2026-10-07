# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Node - treetop detection from the CHM mosaic.

Barrier task: variable-window local maxima (Popescu & Wynne style) on a
smoothed CHM. Writes a GeoPackage of treetop points plus a summary CSV.
"""

from __future__ import annotations

from pathlib import Path

from lynceus.nodes.ports import PortType

NODE_ID = "forestry.treetops"
NODE_NAME = "Treetops"
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
    "<b>Treetops</b> -- Individual Tree Detection from CHM<br><br>"
    "Finds treetops as local maxima of the canopy height model with a "
    "height-variable window (taller trees get wider windows), after "
    "Gaussian smoothing. Each plateau counts once, at its centroid.<br><br>"
    "<b>Process:</b> The CHM is smoothed, heights are banded every 2 m, "
    "and each band takes local maxima with its own window from the crown "
    "factor. Maxima below the minimum height are ignored.<br><br>"
    "<b>Tips:</b> Feed it a pit-free CHM (Generate CHM with pit_free on) "
    "to avoid false tops in pits. Raise the crown factor to merge "
    "neighboring crowns, lower it to split them (risk of over-counting)."
)

INPUTS = (PortType.CHM_MOSAIC,)
OUTPUTS = (PortType.VECTOR, PortType.TABLE_CSV)

PROCESSING_SPECS = {
    "barrier_task": "barrier_treetops",
    "input_ports": ("chm_mosaic",),
    "output_ports": ("vector", "table_csv"),
    "output_files": {
        "vector": "treetops.gpkg",
        "table_csv": "treetops_stats.csv",
    },
    "output_globs": ("treetops.gpkg", "treetops_stats.csv"),
    "qml": {
        "treetops.gpkg": {
            "field": "height_m",
            "name": "Tree Height",
            "classes": [
                (0.0, 5.0, "#ffffb2", "0 \u2013 5 m"),
                (5.0, 10.0, "#fecc5c", "5 \u2013 10 m"),
                (10.0, 20.0, "#fd8d3c", "10 \u2013 20 m"),
                (20.0, 35.0, "#f03b20", "20 \u2013 35 m"),
                (35.0, 999.0, "#bd0026", "35+ m"),
            ],
        },
    },
    "config_schema": {
        "min_height_m": {
            "type": "float",
            "default": 2.0,
            "minimum": 0.0,
            "maximum": 30.0,
            "description": "Minimum CHM height (meters) to process.",
            "impact": "Below this nothing is a tree: raise to ignore shrubs and regeneration.",
            "group": "Detection",
        },
        "crown_factor": {
            "type": "float",
            "default": 0.6,
            "minimum": 0.2,
            "maximum": 1.5,
            "description": "Crown diameter as a fraction of tree height for the detection window.",
            "impact": "Wider windows merge neighboring crowns (fewer, bigger trees); narrower windows split crowns (more, smaller trees, risk of over-counting). 0.6 follows the forestry literature.",
            "group": "Detection",
        },
        "smooth_sigma": {
            "type": "float",
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 3.0,
            "description": "Gaussian smoothing radius (cells) applied to the CHM first.",
            "impact": "Suppresses pits and noise that split crowns. 0 disables; higher values merge close crowns.",
            "group": "Preprocessing",
        },
    },
}


def get_config_defaults() -> dict:
    return {
        k: v["default"] for k, v in PROCESSING_SPECS["config_schema"].items()
    }


def barrier_treetops(ctx: dict) -> dict:
    """Detect treetops as banded variable-window maxima of the CHM."""
    import csv

    import numpy as np
    import rasterio
    from scipy.ndimage import center_of_mass, label, maximum_filter

    from lynceus.processing import provenance

    prov_doc = provenance.build_provenance(ctx, "treetops.gpkg")
    session = Path(ctx["session_dir"])
    # NOTE: Path("") normalizes to Path(".") which exists: test the raw
    # string first, and require a file.
    chm_raw = ctx.get("chm_mosaic_path",
                      str(session / "chm_mosaic.tif")) or ""
    chm_path = Path(chm_raw)
    if not chm_raw or not chm_path.is_file():
        raise RuntimeError("Treetops requires a CHM mosaic upstream")

    min_height = float(ctx.get("min_height_m", 2.0))
    crown_factor = float(ctx.get("crown_factor", 0.6))
    smooth_sigma = float(ctx.get("smooth_sigma", 1.0))

    with rasterio.open(str(chm_path)) as src:
        chm = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata

    nodata = nodata if nodata is not None else -9999.0
    valid = np.isfinite(chm) & (chm != nodata)
    work = np.where(valid, chm, 0.0).astype(np.float32)
    if smooth_sigma > 0:
        from lynceus.processing.raster import smooth_nodata

        work = smooth_nodata(
            np.where(valid, chm, nodata).astype(np.float32),
            sigma=smooth_sigma, nodata=nodata,
        )
        work = np.where(valid, work, 0.0).astype(np.float32)

    res = abs(float(transform.a))
    if res <= 0:
        raise RuntimeError("Treetops needs a CHM with positive resolution")
    maxima = np.zeros_like(valid, dtype=bool)
    top = float(work[valid].max(initial=0.0))
    band = 2.0
    lo = min_height
    while lo <= top:
        hi = lo + band
        in_band = valid & (work >= lo) & (work < hi)
        if in_band.any():
            win = max(3, int(round(crown_factor * hi / res)) | 1)
            local_max = maximum_filter(work, size=win) <= work + 1e-6
            maxima |= in_band & local_max
        lo = hi

    labeled, n_labels = label(maxima)
    points: list = []
    records: list[dict] = []
    for label_val in range(1, n_labels + 1):
        cells = labeled == label_val
        cy, cx = center_of_mass(cells)
        col = int(round(float(cx)))
        row = int(round(float(cy)))
        col = min(max(col, 0), chm.shape[1] - 1)
        row = min(max(row, 0), chm.shape[0] - 1)
        height = float(chm[row, col]) if valid[row, col] else float(work[row, col])
        east = float(transform.c + (col + 0.5) * transform.a)
        north = float(transform.f + (row + 0.5) * transform.e)
        records.append(
            {
                "tree_id": len(records) + 1,
                "height_m": round(height, 2),
                "win_m": round(
                    max(3 * res, crown_factor * max(height, min_height)), 2),
            }
        )
        from shapely.geometry import Point

        points.append(Point(east, north))

    gpkg_path = session / "treetops.gpkg"
    if points:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(records, geometry=points, crs=crs)
        gdf.to_file(gpkg_path, driver="GPKG")
        provenance.embed_gpkg_metadata(gpkg_path, prov_doc)
    else:
        gpkg_path = None

    stats_path = session / "treetops_stats.csv"
    heights = [r["height_m"] for r in records]
    with open(stats_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["total_trees", len(records)])
        writer.writerow(["mean_height_m",
                         f"{(sum(heights) / len(heights)):.2f}" if heights else "0.0"])
        writer.writerow(["max_height_m",
                         f"{max(heights):.2f}" if heights else "0.0"])
    provenance.write_sidecar(
        stats_path,
        provenance.build_provenance(ctx, "treetops_stats.csv"),
        {"node_id": NODE_ID},
    )

    payload = {
        "kind": "TREETOPS",
        "stats_csv": str(stats_path),
        "node": NODE_ID,
    }
    if gpkg_path is not None:
        payload["file"] = str(gpkg_path)
        meta_path = provenance.write_sidecar(
            gpkg_path,
            prov_doc,
            {
                "node_id": NODE_ID,
                "metrics_computed": ["height_m"],
                "default_metric": "height_m",
                "bookkeeping_cols": ["tree_id"],
            },
        )
        payload["meta_json"] = str(meta_path)
    return payload
