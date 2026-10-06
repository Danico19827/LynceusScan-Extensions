# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Taritolay, Nicolás Daniel <lynceusscan@gmail.com>
"""Node: Open-Meteo weather query.

Queries the Open-Meteo API (https://open-meteo.com/) for weather data at the
location represented by an upstream LAS/LAZ source.

Barrier-only node (no tiling). Coordinates are read from the connected LAS
file when available; otherwise the configured latitude and longitude are used.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from lynceus.nodes.ports import PortDef, PortType


# ---------------------------------------------------------------------------
# 1. Node metadata
# ---------------------------------------------------------------------------
NODE_ID = "openmeteo.query"
NODE_NAME = "Open-Meteo Query"
NODE_CATEGORY = "Input"
NODE_AUTHOR = "Taritolay, Nicolás Daniel"
NODE_LICENSE = "GPL-3.0-or-later"
NODE_REPOSITORY = "https://github.com/Danico19827/LynceusScan-Extensions"
NODE_HOMEPAGE = "https://danico19827.github.io/LynceusScan-Web/extensions/"
NODE_EULA = "Provided as-is without warranty of any kind."

INPUTS = (
    PortDef(PortType.POINT_CLOUD, "LAS Source"),
)

OUTPUTS = (
    PortDef(PortType.TABLE_CSV, "weather_data"),
)

# ---------------------------------------------------------------------------
# 2. Processing specs
# ---------------------------------------------------------------------------
PROCESSING_SPECS = {
    "barrier_task": "barrier_open_meteo_query",
    "input_ports": ("point_cloud",),
    "output_ports": ("table_csv",),
    "output_files": {"table_csv": "weather_data.csv"},
    "output_globs": (
        "weather_data.csv",
        "weather_data.meta.json",
        "location_info.json",
    ),
    "config_schema": {
        "api_endpoint": {
            "type": "str",
            "default": "https://api.open-meteo.com/v1/forecast",
            "description": "Base URL for the Open-Meteo API.",
        },
        "hourly_variables": {
            "type": "list",
            "default": [
                "precipitation",
                "temperature_2m",
                "windspeed_10m",
                "winddirection_10m",
                "weathercode",
            ],
            "description": "Hourly variables to request from Open-Meteo.",
        },
        "timezone": {
            "type": "str",
            "default": "auto",
            "description": "Timezone for results ('auto', 'UTC', or an IANA name).",
        },
        "latitude": {
            "type": "float",
            "default": 0.0,
            "description": "Decimal latitude used when no upstream LAS location is available.",
        },
        "longitude": {
            "type": "float",
            "default": 0.0,
            "description": "Decimal longitude used when no upstream LAS location is available.",
        },
    },
}


# ---------------------------------------------------------------------------
# 2. Core helpers
# ---------------------------------------------------------------------------

def _validate_lat_lon(lat: float, lon: float) -> tuple[float, float]:
    """Validate that latitude and longitude are within valid ranges."""
    lat = float(lat)
    lon = float(lon)
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"Invalid latitude: {lat}")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"Invalid longitude: {lon}")
    return lat, lon


def _build_openmeteo_url(
    lat: float,
    lon: float,
    hourly_variables: list[str],
    timezone: str,
    endpoint: str = "https://api.open-meteo.com/v1/forecast",
) -> str:
    """Build the Open-Meteo API URL."""
    lat, lon = _validate_lat_lon(lat, lon)
    vars_str = ",".join(hourly_variables)
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": vars_str,
        "timezone": timezone,
    }
    query = urllib.parse.urlencode(params, doseq=True)
    return f"{endpoint}?{query}"


def _fetch_openmeteo(url: str) -> dict | None:
    """Fetch data from the Open-Meteo API."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LynceusScan"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return json.loads(resp.read().decode(charset))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        TimeoutError,
    ):
        return None


def _parse_openmeteo(data: dict, variables: list[str]) -> list[tuple[str, float]]:
    """Parse the ``hourly`` section of an Open-Meteo JSON response."""
    if not data or "hourly" not in data:
        return []
    hourly = data["hourly"]
    times = hourly.get("time", [])
    results: list[tuple[str, float]] = []
    for var in variables:
        values = hourly.get(var, [])
        for t, v in zip(times, values):
            try:
                results.append((str(t), float(v)))
            except (ValueError, TypeError):
                continue
    return results


