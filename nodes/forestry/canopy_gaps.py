# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Node - canopy gap detection from the CHM mosaic.

Barrier task: threshold-based gap detection with connected components,
polygon extraction and area filtering. Writes a GeoPackage of gaps plus a
summary CSV.
"""

from __future__ import annotations

from pathlib import Path

from lynceus.nodes.ports import PortType

NODE_ID = "forestry.canopy_gaps"
NODE_NAME = "Canopy Gaps"
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
    "<b>Canopy Gaps</b> -- Gap Detection from CHM<br><br>"
    "Identifies open areas within the forest canopy by thresholding the CHM "
    "and extracting connected components as gap polygons.<br><br>"
    "<b>Process:</b> Pixels with height below <i>height_threshold</i> are labeled "
    "as gaps. Connected gap pixels are grouped into polygons, filtered by area "
    "limits, and optionally simplified. Results include a GeoPackage of gap "
    "polygons and a summary CSV with gap density statistics.<br><br>"
    "<b>Tips:</b> For dense forests, use a lower <i>height_threshold</i> (1-2m) "
    "to detect small gaps. For open woodlands, increase it (3-5m). "
    "Adjust <i>min_gap_area</i> to filter out noise from individual bare pixels."
)

INPUTS = (PortType.CHM_MOSAIC,)
OUTPUTS = (PortType.VECTOR, PortType.TABLE_CSV)

PROCESSING_SPECS = {
    "barrier_task": "barrier_canopy_gaps",
    "input_ports": ("chm_mosaic",),
    "output_ports": ("vector", "table_csv"),
    "output_files": {
        "vector": "canopy_gaps.gpkg",
        "table_csv": "canopy_gaps_stats.csv",
    },
    "output_globs": ("canopy_gaps.gpkg", "canopy_gaps.csv", "canopy_gaps_stats.csv"),
    "qml": {
        "canopy_gaps.gpkg": {
            "field": "area_m2",
            "name": "Gap Area",
            "classes": [
                (0.0, 20.0, "#4575b4", "0 \u2013 20 m\u00b2"),
                (20.0, 50.0, "#91bfdb", "20 \u2013 50 m\u00b2"),
                (50.0, 200.0, "#ffffbf", "50 \u2013 200 m\u00b2"),
                (200.0, 500.0, "#fc8d59", "200 \u2013 500 m\u00b2"),
                (500.0, 99999.0, "#d73027", "500+ m\u00b2"),
            ],
        },
    },
    "config_schema": {
        "height_threshold": {
            "type": "float",
            "default": 2.0,
            "minimum": 0.0,
            "maximum": 30.0,
            "description": "Maximum CHM height (meters) to consider a pixel as gap.",
            "impact": "Lower values detect only very open areas (ground-level gaps). Higher values include understory-level gaps and forest edges in the detection.",
            "group": "Detection",
        },
        "min_gap_area_m2": {
            "type": "float",
            "default": 10.0,
            "minimum": 0.0,
            "maximum": 10000.0,
            "description": "Minimum gap area (m2) to include in output.",
            "impact": "Filters out tiny isolated gap pixels. Increase to focus on ecologically significant gaps.",
            "group": "Filtering",
        },
        "max_gap_area_m2": {
            "type": "float",
            "default": 10000.0,
            "minimum": 0.0,
            "maximum": 1000000.0,
            "description": "Maximum gap area (m2) to include in output.",
            "impact": "Excludes very large open areas (clearings, edges) that may not represent true canopy gaps.",
            "group": "Filtering",
        },
        "simplify_tolerance": {
            "type": "float",
            "default": 0.5,
            "minimum": 0.0,
            "maximum": 10.0,
            "description": "Polygon simplification tolerance (meters).",
            "impact": "Smooths jagged gap boundaries. Higher values produce simpler polygons but lose edge detail. Set to 0 to keep pixel-exact boundaries.",
            "group": "Output",
        },
        "min_gap_width_m": {
            "type": "float",
            "default": 0.0,
            "minimum": 0.0,
            "maximum": 50.0,
            "description": "Minimum width (meters) of a gap to be included.",
            "impact": "Filters out thin gaps that may be artifacts of narrow trails or single-pixel noise. The width is estimated from the minimum bounding box of each polygon. Set to 0 to disable.",
            "group": "Filtering",
        },
        "smooth_chm": {
            "type": "bool",
            "default": False,
            "description": "Apply Gaussian smoothing to the CHM before gap detection.",
            "impact": "Reduces noise that creates small spurious gaps. Recommended for high-resolution CHMs (sub-meter). Increases processing time.",
            "group": "Preprocessing",
        },
    },
}


def get_config_defaults() -> dict:
    return {
        k: v["default"] for k, v in PROCESSING_SPECS["config_schema"].items()
    }


def barrier_canopy_gaps(ctx: dict) -> dict:
    """Detect canopy gaps in the CHM and write a GeoPackage plus CSV summary."""
    import csv

    import numpy as np
    import rasterio
    from rasterio.features import shapes as rio_shapes
    from scipy.ndimage import label
    from shapely.geometry import shape

    from lynceus.processing import provenance

    prov_doc = provenance.build_provenance(ctx, "canopy_gaps.gpkg")
    session = Path(ctx["session_dir"])
    chm_path = Path(ctx.get("chm_mosaic_path", str(session / "chm_mosaic.tif")))
    if not chm_path.exists():
        raise RuntimeError("Canopy Gaps requires a CHM mosaic upstream")

    height_threshold = float(ctx.get("height_threshold", 2.0))
    min_gap_area_m2 = float(ctx.get("min_gap_area_m2", 10.0))
    max_gap_area_m2 = float(ctx.get("max_gap_area_m2", 10000.0))
    simplify_tolerance = float(ctx.get("simplify_tolerance", 0.5))
    min_gap_width_m = float(ctx.get("min_gap_width_m", 0.0))
    smooth_chm = bool(ctx.get("smooth_chm", False))

    with rasterio.open(str(chm_path)) as src:
        chm = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
        rows, cols = chm.shape

    if smooth_chm:
        from lynceus.processing.raster import smooth_nodata

        chm = smooth_nodata(chm, sigma=0.5, nodata=nodata if nodata is not None else -9999.0)

    valid = np.isfinite(chm)
    if nodata is not None:
        valid &= chm != nodata
    gap_mask = valid & (chm <= height_threshold)

    labeled, n_components = label(gap_mask)

    geometries: list = []
    records: list[dict] = []
    for geom_dict, label_val in rio_shapes(
        labeled.astype(np.uint32), mask=gap_mask, transform=transform
    ):
        if label_val == 0:
            continue
        poly = shape(geom_dict)
        area_m2 = poly.area
        if area_m2 < min_gap_area_m2 or area_m2 > max_gap_area_m2:
            continue
        if min_gap_width_m > 0:
            minx, miny, maxx, maxy = poly.bounds
            width = min(maxx - minx, maxy - miny)
            if width < min_gap_width_m:
                continue
        if simplify_tolerance > 0:
            poly = poly.simplify(simplify_tolerance, preserve_topology=True)
        if poly.is_valid and not poly.is_empty:
            records.append(
                {
                    "gap_id": len(records) + 1,
                    "area_m2": round(area_m2, 1),
                    "perimeter_m": round(poly.length, 1),
                    "compactness": round(
                        (4 * np.pi * area_m2) / (poly.length ** 2 + 1e-6), 3
                    ),
                }
            )
            geometries.append(poly)

    gpkg_path = session / "canopy_gaps.gpkg"
    if geometries:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(records, geometry=geometries, crs=crs)
        gdf.to_file(gpkg_path, driver="GPKG")
        provenance.embed_gpkg_metadata(gpkg_path, prov_doc)
    else:
        gpkg_path = None

    pixel_area_m2 = abs(transform.a * transform.e)
    total_gaps = len(records)
    if total_gaps:
        areas = [r["area_m2"] for r in records]
        total_area = sum(areas)
        mean_area = total_area / total_gaps
        max_area = max(areas)
        scene_area = rows * cols * pixel_area_m2
        density_pct = (total_area / scene_area * 100.0) if scene_area > 0 else 0.0
    else:
        total_area = mean_area = max_area = density_pct = 0.0

    stats_path = session / "canopy_gaps_stats.csv"
    with open(stats_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["total_gaps", total_gaps])
        writer.writerow(["total_area_m2", f"{total_area:.1f}"])
        writer.writerow(["mean_area_m2", f"{mean_area:.1f}"])
        writer.writerow(["max_area_m2", f"{max_area:.1f}"])
        writer.writerow(["gap_density_pct", f"{density_pct:.1f}"])
    provenance.write_sidecar(
        stats_path,
        provenance.build_provenance(ctx, "canopy_gaps_stats.csv"),
        {"node_id": NODE_ID},
    )

    payload = {
        "kind": "GAPS",
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
                "metrics_computed": ["area_m2"],
                "default_metric": "area_m2",
                "bookkeeping_cols": ["gap_id"],
            },
        )
        payload["meta_json"] = str(meta_path)
    return payload
