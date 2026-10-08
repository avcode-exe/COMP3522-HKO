import argparse
import csv
from datetime import datetime

import pandas as pd

from common import (
    HKO_OPENDATA_API,
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
    save_raw_json,
    save_raw_text,
    setup_logger,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_daily_temp.csv"

DAILY_TEMP_COLUMNS = [
    "station_code",
    "date",
    "data_type",
    "value_c",
    "unit",
    "data_completeness",
    "station_title_en",
    "source_api",
]


def parse_daily_temp_json(payload, data_type, station_code, year):
    titles = payload.get("type") or []
    en_title = titles[1] if len(titles) > 1 else (titles[0] if titles else None)
    rows = []
    for record in payload.get("data") or []:
        values = list(record) + [None] * 5
        year_v, month_v, day_v, value_v, completeness = values[:5]
        date = None
        try:
            date = datetime(int(year_v), int(month_v), int(day_v)).date().isoformat()
        except (TypeError, ValueError):
            date = None
        value = None
        try:
            value = float(value_v)
        except (TypeError, ValueError):
            value = None
        rows.append({
            "station_code": station_code,
            "date": date,
            "data_type": data_type,
            "value_c": value,
            "unit": "C",
            "data_completeness": completeness,
            "station_title_en": en_title,
            "source_api": "hko_opendata_api",
        })
    return pd.DataFrame(rows, columns=DAILY_TEMP_COLUMNS)


def parse_daily_temp_csv(text, data_type, station_code, year):
    lines = text.lstrip("﻿").splitlines()
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
            value = None
            try:
                value = float(value_v)
            except (TypeError, ValueError):
                value = None
            rows.append({
                "station_code": station_code,
                "date": date,
                "data_type": data_type,
                "value_c": value,
                "unit": "C",
                "data_completeness": completeness,
                "station_title_en": None,
                "source_api": "hko_opendata_api",
            })
    return pd.DataFrame(rows, columns=DAILY_TEMP_COLUMNS)


def _collect_one(data_type, station_code, year, rformat, logger):
    params = {
        "dataType": data_type,
        "station": station_code,
        "year": year,
        "rformat": rformat,
        "lang": "en",
    }
    response = get_with_retry(HKO_OPENDATA_API, params=params)
    timestamp = now_hkt().strftime("%Y%m%d_%H%M%S")
    filename = "{}_{}_{}_{}".format(data_type, station_code, year, timestamp)
    if rformat == "json":
        if not response.text.lstrip().startswith("{"):
            raise ValueError("unexpected non-JSON response for {} {} {}".format(data_type, station_code, year))
        payload = response.json()
        raw_path = save_raw_json("hko_daily_temp", payload, filename=filename + ".json")
        df = parse_daily_temp_json(payload, data_type, station_code, year)
    else:
        raw_path = save_raw_text("hko_daily_temp", filename + ".csv", response.text)
        df = parse_daily_temp_csv(response.text, data_type, station_code, year)
    append_to_csv(PROCESSED_FILE, df)
    log_collection_event("hko_daily_temp", "ok", len(df), raw_path, notes="{} {} {}".format(data_type, station_code, year))
    logger.info("hko_daily_temp: %s %s %d -> %d rows", data_type, station_code, year, len(df))
    return len(df)


def collect_daily_temperature(data_types=("CLMTEMP", "CLMMAXT", "CLMMINT"), station_codes=None, start_year=None, end_year=None, rformat="json", logger=None):
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
    for station_code in station_codes:
        for year in range(start_year, end_year + 1):
            for data_type in data_types:
                try:
                    total_rows += _collect_one(data_type, station_code, year, rformat, logger)
                except Exception as exc:
                    failed += 1
                    logger.warning("hko_daily_temp failed for %s %s %s: %s", data_type, station_code, year, exc)
                    log_collection_event("hko_daily_temp", "error", 0, notes="{} {} {}: {}".format(data_type, station_code, year, exc))
    logger.info("hko_daily_temp backfill complete: %d rows, %d failures", total_rows, failed)
    return total_rows, failed


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Backfill HKO daily temperature (CLMTEMP/CLMMAXT/CLMMINT)")
    parser.add_argument("--data-type", choices=["CLMTEMP", "CLMMAXT", "CLMMINT", "ALL"], default="ALL")
    parser.add_argument("--stations", default=None, help="comma-separated temperature station codes; default = all selected stations in meta/stations.csv")
    parser.add_argument("--start-year", type=int, default=None, help="default = {} (project data start)".format(PROJECT_DATA_START_DATE[:4]))
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--rformat", choices=["json", "csv"], default="json")
    args = parser.parse_args()
    data_types = ("CLMTEMP", "CLMMAXT", "CLMMINT") if args.data_type == "ALL" else (args.data_type,)
    station_codes = args.stations.split(",") if args.stations else None
    collect_daily_temperature(data_types=data_types, station_codes=station_codes, start_year=args.start_year, end_year=args.end_year, rformat=args.rformat, logger=logger)


if __name__ == "__main__":
    main()