def _write_weather_csv(csv_path: Path, rows: list[tuple[str, float]], variables: list[str]) -> None:
    """Write ``weather_data.csv`` with columns: time, then each variable."""
    if not rows:
        # Write header only
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            fh.write(",".join(["time"] + variables) + "\n")
        return

    # Group rows by variable: we assume rows are grouped by variable in the order of `variables`
    n_vars = len(variables)
    n_times = len(rows) // n_vars
    if n_times * n_vars != len(rows):
        # Fallback: write header only (should not happen with correct grouping)
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            fh.write(",".join(["time"] + variables) + "\n")
        return

    # Build the data in column-major order? We have rows grouped by variable.
    # We want to output by time.
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        # Header
        fh.write(",".join(["time"] + variables) + "\n")
        # For each time index
        for t_idx in range(n_times):
            # Time from the first variable's t_idx-th row
            time_str = rows[t_idx][0]
            row_values = [time_str]
            for v_idx in range(n_vars):
                # The v_idx-th variable's t_idx-th row is at index: v_idx * n_times + t_idx
                value_str = str(rows[v_idx * n_times + t_idx][1])
                row_values.append(value_str)
            fh.write(",".join(row_values) + "\n")


def _write_location_json(path: Path, lat: float, lon: float, tz: str) -> None:
    """Write ``location_info.json`` with the survey location."""
    payload = {"latitude": lat, "longitude": lon, "timezone": tz}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 3. Barrier task
# ---------------------------------------------------------------------------

def _stamp_weather_csv(ctx: dict, out_csv: Path) -> None:
    """Provenance sidecar for the weather CSV (best-effort)."""
    from lynceus.processing import provenance

    try:
        prov_doc = provenance.build_provenance(ctx, out_csv.name)
        provenance.write_sidecar(str(out_csv), prov_doc)
    except Exception:
        pass

def barrier_open_meteo_query(ctx: dict) -> dict:
    """Query Open-Meteo and write ``weather_data.csv``.

    The geographic location is resolved in this order:

     1. From the connected upstream LAS/LAZ header.
     2. From the ``latitude`` and ``longitude`` configuration values.

    Returns
    -------
    dict
        Payload with ``file`` (CSV path), ``file_2`` (location JSON path),
        ``kind``, ``location`` and ``node``.
    """
    # Keep None until an upstream location or configuration supplies a value.
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Try the survey location from the connected source: the LAS/LAZ
    # header center, only when its coordinates are geographic (projected
    # files need the configured latitude/longitude).
    for source in ctx.get("provenance", {}).get("sources", []) or []:
        path = source.get("file") if isinstance(source, dict) else None
        if not path or not Path(path).is_file():
            continue
        try:
            import laspy

            with laspy.open(path) as reader:
                header = reader.header
                x = (float(header.mins[0]) + float(header.maxs[0])) / 2.0
                y = (float(header.mins[1]) + float(header.maxs[1])) / 2.0
        except Exception:
            continue
        if -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0:
            latitude, longitude = x, y
            break

    # Fall back to configured coordinates when no upstream location is available.
    if latitude is None:
        latitude = ctx.get("latitude", 0.0)
        longitude = ctx.get("longitude", 0.0)

    # Build and execute the Open-Meteo request.
    api_endpoint: str = ctx.get("api_endpoint", "https://api.open-meteo.com/v1/forecast")
    hourly_vars: list = ctx.get("hourly_variables", [
        "precipitation",
        "temperature_2m",
        "windspeed_10m",
        "winddirection_10m",
        "weathercode",
    ])
    timezone: str = ctx.get("timezone", "auto")

    url = _build_openmeteo_url(
        latitude, longitude, hourly_vars, timezone, api_endpoint
    )

    data = _fetch_openmeteo(url)

    # Return an empty, valid CSV when the request fails.
    if data is None:
        out_csv = Path(ctx["session_dir"]) / "weather_data.csv"
        _write_weather_csv(out_csv, [], hourly_vars)
        out_loc = Path(ctx["session_dir"]) / "location_info.json"
        _write_location_json(out_loc, latitude if latitude is not None else 0.0,
                             longitude if longitude is not None else 0.0, timezone)
        _stamp_weather_csv(ctx, out_csv)
        return {
            "file": str(out_csv),
            "file_2": str(out_loc),
            "kind": "Weather",
            "location": {"latitude": latitude if latitude is not None else 0.0,
                         "longitude": longitude if longitude is not None else 0.0},
            "node": NODE_ID,
        }

    # Parse the response and write the CSV plus location JSON.
    rows = _parse_openmeteo(data, hourly_vars)

    out_csv = Path(ctx["session_dir"]) / "weather_data.csv"
    _write_weather_csv(out_csv, rows, hourly_vars)

    out_loc = Path(ctx["session_dir"]) / "location_info.json"
    _write_location_json(out_loc, latitude if latitude is not None else 0.0,
                         longitude if longitude is not None else 0.0, timezone)
    _stamp_weather_csv(ctx, out_csv)

    return {
        "file": str(out_csv),
        "file_2": str(out_loc),
        "kind": "Weather",
        "location": {"latitude": latitude if latitude is not None else 0.0,
                     "longitude": longitude if longitude is not None else 0.0},
        "node": NODE_ID,
    }