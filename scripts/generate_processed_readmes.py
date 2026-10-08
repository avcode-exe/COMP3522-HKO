import argparse
import re
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

from common import PROCESSED_ROOT, now_hkt, setup_logger

COLUMN_GLOSSARY = {
    "snapshot_id": "unique forecast snapshot ID (collection time, YYYYMMDDTHHMMSS)",
    "collected_at_hkt": "when the API was called (HKT)",
    "issue_date_proxy": "snapshot collection date used as the forecast issue-date proxy",
    "target_date": "calendar day being forecast",
    "lead_time_days": "target_date minus issue_date_proxy",
    "week": "day of week at the target date",
    "forecast_weather_text": "raw HKO forecast description",
    "forecast_max_temp_c": "forecast daily maximum temperature (C)",
    "forecast_max_temp_unit": "unit for the maximum temperature",
    "forecast_min_temp_c": "forecast daily minimum temperature (C)",
    "forecast_min_temp_unit": "unit for the minimum temperature",
    "forecast_wind": "forecast wind description",
    "forecast_max_rh_percent": "forecast maximum relative humidity (%)",
    "forecast_min_rh_percent": "forecast minimum relative humidity (%)",
    "forecast_icon": "HKO forecast icon code",
    "psr": "Probability of Significant Rain (High/Medium/Low) - categorical, not mm",
    "general_situation": "HKO general situation text for the forecast",
    "obs_time_hkt": "observation time (end of the 1-hour rainfall window)",
    "station_id": "AWS/CoWIN station identifier",
    "station_code": "climate/temperature station code",
    "station_name": "station name",
    "value_raw": "original value exactly as received (may be 'M' or 'Trace')",
    "rain_mm_1h": "rainfall in the past hour (mm)",
    "rain_mm_1d": "daily total rainfall (mm)",
    "unit": "unit of the value column",
    "maintenance_or_missing": "TRUE when the raw value is 'M' (maintenance/missing)",
    "station_amber_flag": "TRUE if hourly rainfall >= 30 mm",
    "station_red_flag": "TRUE if hourly rainfall >= 50 mm",
    "station_black_flag": "TRUE if hourly rainfall >= 70 mm",
    "warning_key": "top-level warning key (e.g. WRAIN, WFIRE)",
    "name": "warning name",
    "code": "warning code (e.g. WRAINR, WFIRER)",
    "type": "warning colour/subtype (e.g. Red, Amber, Black)",
    "action_code": "warning action (ISSUE / CANCEL / UPDATE)",
    "issue_time": "warning issue time (ISO 8601 +08:00)",
    "update_time": "warning update time (ISO 8601 +08:00)",
    "expire_time": "warning expiry time (may be missing)",
    "warning_statement_code": "warning statement code (e.g. WRAIN)",
    "subtype": "warning subtype (e.g. WRAINA / WRAINR / WRAINB)",
    "contents_text": "joined warning text lines",
    "warning_id": "unique rainstorm warning event ID",
    "episode_id": "linked escalation episode ID (Amber -> Red -> Black chain)",
    "episode_warning_index": "position within the escalation episode",
    "warning_color": "Amber / Red / Black",
    "warning_code": "WRAINA / WRAINR / WRAINB",
    "issue_datetime_hkt": "official issue time (ISO 8601 +08:00)",
    "cancel_datetime_hkt": "official cancel time (ISO 8601 +08:00)",
    "duration_minutes": "cancel minus issue, in minutes",
    "year": "year",
    "month": "month",
    "source_url": "provenance URL of the source dataset",
    "raw_text": "original source record line",
    "notes": "processing notes (e.g. hour-24 normalization)",
    "element": "measured element (e.g. temperature, max_temp_c)",
    "place": "district or place name",
    "value": "observation value",
    "record_time": "observation record time",
    "extra": "extra field (e.g. maintenance flag or UV description)",
    "icon": "weather icon code",
    "icon_update_time": "icon update time",
    "rainfall_from_00_to_12_mm": "rainfall since midnight to noon (mm)",
    "mintemp_from_00_to_09_c": "minimum temperature from midnight to 9am (C)",
    "rainfall_last_month_mm": "rainfall for the previous month (mm)",
    "rainfall_january_to_last_month_mm": "accumulated rainfall since 1 January (mm)",
    "warning_message": "active warning message text",
    "tc_message": "tropical cyclone message text",
    "date": "observation date",
    "data_type": "climate data type (CLMTEMP / CLMMAXT / CLMMINT)",
    "value_c": "temperature value (C)",
    "data_completeness": "completeness flag: C = complete, # = incomplete",
    "station_title_en": "English dataset title from the API",
    "source_api": "source API / dataset identifier",
    "mean_temp_c": "daily mean temperature (C)",
    "max_temp_c": "daily maximum temperature (C)",
    "min_temp_c": "daily minimum temperature (C)",
    "is_trace": "TRUE when the raw rainfall value is 'Trace' (< 0.05 mm)",
    "report_date": "reported day (YYYYMMDD)",
    "bulletin_date": "bulletin issue date (YYYYMMDD)",
    "bulletin_time": "bulletin issue time (HHMM)",
    "location": "location name",
    "qcscore": "CoWIN quality-control flag (G = good; M/F/X = flagged)",
    "source": "source dataset identifier",
    "latitude": "requested station latitude",
    "longitude": "requested station longitude",
    "grid_latitude": "model grid-cell latitude returned by the API",
    "grid_longitude": "model grid-cell longitude returned by the API",
    "timezone": "timezone of the timestamps",
    "issue_time_known": "whether the forecast issue/run time is known (FALSE for Open-Meteo)",
    "time_hkt": "local time (HKT)",
    "precipitation": "hourly precipitation (mm)",
    "temperature_2m": "air temperature at 2 m (C)",
    "relative_humidity_2m": "relative humidity at 2 m (%)",
    "cloud_cover": "cloud cover (%)",
    "wind_speed_10m": "wind speed at 10 m (km/h)",
    "surface_pressure": "surface pressure (hPa)",
    "precipitation_sum": "daily precipitation sum (mm)",
    "temperature_2m_max": "daily maximum air temperature at 2 m (C)",
    "temperature_2m_min": "daily minimum air temperature at 2 m (C)",
    "hour_end_hkt": "hour-ending timestamp (HKT)",
    "any_station_amber": "TRUE if any CoWIN station (QC=G) reached 30 mm/h",
    "any_station_red": "TRUE if any CoWIN station (QC=G) reached 50 mm/h",
    "any_station_black": "TRUE if any CoWIN station (QC=G) reached 70 mm/h",
    "any_selected_station_amber": "TRUE if any selected HKO station reached 30 mm/h",
    "any_selected_station_red": "TRUE if any selected HKO station reached 50 mm/h",
    "any_selected_station_black": "TRUE if any selected HKO station reached 70 mm/h",
    "n_amber": "number of stations reaching 30 mm/h",
    "n_red": "number of stations reaching 50 mm/h",
    "n_black": "number of stations reaching 70 mm/h",
    "stations_triggering_amber": "comma-separated station IDs reaching 30 mm/h",
    "stations_triggering_red": "comma-separated station IDs reaching 50 mm/h",
    "stations_triggering_black": "comma-separated station IDs reaching 70 mm/h",
    "label_basis": "rule used to build the label (e.g. any_cowin_station_qc_G)",
    "anchor_time_hkt": "prediction anchor time (hourly, HKT)",
    "label_warning_3h": "1 if a rainstorm warning is issued within the next 3 hours",
    "label_warning_6h": "1 if a rainstorm warning is issued within the next 6 hours",
    "label_warning_12h": "1 if a rainstorm warning is issued within the next 12 hours",
    "next_warning_issue_hkt": "issue time of the next warning after the anchor (reference only; leaks the future)",
    "hours_to_next_warning": "hours from the anchor to the next warning (reference only; leaks the future)",
    "next_warning_color": "Amber/Red/Black of the next warning (reference only; leaks the future)",
    "region": "geographic region (Hong Kong Island / Kowloon / New Territories / Lantau)",
    "temperature_station_code": "HKO daily-temperature station code",
    "rainfall_station_id": "HKO AWS rainfall station ID",
    "station_type": "station type (AWS / climate station)",
    "selected": "TRUE if the station is selected for collection",
    "selected_for_task1": "TRUE if used for Task 1 (forecast reliability/validity)",
    "selected_for_task3": "TRUE if used for Task 3 (rainstorm labels/warnings)",
    "coordinates_verified": "TRUE once coordinates are verified against the official dataset",
    "metadata_missing": "TRUE when a rainfall station has no row in CoWIN available.csv",
    "elevation": "station elevation (m)",
}

