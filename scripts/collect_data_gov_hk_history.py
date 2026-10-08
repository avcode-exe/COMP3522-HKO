import argparse
import calendar
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import pandas as pd

from common import (
    DATA_GOV_HK_ARCHIVE_API,
    HKO_FND_RSS_URL,
    PROCESSED_HKO,
    PROJECT_DATA_START_DATE,
    RAW_ROOT,
    default_end_date,
    ensure_directories,
    get_with_retry,
    log_collection_event,
    now_hkt,
    project_end_year,
    project_start_year,
    save_raw_text,
    setup_logger,
    write_csv,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_fnd_archive.csv"

FND_ARCHIVE_COLUMNS = [
    "issue_datetime_hkt",
    "archive_time_hkt",
    "issue_date",
    "target_date",
    "lead_time_days",
    "week",
    "forecast_weather_text",
    "forecast_wind",
    "forecast_max_temp_c",
    "forecast_min_temp_c",
    "forecast_max_rh_percent",
    "forecast_min_rh_percent",
    "psr",
    "general_situation",
    "source",
]

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _strip_html(text):
    text = text.replace("<br/>", "\n").replace("<br>", "\n").replace("<p/>", "\n").replace("<p>", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t]+", " ", text)


def _issue_datetime(title, pub_date_text):
    match = re.search(r"updated at\s+(\d{1,2}):(\d{2})\s+HKT\s+(\d{1,2})/([A-Za-z]{3})/(\d{4})", title or "")
    if match:
        hour, minute, day, mon, year = match.groups()
        month = MONTHS.get(mon.lower())
        if month:
            return datetime(int(year), month, int(day), int(hour), int(minute))
    try:
        dt = parsedate_to_datetime(pub_date_text)
        return dt.astimezone(now_hkt().tzinfo).replace(tzinfo=None)
    except Exception:
        return None


def _target_date(day, month, issue_dt):
    year = issue_dt.year
    if month == 1 and issue_dt.month == 12:
        year += 1
    return date(year, month, day)


def parse_fnd_rss(xml_text, archive_time_hkt):
    root = ET.fromstring(xml_text)
    item = root.find(".//item")
    if item is None:
        return pd.DataFrame(columns=FND_ARCHIVE_COLUMNS)
    title = item.findtext("title") or ""
    pub_date = item.findtext("pubDate") or ""
    description = item.findtext("description") or ""
    issue_dt = _issue_datetime(title, pub_date)
    if issue_dt is None:
        return pd.DataFrame(columns=FND_ARCHIVE_COLUMNS)

    text = _strip_html(description)
    general = text.split("Date/Month:")[0].replace("General Situation:", "").strip()
    general = re.sub(r"\s+", " ", general)

    rows = []
    for block in text.split("Date/Month:")[1:]:
        block = re.split(r"Sea surface", block)[0]
        date_match = re.search(r"(\d{1,2})/(\d{1,2})", block)
        if not date_match:
            continue
        day, month = int(date_match.group(1)), int(date_match.group(2))
        target = _target_date(day, month, issue_dt)
        wind = re.search(r"Wind:\s*(.*?)Weather:", block, re.S)
        weather = re.search(r"Weather:\s*(.*?)Temp range:", block, re.S)
        temp = re.search(r"Temp range:\s*(-?\d+)\s*-\s*(-?\d+)", block)
        rh = re.search(r"R\.H\. range:\s*(\d+)\s*-\s*(\d+)", block)
        psr = re.search(r"PSR:\s*([^\n]+)", block)
        rows.append({
            "issue_datetime_hkt": issue_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "archive_time_hkt": archive_time_hkt,
            "issue_date": issue_dt.date().isoformat(),
            "target_date": target.isoformat(),
            "lead_time_days": (target - issue_dt.date()).days,
            "week": WEEKDAYS[target.weekday()],
            "forecast_weather_text": re.sub(r"\s+", " ", weather.group(1)).strip() if weather else None,
            "forecast_wind": re.sub(r"\s+", " ", wind.group(1)).strip() if wind else None,
            "forecast_max_temp_c": int(temp.group(2)) if temp else None,
            "forecast_min_temp_c": int(temp.group(1)) if temp else None,
            "forecast_max_rh_percent": int(rh.group(2)) if rh else None,
            "forecast_min_rh_percent": int(rh.group(1)) if rh else None,
            "psr": re.sub(r"\s+", " ", psr.group(1)).strip() if psr else None,
            "general_situation": general,
            "source": "data_gov_hk_9day",
        })
    return pd.DataFrame(rows, columns=FND_ARCHIVE_COLUMNS)


def _timestamp_to_hkt(ts):
    match = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", ts)
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    return "{}-{}-{}T{}:{}:00+08:00".format(year, month, day, hour, minute)


def list_versions(start, end, logger):
    response = get_with_retry(
        DATA_GOV_HK_ARCHIVE_API + "/list-file-versions",
        params={"url": HKO_FND_RSS_URL, "start": start, "end": end},
        max_tries=4,
    )
    return response.json().get("timestamps", [])


def download_snapshot(timestamp, logger):
    filename = "fnd_{}.xml".format(timestamp.replace("-", "_"))
    candidate = RAW_ROOT / "data_gov_hk_9day" / now_hkt().strftime("%Y%m%d") / filename
    if candidate.exists():
        return str(candidate), candidate.read_text(encoding="utf-8")
    response = get_with_retry(
        DATA_GOV_HK_ARCHIVE_API + "/get-file",
        params={"url": HKO_FND_RSS_URL, "time": timestamp},
        max_tries=4,
    )
    raw_path = save_raw_text("data_gov_hk_9day", filename, response.text)
    return raw_path, response.text


def _month_windows(start_date, end_date):
    cur = date(start_date.year, start_date.month, 1)
    while cur <= end_date:
        last_day = calendar.monthrange(cur.year, cur.month)[1]
        window_end = min(date(cur.year, cur.month, last_day), end_date)
        yield cur, window_end
        cur = date(cur.year, cur.month, last_day) + (date.resolution * 1)


def collect_fnd_archive(start_date=None, end_date=None, per_day=1, logger=None):
    logger = logger or setup_logger()
    start = datetime.fromisoformat(start_date).date() if start_date else date(project_start_year(), 1, 1)
    end = datetime.fromisoformat(end_date).date() if end_date else default_end_date()
    total = 0
    for window_start, window_end in _month_windows(start, end):
        try:
            stamps = list_versions(window_start.strftime("%Y%m%d"), window_end.strftime("%Y%m%d"), logger)
        except Exception as exc:
            logger.warning("fnd archive list failed %s..%s: %s", window_start, window_end, exc)
            continue
        by_day = {}
        for ts in stamps:
            by_day.setdefault(ts[:8], []).append(ts)
        chosen = []
        for day, ts_list in sorted(by_day.items()):
            ts_list.sort()
            n = len(ts_list)
            if per_day >= n:
                chosen.extend(ts_list)
            elif per_day <= 1:
                chosen.append(ts_list[-1])
            else:
                idxs = sorted({round(i * (n - 1) / (per_day - 1)) for i in range(per_day)})
                chosen.extend(ts_list[i] for i in idxs)
        rows = []
        for ts in chosen:
            try:
                raw_path, xml_text = download_snapshot(ts, logger)
                df = parse_fnd_rss(xml_text, _timestamp_to_hkt(ts))
                rows.append(df)
                total += len(df)
            except Exception as exc:
                logger.warning("fnd archive %s failed: %s", ts, exc)
        if rows:
            month_df = pd.concat(rows, ignore_index=True)
            append_month_write(month_df, logger, window_start)
        log_collection_event("data_gov_hk_9day", "ok", sum(len(r) for r in rows), str(PROCESSED_FILE),
                             notes="{} snapshots {}..{}".format(len(chosen), window_start, window_end))
        logger.info("fnd archive %s..%s: %d snapshots, %d rows", window_start, window_end, len(chosen), sum(len(r) for r in rows))
    logger.info("fnd archive backfill complete: %d rows", total)
    return total


def append_month_write(month_df, logger, window_start):
    frames = []
    if PROCESSED_FILE.exists():
        frames.append(pd.read_csv(PROCESSED_FILE))
    frames.append(month_df)
    combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["issue_datetime_hkt", "target_date"]).sort_values(["issue_datetime_hkt", "target_date"])
    write_csv(PROCESSED_FILE, combined)


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Backfill historical HKO 9-day forecasts from the data.gov.hk Historical Archive API")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (default project start)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (default yesterday)")
    parser.add_argument("--per-day", type=int, default=1, help="snapshots per day (1-4)")
    args = parser.parse_args()
    collect_fnd_archive(start_date=args.start_date, end_date=args.end_date, per_day=args.per_day, logger=logger)


if __name__ == "__main__":
    main()
