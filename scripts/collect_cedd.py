import argparse
import io

import pandas as pd
import requests
import urllib3

from common import (
    CEDD_RG_BASE,
    HEADERS,
    PROCESSED_CEDD,
    PROJECT_DATA_START_DATE,
    ensure_directories,
    log_collection_event,
    project_end_year,
    project_start_year,
    save_raw_bytes,
    setup_logger,
    write_csv,
    write_parquet,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROCESSED_RAIN_FILE = PROCESSED_CEDD / "cedd_daily_rainfall.parquet"
PROCESSED_STATIONS_FILE = PROCESSED_CEDD / "cedd_stations.csv"

RAIN_COLUMNS = [
    "date",
    "station_id",
    "station_name",
    "easting",
    "northing",
    "value_raw",
    "rain_mm_1d",
    "is_trace",
    "is_incomplete",
    "source",
]


def parse_cedd_rainfall(text, year):
    df = pd.read_csv(io.StringIO(text))
    df = df.rename(columns={
        "Date": "date",
        "Raingauge_No": "station_id",
        "Location": "station_name",
        "Easting": "easting",
        "Northing": "northing",
        "Rainfall": "value_raw",
    })
    df["station_id"] = df["station_id"].astype(str).str.strip()
    df["station_name"] = df["station_name"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    value_raw = df["value_raw"].astype(str).str.strip()
    df["value_raw"] = value_raw
    df["is_incomplete"] = value_raw.str.endswith("#")
    cleaned = value_raw.str.rstrip("#").str.strip()
    df["is_trace"] = cleaned.str.lower().eq("trace")
    numeric = pd.to_numeric(cleaned, errors="coerce")
    df["rain_mm_1d"] = numeric.where(~df["is_trace"], 0.0)
    df["source"] = "cedd_geo"
    df = df[df["date"].notna()]
    return df[RAIN_COLUMNS]


def download_year(year, logger):
    url = "{}/{}.csv".format(CEDD_RG_BASE, year)
    response = requests.get(url, headers=HEADERS, timeout=180, verify=False)
    if response.status_code == 404:
        logger.info("cedd: %s not available (404)", year)
        log_collection_event("cedd", "skip", 0, notes="{} not available (404)".format(year))
        return None
    response.raise_for_status()
    raw_path = save_raw_bytes("cedd", "{}.csv".format(year), response.content)
    return raw_path, response.content


def collect_cedd(start_year=None, end_year=None, logger=None):
    logger = logger or setup_logger()
    if start_year is None:
        start_year = project_start_year()
    if end_year is None:
        end_year = project_end_year()
    frames = []
    station_frames = []
    total = 0
    for year in range(start_year, end_year + 1):
        try:
            result = download_year(year, logger)
        except Exception as exc:
            logger.warning("cedd %s download failed: %s", year, exc)
            log_collection_event("cedd", "error", 0, notes="{}: {}".format(year, exc))
            continue
        if result is None:
            continue
        raw_path, content = result
        text = content.decode("utf-8-sig", errors="replace")
        df = parse_cedd_rainfall(text, year)
        frames.append(df)
        station_frames.append(df[["station_id", "station_name", "easting", "northing"]].drop_duplicates())
        total += len(df)
        log_collection_event("cedd", "ok", len(df), raw_path, notes="{} daily rainfall; {} gauges".format(year, df["station_id"].nunique()))
        logger.info("cedd: %s -> %d rows, %d gauges", year, len(df), df["station_id"].nunique())
    if frames:
        rain = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["date", "station_id"])
        rain = rain[rain["date"] >= PROJECT_DATA_START_DATE]
        rain = rain.sort_values(["date", "station_id"])
        write_parquet(PROCESSED_RAIN_FILE, rain)
    if station_frames:
        stations = pd.concat(station_frames, ignore_index=True).drop_duplicates(subset=["station_id"]).sort_values("station_id")
        stations["source"] = "cedd_geo"
        write_csv(PROCESSED_STATIONS_FILE, stations)
    logger.info("cedd backfill complete: %d daily rainfall rows", total)
    return total


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Download CEDD GEO raingauge yearly daily-rainfall CSVs")
    parser.add_argument("--start-year", type=int, default=int(PROJECT_DATA_START_DATE[:4]))
    parser.add_argument("--end-year", type=int, default=None)
    args = parser.parse_args()
    collect_cedd(start_year=args.start_year, end_year=args.end_year, logger=logger)


if __name__ == "__main__":
    main()
