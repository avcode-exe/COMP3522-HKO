import argparse
from datetime import datetime, timedelta

import pandas as pd

from common import (
    HKO_WARN_DB_RAINSTORM_DAT,
    PROCESSED_HKO,
    PROJECT_DATA_END_DATE,
    PROJECT_DATA_START_DATE,
    ensure_directories,
    get_with_retry,
    log_collection_event,
    now_hkt,
    save_raw_text,
    setup_logger,
)

PROCESSED_FILE = PROCESSED_HKO / "hko_rainstorm_warning_history.csv"

COLOR_MAP = {
    "A": ("Amber", "WRAINA"),
    "R": ("Red", "WRAINR"),
    "B": ("Black", "WRAINB"),
}

WARNING_HISTORY_COLUMNS = [
    "warning_id",
    "episode_id",
    "episode_warning_index",
    "warning_color",
    "warning_code",
    "issue_datetime_hkt",
    "cancel_datetime_hkt",
    "duration_minutes",
    "year",
    "month",
    "source_url",
    "raw_text",
    "notes",
    "collected_at_hkt",
]


def _make_datetime(year, month, day, hour, minute):
    if hour == 24:
        return datetime(year, month, day) + timedelta(days=1), "hour 24 normalized to next day 00:00"
    return datetime(year, month, day, hour, minute), None


def _build_records(text, source_url):
    records = []
    for line in text.replace("\r", "").split("\n"):
        if not line.strip():
            continue
        fields = line.rstrip("\t").split("\t")
        if len(fields) != 13 or fields[0] not in COLOR_MAP:
            continue
        try:
            issue_dt, issue_note = _make_datetime(*[int(x) for x in fields[1:6]])
            cancel_dt, cancel_note = _make_datetime(*[int(x) for x in fields[6:11]])
            int(fields[11])
            int(fields[12])
        except (ValueError, IndexError):
            continue
        color_name, code = COLOR_MAP[fields[0]]
        records.append({
            "issue_dt": issue_dt,
            "cancel_dt": cancel_dt,
            "warning_color": color_name,
            "warning_code": code,
            "notes": "; ".join(note for note in (issue_note, cancel_note) if note),
            "source_url": source_url,
            "raw_text": line.strip(),
        })
    records.sort(key=lambda record: record["issue_dt"])
    return records


def parse_rainstorm_dat(text, collected_at_hkt, min_issue_date=None, max_issue_date=None):
    collected_at = collected_at_hkt.isoformat(timespec="seconds")
    rows = []
    prev_cancel = None
    episode_id = None
    episode_index = 0
    for record in _build_records(text, HKO_WARN_DB_RAINSTORM_DAT):
        issue_dt = record["issue_dt"]
        cancel_dt = record["cancel_dt"]
        if max_issue_date is not None and issue_dt > max_issue_date:
            break
        if prev_cancel is None or issue_dt != prev_cancel:
            episode_id = "RS-EP-" + issue_dt.strftime("%Y%m%dT%H%M")
            episode_index = 0
        episode_index += 1
        if min_issue_date is not None and issue_dt < min_issue_date:
            prev_cancel = cancel_dt
            continue
        rows.append({
            "warning_id": "RS-" + issue_dt.strftime("%Y%m%dT%H%M") + "-" + record["warning_code"][-1],
            "episode_id": episode_id,
            "episode_warning_index": episode_index,
            "warning_color": record["warning_color"],
            "warning_code": record["warning_code"],
            "issue_datetime_hkt": issue_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "cancel_datetime_hkt": cancel_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "duration_minutes": int((cancel_dt - issue_dt).total_seconds() // 60),
            "year": issue_dt.year,
            "month": issue_dt.month,
            "source_url": record["source_url"],
            "raw_text": record["raw_text"],
            "notes": record["notes"],
            "collected_at_hkt": collected_at,
        })
        prev_cancel = cancel_dt
    return pd.DataFrame(rows, columns=WARNING_HISTORY_COLUMNS)


def collect_warning_history(logger=None):
    logger = logger or setup_logger()
    response = get_with_retry(HKO_WARN_DB_RAINSTORM_DAT, max_tries=4)
    text = response.text
    timestamp = now_hkt().strftime("%Y%m%d_%H%M%S")
    save_raw_text("hko_warning_history", "rstorm_{}.dat".format(timestamp), text)
    min_issue_date = datetime.fromisoformat(PROJECT_DATA_START_DATE)
    max_issue_date = datetime.fromisoformat(PROJECT_DATA_END_DATE).replace(hour=23, minute=59, second=59) if PROJECT_DATA_END_DATE else None
    df = parse_rainstorm_dat(text, now_hkt(), min_issue_date=min_issue_date, max_issue_date=max_issue_date)
    df.to_csv(PROCESSED_FILE, index=False)
    log_collection_event("hko_warning_history", "ok", len(df), str(PROCESSED_FILE), notes="rainstorm DB; project window >= {}".format(PROJECT_DATA_START_DATE))
    logger.info("hko_warning_history: %d rainstorm warnings (>= %s) -> %s", len(df), PROJECT_DATA_START_DATE, PROCESSED_FILE)
    return df


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="Collect HKO rainstorm warning history (scraped from the HKO warning database)")
    parser.parse_args()
    collect_warning_history(logger)


if __name__ == "__main__":
    main()
