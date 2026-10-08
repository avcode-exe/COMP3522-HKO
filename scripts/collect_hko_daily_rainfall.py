import argparse
import csv
from datetime import datetime

import pandas as pd
from requests.exceptions import HTTPError

from common import (
    CIS_BASE,
    PROCESSED_HKO,
    PROJECT_DATA_START_DATE,
    append_to_csv,
    ensure_directories,
    get_with_retry,
    load_stations,
    log_collection_event,
    now_hkt,
    project_end_year,
    project_start_year,
    save_raw_text,
    setup_logger,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_daily_rainfall.csv"

DAILY_RAINFALL_COLUMNS = [
    "station_code",
    "date",
    "element",
    "value_raw",
    "rain_mm_1d",
    "unit",
    "data_completeness",
    "is_trace",
    "source_api",
]


def parse_cis_rainfall_csv(text, station_code, year):
    lines = text.lstrip("\ufeff").splitlines()
    header_index = None
    for index, line in enumerate(lines):
        if "Year" in line and "Month" in line and "Day" in line:
            header_index = index
            break
    rows = []
    if header_index is not None:
        reader = csv.reader(lines[header_index:])
        next(reader, None)
        for record in reader:
            if len(record) < 5:
                continue
            year_v, month_v, day_v, value_v, completeness = record[:5]
            date = None
            try:
                date = datetime(int(year_v), int(month_v), int(day_v)).date().isoformat()
            except (TypeError, ValueError):
                date = None
            value_raw = value_v.strip()
            is_trace = value_raw.lower() == "trace"
            if is_trace:
                rain_mm = 0.0
            else:
                try:
                    rain_mm = float(value_raw)
                except (TypeError, ValueError):
                    rain_mm = None
            rows.append({
                "station_code": station_code,
                "date": date,
                "element": "daily_total_rainfall",
                "value_raw": value_raw,
                "rain_mm_1d": rain_mm,
                "unit": "mm",
                "data_completeness": completeness,
                "is_trace": is_trace,
                "source_api": "hko_cis_csvfile",
            })
    return pd.DataFrame(rows, columns=DAILY_RAINFALL_COLUMNS)


def _collect_one(station_code, year, logger):
    url = "{}/{}/{}/daily_{}_RF_{}.csv".format(CIS_BASE, station_code, year, station_code, year)
    try:
        response = get_with_retry(url, max_tries=3)
    except HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            logger.info("hko_daily_rainfall: %s %d not available (404), skipping", station_code, year)
            log_collection_event("hko_daily_rainfall", "skip", 0, notes="{} {} not available (404)".format(station_code, year))
            return 0
        raise
    timestamp = now_hkt().strftime("%Y%m%d_%H%M%S")
    filename = "daily_{}_RF_{}_{}.csv".format(station_code, year, timestamp)
    raw_path = save_raw_text("hko_daily_rainfall", filename, response.text)
    df = parse_cis_rainfall_csv(response.text, station_code, year)
    append_to_csv(PROCESSED_FILE, df)
    log_collection_event("hko_daily_rainfall", "ok", len(df), raw_path, notes="{} {} RF".format(station_code, year))
    logger.info("hko_daily_rainfall: %s %d -> %d rows", station_code, year, len(df))
    return len(df)


def collect_daily_rainfall(station_codes=None, start_year=None, end_year=None, logger=None):
    logger = logger or setup_logger()
    if start_year is None:
        start_year = project_start_year()
    if end_year is None:
        end_year = project_end_year()
    if station_codes is None:
        stations = load_stations()
        station_codes = sorted(stations["temperature_station_code"].dropna().unique())
    total_rows = 0
    failed = 0
    skipped = 0
    for station_code in station_codes:
        for year in range(start_year, end_year + 1):
            try:
                rows = _collect_one(station_code, year, logger)
                if rows == 0:
                    skipped += 1
                total_rows += rows
            except Exception as exc:
                failed += 1
                logger.warning("hko_daily_rainfall failed for %s %s: %s", station_code, year, exc)
                log_collection_event("hko_daily_rainfall", "error", 0, notes="{} {}: {}".format(station_code, year, exc))
    logger.info("hko_daily_rainfall backfill complete: %d rows, %d skipped, %d failures", total_rows, skipped, failed)
    return total_rows, skipped, failed


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Backfill HKO CIS daily total rainfall (per station)")
    parser.add_argument("--stations", default=None, help="comma-separated CIS/temperature station codes; default = all selected stations")
    parser.add_argument("--start-year", type=int, default=int(PROJECT_DATA_START_DATE[:4]))
    parser.add_argument("--end-year", type=int, default=None)
    args = parser.parse_args()
    station_codes = args.stations.split(",") if args.stations else None
    collect_daily_rainfall(station_codes=station_codes, start_year=args.start_year, end_year=args.end_year, logger=logger)


if __name__ == "__main__":
    main()
