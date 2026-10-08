import argparse
import calendar
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import pandas as pd

from common import (
    DATA_GOV_HK_ARCHIVE_API,
    HKO_CURRENT_WEATHER_RSS_URL,
    PROCESSED_HKO,
    PROJECT_DATA_START_DATE,
    RAW_ROOT,
    default_end_date,
    ensure_directories,
    get_with_retry,
    log_collection_event,
    now_hkt,
    project_start_year,
    save_raw_text,
    setup_logger,
    write_csv,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_rhrread_archive.csv"
SUMMARY_FILE = PROCESSED_HKO / "hko_rhrread_archive_summary.csv"

OBS_COLUMNS = ["issue_datetime_hkt", "archive_time_hkt", "element", "place", "value", "unit", "source"]
SUMMARY_COLUMNS = ["issue_datetime_hkt", "archive_time_hkt", "warning_text", "station_count", "source"]

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def _strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


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


def parse_current_weather_rss(xml_text, archive_time_hkt):
    root = ET.fromstring(xml_text)
    item = root.find(".//item")
    if item is None:
        return pd.DataFrame(columns=OBS_COLUMNS), pd.DataFrame(columns=SUMMARY_COLUMNS)
    title = item.findtext("title") or ""
    pub_date = item.findtext("pubDate") or ""
    description = item.findtext("description") or ""
    issue_dt = _issue_datetime(title, pub_date)
    if issue_dt is None:
        return pd.DataFrame(columns=OBS_COLUMNS), pd.DataFrame(columns=SUMMARY_COLUMNS)
    issue_str = issue_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    body = _strip_html(description)

    rows = []
    hko_temp = re.search(r"Air temperature\s*:\s*(-?\d+)", body)
    hko_rh = re.search(r"Relative Humidity\s*:\s*(\d+)", body)
    if hko_temp:
        rows.append((issue_str, archive_time_hkt, "temperature", "Hong Kong Observatory", int(hko_temp.group(1)), "C", "data_gov_hk_rhrread"))
    if hko_rh:
        rows.append((issue_str, archive_time_hkt, "humidity", "Hong Kong Observatory", int(hko_rh.group(1)), "percent", "data_gov_hk_rhrread"))

    stations = []
    block = re.search(r"at other places were:\s*(.*)", body)
    if block:
        for name, value in re.findall(r"([A-Za-z][A-Za-z'.\-/ ]*?)\s+(-?\d+)\s+degrees", block.group(1)):
            place = re.sub(r"\s+", " ", name).strip()
            if place and place.lower() != "hong kong observatory":
                stations.append((place, int(value)))
                rows.append((issue_str, archive_time_hkt, "temperature", place, int(value), "C", "data_gov_hk_rhrread"))

    warning = re.search(r"Please be reminded that:\s*(.+?)(?:\s*The air temperatures|$)", body)
    warning_text = warning.group(1).strip() if warning else None
    summary = [{
        "issue_datetime_hkt": issue_str,
        "archive_time_hkt": archive_time_hkt,
        "warning_text": warning_text,
        "station_count": len(stations),
        "source": "data_gov_hk_rhrread",
    }]
    return pd.DataFrame(rows, columns=OBS_COLUMNS), pd.DataFrame(summary, columns=SUMMARY_COLUMNS)


def _timestamp_to_hkt(ts):
    match = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", ts)
    if not match:
        return None
    y, mo, d, h, mi = match.groups()
    return "{}-{}-{}T{}:{}:00+08:00".format(y, mo, d, h, mi)


def list_versions(start, end):
    response = get_with_retry(
        DATA_GOV_HK_ARCHIVE_API + "/list-file-versions",
        params={"url": HKO_CURRENT_WEATHER_RSS_URL, "start": start, "end": end},
        max_tries=4,
    )
    return response.json().get("timestamps", [])


def download_snapshot(timestamp):
    filename = "cwr_{}.xml".format(timestamp.replace("-", "_"))
    candidate = RAW_ROOT / "data_gov_hk_rhrread" / now_hkt().strftime("%Y%m%d") / filename
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    response = get_with_retry(
        DATA_GOV_HK_ARCHIVE_API + "/get-file",
        params={"url": HKO_CURRENT_WEATHER_RSS_URL, "time": timestamp},
        max_tries=4,
    )
    save_raw_text("data_gov_hk_rhrread", filename, response.text)
    return response.text


def _month_windows(start, end):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last_day = calendar.monthrange(cur.year, cur.month)[1]
        yield cur, min(date(cur.year, cur.month, last_day), end)
        cur = date(cur.year, cur.month, last_day) + timedelta(days=1)


def collect_rhrread_archive(start_date=None, end_date=None, per_day=1, logger=None):
    logger = logger or setup_logger()
    start = datetime.fromisoformat(start_date).date() if start_date else date(project_start_year(), 1, 1)
    end = datetime.fromisoformat(end_date).date() if end_date else default_end_date()
    all_obs = []
    all_summary = []
    for ws, we in _month_windows(start, end):
        try:
            stamps = list_versions(ws.strftime("%Y%m%d"), we.strftime("%Y%m%d"))
        except Exception as exc:
            logger.warning("rhrread archive list failed %s..%s: %s", ws, we, exc)
            continue
        by_day = {}
        for ts in stamps:
            by_day.setdefault(ts[:8], []).append(ts)
        chosen = []
        for _day, ts_list in sorted(by_day.items()):
            ts_list.sort()
            n = len(ts_list)
            if per_day >= n:
                chosen.extend(ts_list)
            elif per_day <= 1:
                chosen.append(ts_list[-1])
            else:
                idxs = sorted({round(i * (n - 1) / (per_day - 1)) for i in range(per_day)})
                chosen.extend(ts_list[i] for i in idxs)
        obs_count = 0
        for ts in chosen:
            try:
                obs, summary = parse_current_weather_rss(download_snapshot(ts), _timestamp_to_hkt(ts))
                all_obs.append(obs)
                all_summary.append(summary)
                obs_count += len(obs)
            except Exception as exc:
                logger.warning("rhrread archive %s failed: %s", ts, exc)
        log_collection_event("data_gov_hk_rhrread", "ok", obs_count, str(PROCESSED_FILE),
                             notes="{} snapshots {}..{}".format(len(chosen), ws, we))
        logger.info("rhrread archive %s..%s: %d snapshots, %d obs rows", ws, we, len(chosen), obs_count)
    if all_obs:
        obs = pd.concat(all_obs, ignore_index=True).drop_duplicates(subset=["issue_datetime_hkt", "element", "place"])
        obs = obs[obs["issue_datetime_hkt"].astype(str) >= PROJECT_DATA_START_DATE]
        write_csv(PROCESSED_FILE, obs.sort_values(["issue_datetime_hkt", "element", "place"]))
    if all_summary:
        summary = pd.concat(all_summary, ignore_index=True).drop_duplicates(subset=["issue_datetime_hkt"])
        write_csv(SUMMARY_FILE, summary.sort_values("issue_datetime_hkt"))
    logger.info("rhrread archive backfill complete: %d obs rows", sum(len(x) for x in all_obs))
    return sum(len(x) for x in all_obs)


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Backfill historical HKO Current Weather Report from the data.gov.hk archive (RSS)")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--per-day", type=int, default=1, help="snapshots per day (1-24)")
    args = parser.parse_args()
    collect_rhrread_archive(start_date=args.start_date, end_date=args.end_date, per_day=args.per_day, logger=logger)


if __name__ == "__main__":
    main()
