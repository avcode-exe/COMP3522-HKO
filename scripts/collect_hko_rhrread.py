import argparse

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

RHRREAD_FILE = PROCESSED_HKO / "hko_rhrread.csv"
RHRREAD_SUMMARY_FILE = PROCESSED_HKO / "hko_rhrread_summary.csv"


def parse_rhrread(payload, collected_at_hkt):
    collected_at = collected_at_hkt.isoformat(timespec="seconds")
    rows = []

    rainfall = payload.get("rainfall") or {}
    for item in rainfall.get("data") or []:
        rows.append({
            "collected_at_hkt": collected_at,
            "element": "rainfall_max_1h",
            "place": item.get("place"),
            "value": item.get("max"),
            "unit": item.get("unit"),
            "record_time": rainfall.get("recordTime"),
            "extra": item.get("main"),
        })

    temperature = payload.get("temperature") or {}
    for item in temperature.get("data") or []:
        rows.append({
            "collected_at_hkt": collected_at,
            "element": "temperature",
            "place": item.get("place"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "record_time": temperature.get("recordTime"),
            "extra": None,
        })

    humidity = payload.get("humidity") or {}
    for item in humidity.get("data") or []:
        rows.append({
            "collected_at_hkt": collected_at,
            "element": "humidity",
            "place": item.get("place"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "record_time": humidity.get("recordTime"),
            "extra": None,
        })

    uvindex = payload.get("uvindex") or {}
    for item in uvindex.get("data") or []:
        rows.append({
            "collected_at_hkt": collected_at,
            "element": "uvindex",
            "place": item.get("place"),
            "value": item.get("value"),
            "unit": None,
            "record_time": uvindex.get("recordTime"),
            "extra": item.get("desc"),
        })

    return pd.DataFrame(rows)


def parse_rhrread_summary(payload, collected_at_hkt):
    row = {
        "collected_at_hkt": collected_at_hkt.isoformat(timespec="seconds"),
        "update_time": payload.get("updateTime"),
        "icon": payload.get("icon"),
        "icon_update_time": payload.get("iconUpdateTime"),
        "rainfall_from_00_to_12_mm": payload.get("rainfallFrom00To12"),
        "mintemp_from_00_to_09_c": payload.get("mintempFrom00To09"),
        "rainfall_last_month_mm": payload.get("rainfallLastMonth"),
        "rainfall_january_to_last_month_mm": payload.get("rainfallJanuaryToLastMonth"),
        "warning_message": payload.get("warningMessage"),
        "tc_message": payload.get("tcmessage"),
    }
    return pd.DataFrame([row])


def collect_rhrread(logger=None):
    logger = logger or setup_logger()
    params = {"dataType": "rhrread", "lang": "en"}
    response = get_with_retry(HKO_WEATHER_API, params=params)
    payload = response.json()
    save_raw_json("hko_rhrread", payload)
    collected_at = now_hkt()
    df = parse_rhrread(payload, collected_at)
    summary_df = parse_rhrread_summary(payload, collected_at)
    output = append_to_csv(RHRREAD_FILE, df)
    summary_output = append_to_csv(RHRREAD_SUMMARY_FILE, summary_df)
    log_collection_event("hko_rhrread", "ok", len(df), output, notes="summary_rows=1")
    logger.info("hko_rhrread: collected %d observation rows -> %s", len(df), output)
    return df


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect HKO current weather report (dataType=rhrread)")
    parser.parse_args()
    collect_rhrread(logger)


if __name__ == "__main__":
    main()
