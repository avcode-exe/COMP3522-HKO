import argparse
from datetime import timedelta

import pandas as pd

from common import (
    OM_ARCHIVE_API,
    OM_HISTORICAL_FORECAST_API,
    PROCESSED_OM,
    PROJECT_DATA_END_DATE,
    PROJECT_DATA_START_DATE,
    append_parquet,
    default_end_date,
    ensure_directories,
    get_with_retry,
    load_stations,
    log_collection_event,
    now_hkt,
    save_raw_json,
    setup_logger,
)

HOURLY_VARS = "precipitation,temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m,surface_pressure"
DAILY_VARS = "precipitation_sum,temperature_2m_max,temperature_2m_min"

SOURCE_MAP = {
    "historical_forecast": {
        "url": OM_HISTORICAL_FORECAST_API,
        "category": "om_historical_forecast",
        "hourly_file": PROCESSED_OM / "om_historical_forecast_hourly.parquet",
        "daily_file": PROCESSED_OM / "om_historical_forecast_daily.parquet",
    },
    "historical_weather": {
        "url": OM_ARCHIVE_API,
        "category": "om_historical_weather",
        "hourly_file": PROCESSED_OM / "om_historical_weather_hourly.parquet",
        "daily_file": PROCESSED_OM / "om_historical_weather_daily.parquet",
    },
}


def parse_open_meteo(payload, source_api, station_id, requested_latitude=None, requested_longitude=None):
    latitude = requested_latitude if requested_latitude is not None else payload.get("latitude")
    longitude = requested_longitude if requested_longitude is not None else payload.get("longitude")
    grid_latitude = payload.get("latitude")
    grid_longitude = payload.get("longitude")
    timezone_name = payload.get("timezone")

    hourly = payload.get("hourly") or {}
    hourly_times = hourly.get("time") or []
    hourly_variables = [key for key in hourly.keys() if key != "time"]
    hourly_rows = []
    for index, time_hkt in enumerate(hourly_times):
        row = {
            "station_id": station_id,
            "latitude": latitude,
            "longitude": longitude,
            "grid_latitude": grid_latitude,
            "grid_longitude": grid_longitude,
            "timezone": timezone_name,
            "source_api": source_api,
            "issue_time_known": False,
            "time_hkt": time_hkt,
        }
        for variable in hourly_variables:
            values = hourly.get(variable) or []
            row[variable] = values[index] if index < len(values) else None
        hourly_rows.append(row)

    daily = payload.get("daily") or {}
    daily_times = daily.get("time") or []
    daily_variables = [key for key in daily.keys() if key != "time"]
    daily_rows = []
    for index, date in enumerate(daily_times):
        row = {
            "station_id": station_id,
            "latitude": latitude,
            "longitude": longitude,
            "grid_latitude": grid_latitude,
            "grid_longitude": grid_longitude,
            "timezone": timezone_name,
            "source_api": source_api,
            "issue_time_known": False,
            "date": date,
        }
        for variable in daily_variables:
            values = daily.get(variable) or []
            row[variable] = values[index] if index < len(values) else None
        daily_rows.append(row)

    return pd.DataFrame(hourly_rows), pd.DataFrame(daily_rows)


def _fetch_source(source_api, station_id, latitude, longitude, start_date, end_date, logger):
    config = SOURCE_MAP[source_api]
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": HOURLY_VARS,
        "daily": DAILY_VARS,
        "timezone": "Asia/Hong_Kong",
    }
    params_with_model = dict(params)
    params_with_model["models"] = "best_match"
    try:
        response = get_with_retry(config["url"], params=params_with_model)
    except Exception:
        logger.warning("open-meteo %s: models=best_match failed, retrying without models parameter", source_api)
        response = get_with_retry(config["url"], params=params)
    payload = response.json()
    timestamp = now_hkt().strftime("%Y%m%d_%H%M%S")
    filename = "{}_{}_{}_{}_{}.json".format(source_api, station_id, start_date, end_date, timestamp)
    save_raw_json(config["category"], payload, filename=filename)
    hourly_df, daily_df = parse_open_meteo(payload, source_api, station_id, latitude, longitude)
    hourly_output = append_parquet(config["hourly_file"], hourly_df)
    daily_output = append_parquet(config["daily_file"], daily_df)
    log_collection_event(
        config["category"],
        "ok",
        len(hourly_df) + len(daily_df),
        hourly_output,
        notes="{} {}..{} hourly={} daily={}".format(station_id, start_date, end_date, len(hourly_df), len(daily_df)),
    )
    logger.info(
        "open-meteo %s: station=%s %s..%s hourly=%d daily=%d",
        source_api, station_id, start_date, end_date, len(hourly_df), len(daily_df),
    )
    return len(hourly_df), len(daily_df)


def collect_open_meteo(station_id=None, latitude=None, longitude=None, start_date=None, end_date=None, source="both", logger=None):
    logger = logger or setup_logger()
    if station_id is not None:
        stations = load_stations()
        matches = stations[stations["station_id"] == station_id]
        if matches.empty:
            raise ValueError("station_id {} not found in meta/stations.csv".format(station_id))
        row = matches.iloc[0]
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
    if latitude is None or longitude is None:
        raise ValueError("provide station_id or both latitude and longitude")
    if start_date is None:
        if PROJECT_DATA_END_DATE:
            start_date = PROJECT_DATA_START_DATE
        else:
            start_date = (now_hkt() - timedelta(days=7)).date().isoformat()
    if end_date is None:
        end_date = default_end_date().isoformat()
    if station_id is None:
        station_id = "custom_{}_{}".format(latitude, longitude)
    sources = []
    if source in ("forecast", "both"):
        sources.append("historical_forecast")
    if source in ("archive", "both"):
        sources.append("historical_weather")
    total_hourly = 0
    total_daily = 0
    for source_api in sources:
        hourly_count, daily_count = _fetch_source(source_api, station_id, latitude, longitude, start_date, end_date, logger)
        total_hourly += hourly_count
        total_daily += daily_count
    return total_hourly, total_daily


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect Open-Meteo historical forecast / archive weather data")
    parser.add_argument("--source", choices=["forecast", "archive", "both"], default="both")
    parser.add_argument("--station", default=None, help="station_id from meta/stations.csv")
    parser.add_argument("--latitude", type=float, default=None)
    parser.add_argument("--longitude", type=float, default=None)
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD; default = 7 days ago")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD; default = yesterday")
    args = parser.parse_args()
    collect_open_meteo(
        station_id=args.station,
        latitude=args.latitude,
        longitude=args.longitude,
        start_date=args.start_date,
        end_date=args.end_date,
        source=args.source,
        logger=logger,
    )


if __name__ == "__main__":
    main()
