# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Node - tree crown delineation from CHM + treetops.

Barrier task: marker-controlled watershed (treetops as seeds) on the
smoothed CHM with Silva-style exclusion. Writes a GeoPackage of crown
polygons plus a summary CSV.
"""

from __future__ import annotations

from pathlib import Path

from lynceus.nodes.ports import PortType

NODE_ID = "forestry.crown_delineation"
NODE_NAME = "Tree Crowns"
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
    "<b>Tree Crowns</b> -- Crown Polygons from CHM + Treetops<br><br>"
    "Delineates one crown polygon per treetop with a marker-controlled "
    "watershed over the smoothed canopy: water floods outward from each "
    "treetop until basins meet, and meeting lines become crown borders. "
    "Low edge pixels drop out by the exclusion fraction.<br><br>"
    "<b>Process:</b> Wire a Treetops node for the seeds plus the same CHM. "
    "Segments without a treetop (orphans) are discarded; crowns report "
    "treetop height and polygon area.<br><br>"
    "<b>Tips:</b> Same CHM and smoothing on both nodes, or seeds and "
    "surface disagree. Raise exclusion to shrink crowns to their tops; "
    "lower it to keep full skirts (risk of merging neighbors)."
)

INPUTS = (PortType.CHM_MOSAIC, PortType.VECTOR)
OUTPUTS = (PortType.VECTOR, PortType.TABLE_CSV)

PROCESSING_SPECS = {
    "barrier_task": "barrier_crown_delineation",
    "input_ports": ("chm_mosaic", "vector"),
    "output_ports": ("vector", "table_csv"),
    "output_files": {
        "vector": "tree_crowns.gpkg",
        "table_csv": "tree_crowns_stats.csv",
    },
    "output_globs": ("tree_crowns.gpkg", "tree_crowns_stats.csv"),
    "qml": {
        "tree_crowns.gpkg": {
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
        "smooth_sigma": {
            "type": "float",
            "default": 1.0,
            "minimum": 0.0,
            "maximum": 3.0,
            "description": "Gaussian smoothing radius (cells) applied to the CHM first.",
            "impact": "Suppresses pits and noise that split crowns. 0 disables; higher values merge close crowns.",
            "group": "Preprocessing",
        },
        "exclusion": {
            "type": "float",
            "default": 0.3,
            "minimum": 0.0,
            "maximum": 0.9,
            "description": "Fraction of tree height below which crown pixels are dropped.",
            "impact": "Removes low edge pixels from each crown (Silva method). Higher values shrink crowns to their tops; 0 keeps every pixel above min height.",
            "group": "Filtering",
        },
        "simplify_tolerance": {
            "type": "float",
            "default": 0.5,
            "minimum": 0.0,
            "maximum": 10.0,
            "description": "Polygon simplification tolerance (meters).",
            "impact": "Smooths jagged crown boundaries. Higher values produce simpler polygons but lose edge detail. Set to 0 to keep pixel-exact boundaries.",
            "group": "Output",
        },
    },
}


def get_config_defaults() -> dict:
    return {
        k: v["default"] for k, v in PROCESSING_SPECS["config_schema"].items()
    }


def barrier_crown_delineation(ctx: dict) -> dict:
    """Delineate one crown polygon per treetop (marker watershed)."""
    import csv

    import numpy as np
    import rasterio
    from rasterio.features import shapes as rio_shapes
    from scipy.ndimage import watershed_ift
    from shapely.geometry import shape

    from lynceus.processing import provenance

    prov_doc = provenance.build_provenance(ctx, "tree_crowns.gpkg")
    session = Path(ctx["session_dir"])
    chm_path = Path(ctx.get("chm_mosaic_path", str(session / "chm_mosaic.tif")))
    # NOTE: Path("") normalizes to Path(".") which exists: test the raw
    # string first, and require a file (a directory is not a vector).
    tops_raw = ctx.get("vector_path", ctx.get("in1_path", "")) or ""
    tops_path = Path(tops_raw)
    if not chm_path.exists():
        raise RuntimeError("Tree Crowns requires a CHM mosaic upstream")
    if not tops_raw or not tops_path.is_file():
        raise RuntimeError(
            "Tree Crowns requires a wired Treetops vector for the seeds"
        )

    min_height = float(ctx.get("min_height_m", 2.0))
    smooth_sigma = float(ctx.get("smooth_sigma", 1.0))
    exclusion = float(ctx.get("exclusion", 0.3))
    simplify_tolerance = float(ctx.get("simplify_tolerance", 0.5))

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

    import geopandas as gpd

    tops = gpd.read_file(str(tops_path))
    if "height_m" not in tops.columns:
        raise RuntimeError(
            "Tree Crowns needs a treetops vector with a height_m column"
        )
    inv = ~transform
    markers = np.zeros(chm.shape, dtype=np.int32)
    seed_heights: dict[int, float] = {}
    for pos, point in enumerate(tops.itertuples(), start=1):
        col_f, row_f = inv * (point.geometry.x, point.geometry.y)
        col, row = int(round(col_f)), int(round(row_f))
        if 0 <= row < chm.shape[0] and 0 <= col < chm.shape[1]:
            if markers[row, col] == 0:
                markers[row, col] = pos
                seed_heights[pos] = float(point.height_m)

    crown_mask = valid & (work >= min_height)
    if not crown_mask.any() or not seed_heights:
        raise RuntimeError("Tree Crowns found no workable crowns")

    # Flood the inverted canopy from the treetop minima. watershed_ift
    # only takes uint8/uint16 input: scale decimeters (100x) so treetops
    # read 0 (deepest) and unmasked cells read max (walls, flood last).
    top = float(work[crown_mask].max())
    surface_u16 = np.full(chm.shape, 65535, dtype=np.uint16)
    surface_u16[crown_mask] = np.clip(
        np.round((top - work[crown_mask]) * 100.0), 0, 65534
    ).astype(np.uint16)
    flooded = watershed_ift(surface_u16, markers)
    # Silva-style exclusion on pixels, before polygonizing: drop segment
    # pixels below exclusion * tree height.
    final_labels = flooded
    if exclusion > 0:
        trimmed = flooded.copy()
        for label_val, height in seed_heights.items():
            drop = (flooded == label_val) & (work < exclusion * height)
            trimmed[drop] = 0
        final_labels = trimmed
    geometries: list = []
    records: list[dict] = []
    # Polygonize inside the crown mask only: cells outside it may have
    # caught a late label from the walls.
    keep_mask = (final_labels > 0) & crown_mask
    for geom_dict, label_val in rio_shapes(
        np.where(keep_mask, final_labels, 0).astype(np.int32),
        mask=keep_mask, transform=transform,
    ):
        label_val = int(label_val)
        if label_val <= 0 or label_val not in seed_heights:
            continue
        poly = shape(geom_dict)
        tree_h = seed_heights[label_val]
        area_m2 = poly.area
        if simplify_tolerance > 0:
            poly = poly.simplify(simplify_tolerance, preserve_topology=True)
        if poly.is_valid and not poly.is_empty and area_m2 > 0:
            records.append(
                {
                    "tree_id": len(records) + 1,
                    "height_m": round(tree_h, 2),
                    "crown_area_m2": round(area_m2, 1),
                }
            )
            geometries.append(poly)

    gpkg_path = session / "tree_crowns.gpkg"
    if geometries:
        gdf = gpd.GeoDataFrame(records, geometry=geometries, crs=crs)
        gdf.to_file(gpkg_path, driver="GPKG")
        provenance.embed_gpkg_metadata(gpkg_path, prov_doc)
    else:
        gpkg_path = None

    stats_path = session / "tree_crowns_stats.csv"
    areas = [r["crown_area_m2"] for r in records]
    heights = [r["height_m"] for r in records]
    with open(stats_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["total_crowns", len(records)])
        writer.writerow(["mean_crown_area_m2",
                         f"{(sum(areas) / len(areas)):.1f}" if areas else "0.0"])
        writer.writerow(["mean_height_m",
                         f"{(sum(heights) / len(heights)):.2f}" if heights else "0.0"])
    provenance.write_sidecar(
        stats_path,
        provenance.build_provenance(ctx, "tree_crowns_stats.csv"),
        {"node_id": NODE_ID},
    )

    payload = {
        "kind": "CROWNS",
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
                "metrics_computed": ["height_m", "crown_area_m2"],
                "default_metric": "height_m",
                "bookkeeping_cols": ["tree_id"],
            },
        )
        payload["meta_json"] = str(meta_path)
    return payload
