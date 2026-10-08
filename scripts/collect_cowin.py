import argparse
import io
import zipfile

import pandas as pd
import requests
import urllib3

from common import (
    COWIN_BASE,
    HEADERS,
    PROCESSED_COWIN,
    RAINSTORM_THRESHOLDS_MM,
    append_parquet,
    ensure_directories,
    log_collection_event,
    now_hkt,
    project_end_year,
    project_start_year,
    read_table,
    save_raw_bytes,
    setup_logger,
    write_csv,
    write_parquet,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROCESSED_RAIN_FILE = PROCESSED_COWIN / "cowin_hourly_rainfall.parquet"
PROCESSED_STATIONS_FILE = PROCESSED_COWIN / "cowin_stations.parquet"
LABELS_FILE = PROCESSED_COWIN / "cowin_labels_rainstorm_hourly.csv"

RAIN_COLUMNS = ["obs_time_hkt", "station_id", "rain_mm_1h", "qcscore", "year", "source"]


def _read_zip_csv(zip_bytes, name):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        if name not in archive.namelist():
            return None
        return archive.read(name)


def parse_cowin_rainfall(zip_bytes, year):
    blob = _read_zip_csv(zip_bytes, "60rf.csv")
    if blob is None:
        return pd.DataFrame(columns=RAIN_COLUMNS)
    df = pd.read_csv(io.BytesIO(blob), encoding="utf-8-sig")
    df = df.rename(columns={df.columns[2]: "rain_mm_1h"})
    df["station_id"] = df["station_id"].astype(str)
    df["rain_mm_1h"] = pd.to_numeric(df["rain_mm_1h"], errors="coerce")
    obs = pd.to_datetime(df["obstime"], errors="coerce")
    df["obs_time_hkt"] = obs.dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    df["year"] = year
    df["source"] = "hk_cowin"
    return df[RAIN_COLUMNS]


def parse_cowin_stations(zip_bytes, year):
    blob = _read_zip_csv(zip_bytes, "available.csv")
    if blob is None:
        return pd.DataFrame()
    df = pd.read_csv(io.BytesIO(blob), encoding="utf-8-sig")
    df = df.rename(columns={df.columns[0]: "station_id"})
    df["station_id"] = df["station_id"].astype(str)
    df["year"] = year
    df["source"] = "hk_cowin"
    return df


def download_year(year, logger):
    url = "{}/{}.zip".format(COWIN_BASE, year)
    response = requests.get(url, headers=HEADERS, timeout=300, verify=False)
    if response.status_code == 404:
        logger.info("cowin: %s not available (404)", year)
        log_collection_event("cowin", "skip", 0, notes="{} zip not available (404)".format(year))
        return None
    response.raise_for_status()
    raw_path = save_raw_bytes("cowin", "{}.zip".format(year), response.content)
    return raw_path, response.content


def build_cowin_station_dimension(station_frames, rain):
    stations = pd.concat(station_frames, ignore_index=True) if station_frames else pd.DataFrame()
    if not stations.empty:
        stations = stations.drop_duplicates(subset=["station_id", "year"]).sort_values(["station_id", "year"])
    if rain is not None and not rain.empty:
        known = set(stations["station_id"].astype(str)) if not stations.empty else set()
        missing = sorted(set(rain["station_id"].astype(str)) - known)
        if missing:
            extra = pd.DataFrame([{
                "station_id": sid,
                "station_name": None,
                "latitude": None,
                "longitude": None,
                "elevation": None,
                "year": None,
                "source": "hk_cowin",
                "metadata_missing": True,
            } for sid in missing])
            stations = pd.concat([stations, extra], ignore_index=True)
    return stations


def collect_cowin(start_year=None, end_year=None, logger=None):
    logger = logger or setup_logger()
    if start_year is None:
        start_year = project_start_year()
    if end_year is None:
        end_year = project_end_year()
    station_frames = []
    rain_frames = []
    total = 0
    for year in range(start_year, end_year + 1):
        try:
            result = download_year(year, logger)
        except Exception as exc:
            logger.warning("cowin %s download failed: %s", year, exc)
            log_collection_event("cowin", "error", 0, notes="{}: {}".format(year, exc))
            continue
        if result is None:
            continue
        raw_path, content = result
        rain_df = parse_cowin_rainfall(content, year)
        append_parquet(PROCESSED_RAIN_FILE, rain_df)
        rain_frames.append(rain_df)
        station_df = parse_cowin_stations(content, year)
        station_frames.append(station_df)
        total += len(rain_df)
        log_collection_event("cowin", "ok", len(rain_df), raw_path, notes="{} hourly rainfall; {} stations".format(year, len(station_df)))
        logger.info("cowin: %s -> %d hourly rainfall rows, %d station rows", year, len(rain_df), len(station_df))
    all_rain = pd.concat(rain_frames, ignore_index=True) if rain_frames else pd.DataFrame()
    stations = build_cowin_station_dimension(station_frames, all_rain)
    if not stations.empty:
        write_parquet(PROCESSED_STATIONS_FILE, stations)
    logger.info("cowin backfill complete: %d hourly rainfall rows", total)
    return total


def build_cowin_labels(logger=None):
    logger = logger or setup_logger()
    if not PROCESSED_RAIN_FILE.exists():
        logger.warning("cowin labels: %s not found", PROCESSED_RAIN_FILE)
        return 0
    df = read_table(PROCESSED_RAIN_FILE)
    df = df[(df["qcscore"] == "G") & df["rain_mm_1h"].notna()]
    rows = []
    for obs_time, group in df.groupby("obs_time_hkt"):
        amber = sorted(group.loc[group["rain_mm_1h"] >= RAINSTORM_THRESHOLDS_MM["amber"], "station_id"].tolist())
        red = sorted(group.loc[group["rain_mm_1h"] >= RAINSTORM_THRESHOLDS_MM["red"], "station_id"].tolist())
        black = sorted(group.loc[group["rain_mm_1h"] >= RAINSTORM_THRESHOLDS_MM["black"], "station_id"].tolist())
        rows.append({
            "hour_end_hkt": obs_time,
            "any_station_amber": bool(amber),
            "any_station_red": bool(red),
            "any_station_black": bool(black),
            "n_amber": len(amber),
            "n_red": len(red),
            "n_black": len(black),
            "label_basis": "any_cowin_station_qc_G",
        })
    labels = pd.DataFrame(rows)
    if not labels.empty:
        labels = labels.sort_values("hour_end_hkt")
    write_csv(LABELS_FILE, labels)
    logger.info("cowin labels: %d hourly rows (amber=%d red=%d black=%d)", len(labels), int(labels["any_station_amber"].sum()) if len(labels) else 0, int(labels["any_station_red"].sum()) if len(labels) else 0, int(labels["any_station_black"].sum()) if len(labels) else 0)
    return len(labels)


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Download HKU CoWIN QC hourly AWS data (hourly rainfall) for Task 3(a)")
    parser.add_argument("--start-year", type=int, default=project_start_year())
    parser.add_argument("--end-year", type=int, default=project_end_year())
    args = parser.parse_args()
    collect_cowin(start_year=args.start_year, end_year=args.end_year, logger=logger)
    build_cowin_labels(logger)


if __name__ == "__main__":
    main()
