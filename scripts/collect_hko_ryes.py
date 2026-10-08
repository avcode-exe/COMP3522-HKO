import argparse
from datetime import date, timedelta

import pandas as pd

from common import (
    HKO_OPENDATA_API,
    PROCESSED_HKO,
    append_to_csv,
    default_end_date,
    ensure_directories,
    get_with_retry,
    log_collection_event,
    now_hkt,
    save_raw_json,
    setup_logger,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_daily_weather.csv"


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ryes(payload, collected_at_hkt):
    report_date = payload.get("ReportTimeInfoDate")
    bulletin_date = payload.get("BulletinDate")
    bulletin_time = payload.get("BulletinTime")
    rows = []

    def add(location, element, value, unit):
        rows.append({
            "report_date": report_date,
            "bulletin_date": bulletin_date,
            "bulletin_time": bulletin_time,
            "location": location,
            "element": element,
            "value": value,
            "unit": unit,
            "source_api": "hko_opendata_api",
        })

    for key, location in payload.items():
        if not key.endswith("LocationName"):
            continue
        prefix = key[: -len("LocationName")]
        max_temp = payload.get(prefix + "MaxTemp")
        min_temp = payload.get(prefix + "MinTemp")
        microsieverts = payload.get(prefix + "Microsieverts")
        if max_temp is not None:
            add(location, "max_temp_c", _to_float(max_temp), "C")
        if min_temp is not None:
            add(location, "min_temp_c", _to_float(min_temp), "C")
        if microsieverts is not None:
            add(location, "radiation_microsieverts_per_hour", _to_float(microsieverts), "microsievert/hour")

    hko = "Hong Kong Observatory"
    add(hko, "max_temp_c", _to_float(payload.get("HKOReadingsMaxTemp")), "C")
    add(hko, "min_temp_c", _to_float(payload.get("HKOReadingsMinTemp")), "C")
    add(hko, "min_grass_temp_c", _to_float(payload.get("HKOReadingsMinGrassTemp")), "C")
    add(hko, "rainfall_mm", _to_float(payload.get("HKOReadingsRainfall")), "mm")
    add(hko, "avg_rainfall_mm", _to_float(payload.get("HKOReadingsAvgRainfall")), "mm")
    add(hko, "accum_rainfall_mm_since_jan_1", _to_float(payload.get("HKOReadingsAccumRainfall")), "mm")
    add(hko, "max_rh_percent", _to_float(payload.get("HKOReadingsMaxRH")), "percent")
    add(hko, "min_rh_percent", _to_float(payload.get("HKOReadingsMinRH")), "percent")

    kings_park = "King's Park"
    add(kings_park, "sunshine_hours", _to_float(payload.get("KingsParkReadingsSunShine")), "hour")
    add(kings_park, "max_uv_index", _to_float(payload.get("KingsParkReadingsMaxUVIndex")), "index")
    add(kings_park, "mean_uv_index", _to_float(payload.get("KingsParkReadingsMeanUVIndex")), "index")

    return pd.DataFrame(rows)


def collect_ryes(date_str=None, logger=None):
    logger = logger or setup_logger()
    if date_str is None:
        date_str = (now_hkt() - timedelta(days=1)).date().isoformat()
    params = {"dataType": "RYES", "rformat": "json", "lang": "en", "date": date_str}
    response = get_with_retry(HKO_OPENDATA_API, params=params)
    if not response.text.lstrip().startswith("{"):
        raise ValueError("unexpected non-JSON response for RYES date={}".format(date_str))
    payload = response.json()
    compact_date = date_str.replace("-", "")
    filename = "RYES_{}_{}.json".format(compact_date, now_hkt().strftime("%Y%m%d_%H%M%S"))
    save_raw_json("hko_ryes", payload, filename=filename)
    collected_at = now_hkt()
    df = parse_ryes(payload, collected_at)
    output = append_to_csv(PROCESSED_FILE, df)
    log_collection_event("hko_ryes", "ok", len(df), output, notes="report_date={}".format(date_str))
    logger.info("hko_ryes: collected %d rows for %s -> %s", len(df), date_str, output)
    return df


def _daterange(start_date, end_date):
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def collect_ryes_range(start_date, end_date, logger=None):
    logger = logger or setup_logger()
    total_rows = 0
    failed = 0
    for date_str in _daterange(start_date, end_date):
        try:
            total_rows += len(collect_ryes(date_str=date_str, logger=logger))
        except Exception as exc:
            failed += 1
            logger.warning("hko_ryes failed for %s: %s", date_str, exc)
            log_collection_event("hko_ryes", "error", 0, notes="{}: {}".format(date_str, exc))
    logger.info("hko_ryes range complete: %d rows, %d failures", total_rows, failed)
    return total_rows, failed


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect HKO daily weather and radiation level report (dataType=RYES)")
    parser.add_argument("--date", default=None, help="single report date YYYY-MM-DD; default = yesterday (HKT)")
    parser.add_argument("--start-date", default=None, help="range start YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", default=None, help="range end YYYY-MM-DD (inclusive); default = yesterday")
    args = parser.parse_args()
    if args.start_date:
        end_date = args.end_date or default_end_date().isoformat()
        collect_ryes_range(args.start_date, end_date, logger)
    else:
        collect_ryes(date_str=args.date, logger=logger)


if __name__ == "__main__":
    main()
