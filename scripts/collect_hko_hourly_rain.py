import argparse

import pandas as pd

from common import (
    HKO_HOURLY_RAIN_API,
    PROCESSED_HKO,
    RAINSTORM_THRESHOLDS_MM,
    append_to_csv,
    ensure_directories,
    get_with_retry,
    log_collection_event,
    save_raw_json,
    setup_logger,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_hourly_rain.csv"


def parse_hourly_rain(payload):
    obs_time = payload.get("obsTime")
    rows = []
    for item in payload.get("hourlyRainfall") or []:
        value_raw = item.get("value")
        rain_mm = None
        maintenance_or_missing = False
        if value_raw == "M":
            maintenance_or_missing = True
        else:
            try:
                rain_mm = float(value_raw)
            except (TypeError, ValueError):
                rain_mm = None
        rows.append({
            "obs_time_hkt": obs_time,
            "station_id": item.get("automaticWeatherStationID"),
            "station_name": item.get("automaticWeatherStation"),
            "value_raw": value_raw,
            "rain_mm_1h": rain_mm,
            "unit": item.get("unit"),
            "maintenance_or_missing": maintenance_or_missing,
            "station_amber_flag": rain_mm is not None and rain_mm >= RAINSTORM_THRESHOLDS_MM["amber"],
            "station_red_flag": rain_mm is not None and rain_mm >= RAINSTORM_THRESHOLDS_MM["red"],
            "station_black_flag": rain_mm is not None and rain_mm >= RAINSTORM_THRESHOLDS_MM["black"],
        })
    return pd.DataFrame(rows)


def collect_hourly_rain(logger=None):
    logger = logger or setup_logger()
    params = {"lang": "en"}
    response = get_with_retry(HKO_HOURLY_RAIN_API, params=params)
    payload = response.json()
    save_raw_json("hko_hourly_rain", payload)
    df = parse_hourly_rain(payload)
    output = append_to_csv(PROCESSED_FILE, df)
    log_collection_event("hko_hourly_rain", "ok", len(df), output, notes="obs_time={}".format(payload.get("obsTime")))
    logger.info("hko_hourly_rain: collected %d station rows (obs_time=%s) -> %s", len(df), payload.get("obsTime"), output)
    return df


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect HKO AWS hourly rainfall (latest snapshot)")
    parser.parse_args()
    collect_hourly_rain(logger)


if __name__ == "__main__":
    main()
