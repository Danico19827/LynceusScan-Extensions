# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Node - CHM classification into vertical strata.

Barrier task: reads the CHM mosaic, classifies each pixel into one of six
vertical strata (ground..emergent) and writes a categorical GeoTIFF plus a
per-stratum area CSV.
"""

from __future__ import annotations

from pathlib import Path

from lynceus.nodes.ports import PortType

NODE_ID = "forestry.height_strata"
NODE_NAME = "Height Strata"
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
    "<b>Height Strata</b> -- Vertical Canopy Stratification<br><br>"
    "Classifies each CHM pixel into one of six predefined vertical strata "
    "based on height ranges.<br><br>"
    "<b>Strata:</b> ground (0-0.5m), understory (0.5-2m), regrowth (2-5m), "
    "intermediate (5-10m), upper canopy (10-20m), emergent (20-60m). "
    "Outputs a categorical GeoTIFF and a per-stratum area summary CSV.<br><br>"
    "<b>Tips:</b> Use this to visualize vertical forest structure and compare "
    "stratum distribution across sites. The strata boundaries are fixed for "
    "consistency across projects."
)

INPUTS = (PortType.CHM_MOSAIC,)
OUTPUTS = (PortType.STRATA_RASTER, PortType.TABLE_CSV)

PROCESSING_SPECS = {
    "barrier_task": "barrier_height_strata",
    "input_ports": ("chm_mosaic",),
    "output_ports": ("strata_raster", "table_csv"),
    "output_files": {
        "strata_raster": "height_strata.tif",
        "table_csv": "height_strata_classes.csv",
    },
    "output_globs": ("height_strata.tif", "height_strata_classes.csv"),
    "qml": {
        "height_strata.tif": {"self_styled": True},
    },
    "config_schema": {
        "strata_config": {
            "type": "list",
            "default": [
                {"name": "ground", "min": 0.0, "max": 0.5},
                {"name": "understory", "min": 0.5, "max": 2.0},
                {"name": "regrowth", "min": 2.0, "max": 5.0},
                {"name": "intermediate", "min": 5.0, "max": 10.0},
                {"name": "upper_canopy", "min": 10.0, "max": 20.0},
                {"name": "emergent", "min": 20.0, "max": 60.0},
            ],
            "description": "List of strata as {name, min, max} dicts in meters.",
            "impact": "Define custom vertical strata for your analysis. Each stratum covers [min, max) in meters. Pixels outside all defined strata become NODATA.",
            "group": "Strata",
        },
        "min_area_ha": {
            "type": "float",
            "default": 0.0,
            "minimum": 0.0,
            "maximum": 100.0,
            "description": "Minimum area (hectares) for a stratum to be reported.",
            "impact": "Filters out tiny stratum patches in the summary table. Useful for removing noise. Set to 0 to report all strata regardless of size.",
            "group": "Output",
        },
    },
}

# (name, minimum height m, maximum height m); fixed half-open intervals [lo, hi).
STRATA_LABELS: list[tuple[str, float, float]] = [
    ("ground", 0.0, 0.5),
    ("understory", 0.5, 2.0),
    ("regrowth", 2.0, 5.0),
    ("intermediate", 5.0, 10.0),
    ("upper_canopy", 10.0, 20.0),
    ("emergent", 20.0, 60.0),
]

STRATA_NODATA = -1

# Strata ramp: ground is brown; higher strata use progressively darker greens.
GROUND_COLOR = "#8B5A2B"
_GREEN_LIGHT = (0xA6, 0xD9, 0x6A)
_GREEN_DARK = (0x00, 0x44, 0x1B)


def _strata_label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _strata_green(frac: float) -> str:
    rgb = tuple(
        round(_GREEN_LIGHT[i] + (_GREEN_DARK[i] - _GREEN_LIGHT[i]) * frac)
        for i in range(3)
    )
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _strata_qml_entries(strata_config: list) -> list[tuple[int, str, str]]:
    n = len(strata_config)
    entries: list[tuple[int, str, str]] = []
    for idx, stratum in enumerate(strata_config):
        name = str(stratum.get("name", f"stratum_{idx}"))
        if idx == 0:
            color = GROUND_COLOR
        else:
            color = _strata_green(idx / (n - 1) if n > 1 else 0.0)
        entries.append((idx, color, _strata_label(name)))
    return entries


def _write_strata_qml(out_path, strata_config: list) -> None:
    """Write a QML sidecar using the strata classes from this run."""
    if not strata_config:
        return
    from lynceus.processing.qml_style import generate_discrete_qml

    generate_discrete_qml(
        str(out_path), _strata_qml_entries(strata_config), "Height Strata"
    )


def get_config_defaults() -> dict:
    return {
        "strata_config": [
            {"name": "ground", "min": 0.0, "max": 0.5},
            {"name": "understory", "min": 0.5, "max": 2.0},
            {"name": "regrowth", "min": 2.0, "max": 5.0},
            {"name": "intermediate", "min": 5.0, "max": 10.0},
            {"name": "upper_canopy", "min": 10.0, "max": 20.0},
            {"name": "emergent", "min": 20.0, "max": 60.0},
        ],
        "min_area_ha": 0.0,
    }


def barrier_height_strata(ctx: dict) -> dict:
    """Classify the CHM into vertical strata and generate summary statistics."""
    import csv

    import numpy as np
    import rasterio

    from lynceus.processing import provenance

    prov_doc = provenance.build_provenance(ctx, "height_strata.tif")
    session = Path(ctx["session_dir"])
    chm_path = Path(ctx.get("chm_mosaic_path", str(session / "chm_mosaic.tif")))
    if not chm_path.exists():
        raise RuntimeError("Height Strata requires a CHM mosaic upstream")

    strata_config = ctx.get("strata_config") or get_config_defaults()["strata_config"]
    min_area_ha = float(ctx.get("min_area_ha", 0.0))

    with rasterio.open(str(chm_path)) as src:
        chm = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
        rows, cols = chm.shape

    valid = np.isfinite(chm)
    if nodata is not None:
        valid &= chm != nodata
    valid &= chm >= 0.0  # Negative heights do not belong to a stratum.

    strata = np.full(chm.shape, STRATA_NODATA, dtype=np.int32)
    for idx, stratum in enumerate(strata_config):
        lo = float(stratum.get("min", 0.0))
        hi = float(stratum.get("max", lo + 1.0))
        mask = valid & (chm >= lo) & (chm < hi)
        strata[mask] = idx

    out_path = session / "height_strata.tif"
    with rasterio.open(
        str(out_path),
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype="int32",
        crs=crs,
        transform=transform,
        nodata=STRATA_NODATA,
        compress="lzw",
    ) as dst:
        dst.write(strata, 1)
    provenance.embed_tiff_tags(out_path, prov_doc)

    pixel_area_m2 = abs(transform.a * transform.e)
    n_valid = int(valid.sum())
    total_ha = 0.0
    table = []
    for idx, stratum in enumerate(strata_config):
        name = str(stratum.get("name", f"stratum_{idx}"))
        lo = float(stratum.get("min", 0.0))
        hi = float(stratum.get("max", lo + 1.0))
        count = int((strata == idx).sum())
        area_ha = count * pixel_area_m2 / 10000.0
        pct = (count / n_valid * 100.0) if n_valid > 0 else 0.0
        total_ha += area_ha
        if min_area_ha > 0 and area_ha < min_area_ha:
            continue
        table.append((name, lo, hi, count, area_ha, pct))

    stats_path = session / "height_strata_classes.csv"
    with open(stats_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["stratum", "lower_limit_m", "upper_limit_m",
             "pixels", "area_ha", "percentage"]
        )
        for name, lo, hi, count, area_ha, pct in table:
            pct_total = (area_ha / total_ha * 100.0) if total_ha > 0 else 0.0
            writer.writerow([name, f"{lo:.1f}", f"{hi:.1f}",
                             count, f"{area_ha:.2f}", f"{pct_total:.1f}"])
        writer.writerow(["total", "", "", n_valid, f"{total_ha:.2f}", "100.0"])

    provenance.write_sidecar(stats_path, prov_doc, {"node_id": NODE_ID})
    _write_strata_qml(out_path, strata_config)

    return {
        "file": str(out_path),
        "kind": "STRATA",
        "stats_csv": str(stats_path),
        "node": NODE_ID,
    }
