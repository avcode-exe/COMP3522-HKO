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

WARNING_CURRENT_FILE = PROCESSED_HKO / "hko_warning_current.csv"
WARNING_INFO_FILE = PROCESSED_HKO / "hko_warning_info.csv"

WARNING_CURRENT_COLUMNS = [
    "collected_at_hkt",
    "warning_key",
    "name",
    "code",
    "type",
    "action_code",
    "issue_time",
    "update_time",
    "expire_time",
]

WARNING_INFO_COLUMNS = [
    "collected_at_hkt",
    "warning_statement_code",
    "subtype",
    "update_time",
    "contents_text",
]


def parse_warning_summary(payload, collected_at_hkt):
    collected_at = collected_at_hkt.isoformat(timespec="seconds")
    rows = []
    for warning_key, warning_value in payload.items():
        if not isinstance(warning_value, dict):
            continue
        rows.append({
            "collected_at_hkt": collected_at,
            "warning_key": warning_key,
            "name": warning_value.get("name"),
            "code": warning_value.get("code"),
            "type": warning_value.get("type"),
            "action_code": warning_value.get("actionCode"),
            "issue_time": warning_value.get("issueTime"),
            "update_time": warning_value.get("updateTime"),
            "expire_time": warning_value.get("expireTime"),
        })
    return pd.DataFrame(rows, columns=WARNING_CURRENT_COLUMNS)


def parse_warning_information(payload, collected_at_hkt):
    collected_at = collected_at_hkt.isoformat(timespec="seconds")
    rows = []
    for item in payload.get("details") or []:
        contents = item.get("contents") or []
        rows.append({
            "collected_at_hkt": collected_at,
            "warning_statement_code": item.get("warningStatementCode"),
            "subtype": item.get("subtype"),
            "update_time": item.get("updateTime"),
            "contents_text": "\n".join(str(content) for content in contents),
        })
    return pd.DataFrame(rows, columns=WARNING_INFO_COLUMNS)


def collect_warning_summary(logger=None):
    logger = logger or setup_logger()
    params = {"dataType": "warnsum", "lang": "en"}
    response = get_with_retry(HKO_WEATHER_API, params=params)
    payload = response.json()
    save_raw_json("hko_warnsum", payload)
    collected_at = now_hkt()
    df = parse_warning_summary(payload, collected_at)
    output = append_to_csv(WARNING_CURRENT_FILE, df)
    log_collection_event("hko_warnsum", "ok", len(df), output, notes="active_warnings={}".format(len(df)))
    logger.info("hko_warnsum: %d active warnings -> %s", len(df), output)
    return df


def collect_warning_information(logger=None):
    logger = logger or setup_logger()
    params = {"dataType": "warningInfo", "lang": "en"}
    response = get_with_retry(HKO_WEATHER_API, params=params)
    payload = response.json()
    save_raw_json("hko_warning_info", payload)
    collected_at = now_hkt()
    df = parse_warning_information(payload, collected_at)
    output = append_to_csv(WARNING_INFO_FILE, df)
    log_collection_event("hko_warning_info", "ok", len(df), output)
    logger.info("hko_warning_info: %d warning detail rows -> %s", len(df), output)
    return df


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect HKO weather warning summary and warning information")
    parser.add_argument("--part", choices=["summary", "info", "both"], default="both")
    args = parser.parse_args()
    if args.part in ("summary", "both"):
        collect_warning_summary(logger)
    if args.part in ("info", "both"):
        collect_warning_information(logger)


if __name__ == "__main__":
    main()
