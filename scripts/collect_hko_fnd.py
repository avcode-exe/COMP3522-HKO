import argparse
from datetime import datetime

import pandas as pd

from common import (
    HKO_WEATHER_API,
    PROCESSED_HKO,
    append_to_csv,
    ensure_directories,
    get_with_retry,
    log_collection_event,
    now_hkt,
    save_raw_json,
    setup_logger,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_fnd_daily.csv"


def _value_unit(field):
    if isinstance(field, dict):
        return field.get("value"), field.get("unit")
    return field, None


def parse_fnd(payload, collected_at_hkt):
    forecasts = payload.get("weatherForecast") or []
    general_situation = payload.get("generalSituation")
    collected_at = collected_at_hkt.isoformat(timespec="seconds")
    snapshot_id = collected_at_hkt.strftime("%Y%m%dT%H%M%S")
    issue_date_proxy = collected_at_hkt.date().isoformat()
    rows = []
    for item in forecasts:
        forecast_date_raw = item.get("forecastDate")
        target_date = None
        if forecast_date_raw:
            try:
                target_date = datetime.strptime(str(forecast_date_raw), "%Y%m%d").date().isoformat()
            except ValueError:
                target_date = None
        lead_time_days = None
        if target_date is not None:
            lead_time_days = (
                datetime.strptime(target_date, "%Y-%m-%d").date()
                - datetime.strptime(issue_date_proxy, "%Y-%m-%d").date()
            ).days
        max_temp_c, max_temp_unit = _value_unit(item.get("forecastMaxtemp"))
        min_temp_c, min_temp_unit = _value_unit(item.get("forecastMintemp"))
        max_rh, _ = _value_unit(item.get("forecastMaxrh"))
        min_rh, _ = _value_unit(item.get("forecastMinrh"))
        rows.append({
            "snapshot_id": snapshot_id,
            "collected_at_hkt": collected_at,
            "issue_date_proxy": issue_date_proxy,
            "target_date": target_date,
            "lead_time_days": lead_time_days,
            "week": item.get("week"),
            "forecast_weather_text": item.get("forecastWeather"),
            "forecast_max_temp_c": max_temp_c,
            "forecast_max_temp_unit": max_temp_unit,
            "forecast_min_temp_c": min_temp_c,
            "forecast_min_temp_unit": min_temp_unit,
            "forecast_wind": item.get("forecastWind"),
            "forecast_max_rh_percent": max_rh,
            "forecast_min_rh_percent": min_rh,
            "forecast_icon": item.get("ForecastIcon"),
            "psr": item.get("PSR"),
            "general_situation": general_situation,
        })
    return pd.DataFrame(rows)


def collect_fnd(logger=None):
    logger = logger or setup_logger()
    params = {"dataType": "fnd", "lang": "en"}
    response = get_with_retry(HKO_WEATHER_API, params=params)
    payload = response.json()
    save_raw_json("hko_fnd", payload)
    collected_at = now_hkt()
    df = parse_fnd(payload, collected_at)
    output = append_to_csv(PROCESSED_FILE, df)
    log_collection_event("hko_fnd", "ok", len(df), output)
    logger.info("hko_fnd: collected %d forecast rows -> %s", len(df), output)
    return df


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect HKO 9-day weather forecast snapshot (dataType=fnd)")
    parser.parse_args()
    collect_fnd(logger)


if __name__ == "__main__":
    main()