TIME_PRIORITY = [
    "time_hkt", "obs_time_hkt", "hour_end_hkt", "collected_at_hkt",
    "issue_datetime_hkt", "date", "report_date", "issue_date_proxy", "target_date",
]
STATION_COLUMNS = ["station_id", "station_code", "station_name", "location"]
SOURCE_COLUMNS = ["source", "source_api"]


def _load(path):
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _parse_times(series):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    tz = getattr(parsed.dt, "tz", None)
    if tz is not None:
        parsed = parsed.dt.tz_localize(None)
    return parsed


def _short(text, limit=60):
    text = str(text).replace("\n", " ").replace("|", "\\|").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _num(value):
    if pd.isna(value):
        return "NA"
    if float(value).is_integer():
        return str(int(value))
    return "{:.4g}".format(value)


def _looks_datetime(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if len(series) == 0 or not pd.api.types.is_string_dtype(series):
        return False
    sample = series.dropna().astype(str).head(50)
    if sample.empty:
        return False
    if sample.str.fullmatch(r"\d{1,6}").all():
        return False
    return _parse_times(sample).notna().mean() > 0.8


def _infer_desc(name):
    low = name.lower()
    if low.endswith("_hkt") or low in ("date", "time"):
        return "timestamp (HKT)"
    if low.endswith("_c"):
        return "temperature (C)"
    if low.endswith("_mm"):
        return "precipitation/amount (mm)"
    if low.endswith("_percent") or low.endswith("_rh"):
        return "relative humidity (%)"
    if low.startswith("any_") or low.endswith("_flag") or low in ("is_trace", "selected"):
        return "boolean indicator"
    if low.endswith("_id") or low.endswith("_code") or low in ("name", "type", "code"):
        return "identifier / label"
    return "field"


def describe_column(name, series):
    n = len(series)
    nonnull = int(series.notna().sum())
    nunique = int(series.nunique(dropna=True)) if n else 0
    desc = COLUMN_GLOSSARY.get(name) or _infer_desc(name)
    if n == 0 or nonnull == 0:
        info = "no data"
    elif pd.api.types.is_bool_dtype(series):
        info = "True / False"
    elif pd.api.types.is_numeric_dtype(series):
        info = "range {} .. {}".format(_num(series.min()), _num(series.max()))
    elif _looks_datetime(series):
        parsed = _parse_times(series)
        info = "{} .. {}".format(_short(parsed.min(), 30), _short(parsed.max(), 30))
    elif nunique <= 20:
        values = list(series.dropna().astype(str).unique())[:20]
        info = "values: {}".format(", ".join(_short(v, 28) for v in values))
    else:
        info = "{} distinct; e.g. \"{}\"".format(nunique, _short(series.dropna().iloc[0], 40))
    pct = 100.0 * nonnull / n if n else 0.0
    return {
        "name": name,
        "dtype": str(series.dtype),
        "nonnull": "{}/{} ({:.0f}%)".format(nonnull, n, pct),
        "info": info,
        "desc": desc,
    }


def _fingerprint(df):
    tags = []
    cols = " ".join(df.columns).lower()
    for key, label in [
        ("rain", "rainfall"), ("temp", "temperature"), ("humidity", "humidity"),
        ("rh", "humidity"), ("wind", "wind"), ("pressure", "pressure"),
        ("cloud", "cloud cover"), ("uv", "UV"), ("radiation", "radiation"),
        ("sunshine", "sunshine"), ("forecast", "forecast"), ("warning", "warnings"),
        ("label", "labels"), ("station", "station metadata"),
    ]:
        if key in cols:
            tags.append(label)
    tags = list(dict.fromkeys(tags))
    granularity = ""
    if any(c in df.columns for c in ("time_hkt", "obs_time_hkt", "hour_end_hkt", "anchor_time_hkt")):
        granularity = "hourly"
    elif "date" in df.columns or "report_date" in df.columns:
        granularity = "daily"
    return tags, granularity


def infer_purpose(path, df):
    tags, granularity = _fingerprint(df)
    name = path.stem.lower()
    hints = [
        ("cowin_hourly_rainfall", "HKU CoWIN QC hourly rainfall"),
        ("cowin_labels", "CoWIN-based rainstorm-hour labels"),
        ("cowin_stations", "HKU CoWIN station metadata"),
        ("om_historical", "Open-Meteo gridded weather"),
        ("rainstorm_warning_history", "HKO historical rainstorm warning issue/cancel times"),
        ("warning_windows", "Task 3(b) rainstorm warning prediction-window labels (3/6/12h)"),
        ("hourly_rain", "HKO AWS hourly rainfall snapshots"),
        ("daily_temp", "HKO daily temperature"),
        ("daily_rainfall", "HKO CIS daily total rainfall"),
        ("daily_weather", "HKO daily weather & radiation report (RYES)"),
        ("warning_current", "current weather warning snapshots"),
        ("warning_info", "current weather warning text"),
        ("rhrread", "current weather report (rhrread)"),
        ("labels_rainstorm", "territory-level rainstorm-hour labels (HKO basis)"),
        ("stations", "HKO station metadata"),
        ("fnd", "HKO 9-day weather forecast snapshots"),
    ]
    for key, label in hints:
        if key in name:
            return label
    parts = []
    if granularity:
        parts.append(granularity)
    if tags:
        parts.append("observations of " + ", ".join(tags))
    return " ".join(parts) if parts else "derived data table"


def describe_file(path):
    df = _load(path)
    fmt = "parquet" if path.suffix == ".parquet" else "CSV"
    size_kb = path.stat().st_size / 1024
    lines = []
    lines.append("### `{}`".format(path.name))
    lines.append("")
    lines.append("- **Format:** {} · **Rows:** {:,} · **Columns:** {} · **Size:** {:.1f} KB".format(
        fmt, len(df), len(df.columns), size_kb))
    lines.append("- **Purpose:** {}".format(infer_purpose(path, df)))

    for sc in SOURCE_COLUMNS:
        if sc in df.columns and len(df):
            vals = sorted(df[sc].dropna().astype(str).unique())
            lines.append("- **{}:** {}".format("Source" if sc == "source" else "Source API", ", ".join(vals[:8])))
            break

    time_col = next((c for c in TIME_PRIORITY if c in df.columns and _looks_datetime(df[c])), None)
    if time_col and len(df):
        parsed = _parse_times(df[time_col])
        lines.append("- **Time coverage (`{}`):** {} .. {}".format(time_col, parsed.min(), parsed.max()))

    for stc in STATION_COLUMNS:
        if stc in df.columns and len(df):
            lines.append("- **{}:** {} distinct".format(stc, df[stc].nunique(dropna=True)))
            break

    lines.append("")
    lines.append("| Column | Type | Non-null | Range / values | Description |")
    lines.append("|---|---|---|---|---|")
    for col in df.columns:
        info = describe_column(col, df[col])
        lines.append("| `{}` | {} | {} | {} | {} |".format(
            info["name"], info["dtype"], info["nonnull"], _short(info["info"], 70), info["desc"]))
    lines.append("")
    return "\n".join(lines)


def generate_directory_readme(directory, generated_at):
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in (".csv", ".parquet"))
    sections = [describe_file(p) for p in files]

    total_rows = 0
    sizes = 0
    time_min = None
    time_max = None
    sources = set()
    for p in files:
        try:
            df = _load(p)
        except Exception:
            continue
        total_rows += len(df)
        sizes += p.stat().st_size
        for sc in SOURCE_COLUMNS:
            if sc in df.columns:
                sources.update(df[sc].dropna().astype(str).unique())
                break
        for c in TIME_PRIORITY:
            if c in df.columns and _looks_datetime(df[c]) and len(df):
                parsed = _parse_times(df[c])
                lo, hi = parsed.min(), parsed.max()
                time_min = lo if time_min is None or lo < time_min else time_min
                time_max = hi if time_max is None or hi > time_max else time_max
                break

    out = []
    out.append("# `{}` — auto-generated data guide".format(directory.name))
    out.append("")
    out.append("> Auto-generated on {} by `scripts/generate_processed_readmes.py` from the files actually present in this directory. Do not edit by hand — re-run the generator (or `scripts/flatten_hko_data.py`) after the data changes.".format(generated_at))
    out.append("")
    out.append("## Overview")
    out.append("")
    out.append("- **Files:** {}".format(len(files)))
    out.append("- **Total rows:** {:,}".format(total_rows))
    out.append("- **Total size:** {:.1f} KB".format(sizes / 1024))
    if sources:
        out.append("- **Sources:** {}".format(", ".join(sorted(sources))))
    if time_min is not None:
        out.append("- **Time coverage:** {} .. {}".format(time_min, time_max))
    out.append("- **Join keys:** use `station_id` / `station_code` to join to the station metadata table in this or the `hko` section; use the time column shown per file for temporal joins.")
    out.append("")
    out.append("## Files")
    out.append("")
    out.extend(sections)
    text = "\n".join(out).rstrip() + "\n"
    target = directory / "README.md"
    target.write_text(text, encoding="utf-8")
    return target


def generate_index(root, section_readmes, generated_at):
    out = []
    out.append("# `processed/` — auto-generated index")
    out.append("")
    out.append("> Auto-generated on {} by `scripts/generate_processed_readmes.py`. Each section below has its own dynamically generated README.".format(generated_at))
    out.append("")
    out.append("## Sections")
    out.append("")
    for directory, readme in section_readmes:
        files = [p for p in directory.iterdir() if p.is_file() and p.suffix in (".csv", ".parquet")]
        rows = 0
        for p in files:
            try:
                rows += len(_load(p))
            except Exception:
                pass
        out.append("- **[{}]({}/README.md)** — {} files, {:,} rows".format(directory.name, directory.name, len(files), rows))
    out.append("")
    out.append("## Getting the data")
    out.append("")
    out.append("`processed/` is self-contained: no file references `raw/`. The data folders are git-ignored (too large for GitHub); regenerate them with `scripts/flatten_hko_data.py`, then this documentation with `scripts/generate_processed_readmes.py`.")
    text = "\n".join(out).rstrip() + "\n"
    target = root / "README.md"
    target.write_text(text, encoding="utf-8")
    return target


def generate_all(root=None, logger=None):
    root = Path(root) if root else PROCESSED_ROOT
    logger = logger or setup_logger(name="readme_gen", log_file="readme_gen.log")
    generated_at = now_hkt().isoformat(timespec="seconds")
    section_readmes = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if not any(f.suffix in (".csv", ".parquet") for f in directory.iterdir() if f.is_file()):
            continue
        readme = generate_directory_readme(directory, generated_at)
        section_readmes.append((directory, readme))
        logger.info("generated %s", readme)
    index = generate_index(root, section_readmes, generated_at)
    logger.info("generated %s", index)
    return index


def main():
    parser = argparse.ArgumentParser(description="Auto-generate README documentation for each processed/ section")
    parser.add_argument("--root", default=None, help="processed root (default: processed/)")
    args = parser.parse_args()
    generate_all(args.root)


if __name__ == "__main__":
    main()
