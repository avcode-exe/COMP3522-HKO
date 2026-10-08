import json
import re
import shutil
from datetime import datetime

import numpy as np
import pandas as pd

from common import (
    HKT,
    META_ROOT,
    PROCESSED_CEDD,
    PROCESSED_COWIN,
    PROCESSED_HKO,
    PROCESSED_OM,
    PROJECT_DATA_END_DATE,
    PROJECT_DATA_START_DATE,
    RAW_ROOT,
    ensure_directories,
    now_hkt,
    setup_logger,
    write_csv,
    write_parquet,
)
from collect_cedd import parse_cedd_rainfall
from collect_cowin import build_cowin_labels, build_cowin_station_dimension, parse_cowin_rainfall, parse_cowin_stations
from collect_data_gov_hk_history import parse_fnd_rss
from collect_data_gov_hk_rhrread import parse_current_weather_rss
from collect_hko_daily_rainfall import parse_cis_rainfall_csv
from collect_hko_daily_temp import parse_daily_temp_csv, parse_daily_temp_json
from collect_hko_fnd import parse_fnd
from collect_hko_hourly_rain import parse_hourly_rain
from collect_open_meteo import parse_open_meteo
from collect_hko_rhrread import parse_rhrread, parse_rhrread_summary
from collect_hko_ryes import parse_ryes
from collect_hko_warning_history import parse_rainstorm_dat
from collect_hko_warnings import parse_warning_information, parse_warning_summary
from generate_processed_readmes import generate_all


def _collected_at_from_filename(path):
    match = re.search(r"(\d{8})_(\d{6})", path.name)
    if not match:
        return None
    stamp = match.group(1) + match.group(2)
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=HKT)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _in_window(values):
    s = values.astype(str)
    ok = s >= PROJECT_DATA_START_DATE
    if PROJECT_DATA_END_DATE:
        ok = ok & (s <= PROJECT_DATA_END_DATE + "\uffff")
    return ok


def _archive_time_from_filename(path):
    match = re.search(r"fnd_(\d{8})_(\d{4})", path.name)
    if not match:
        return None
    stamp, hm = match.group(1), match.group(2)
    return "{}-{}-{}T{}:{}:00+08:00".format(stamp[:4], stamp[4:6], stamp[6:8], hm[:2], hm[2:])


def _cwr_time_from_filename(path):
    match = re.search(r"cwr_(\d{8})_(\d{4})", path.name)
    if not match:
        return None
    stamp, hm = match.group(1), match.group(2)
    return "{}-{}-{}T{}:{}:00+08:00".format(stamp[:4], stamp[4:6], stamp[6:8], hm[:2], hm[2:])


def rebuild_fnd(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_fnd").rglob("*.json")):
        collected_at = _collected_at_from_filename(path)
        if collected_at is None:
            continue
        frames.append(parse_fnd(_read_json(path), collected_at))
    if not frames:
        logger.info("flatten hko_fnd: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["snapshot_id", "target_date"])
        df = df.sort_values(["target_date", "issue_date_proxy", "snapshot_id"])
    write_csv(PROCESSED_HKO / "hko_fnd_daily.csv", df)
    logger.info("flatten hko_fnd: %d rows", len(df))
    return len(df)


def rebuild_data_gov_hk(logger):
    frames = []
    for path in sorted((RAW_ROOT / "data_gov_hk_9day").rglob("*.xml")):
        archive_time = _archive_time_from_filename(path)
        with open(path, "r", encoding="utf-8") as f:
            frames.append(parse_fnd_rss(f.read(), archive_time))
    if not frames:
        logger.info("flatten data_gov_hk_9day: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True)
    if not df.empty:
        df = df.drop_duplicates(subset=["issue_datetime_hkt", "target_date"])
        df = df[df["issue_date"].notna() & _in_window(df["issue_date"])]
        df = df.sort_values(["issue_datetime_hkt", "target_date"])
    write_csv(PROCESSED_HKO / "hko_fnd_archive.csv", df)
    logger.info("flatten data_gov_hk_9day: %d rows, %d snapshots", len(df), df["issue_datetime_hkt"].nunique() if not df.empty else 0)
    return len(df)


def rebuild_data_gov_hk_rhrread(logger):
    obs_frames = []
    summary_frames = []
    for path in sorted((RAW_ROOT / "data_gov_hk_rhrread").rglob("*.xml")):
        archive_time = _cwr_time_from_filename(path)
        with open(path, "r", encoding="utf-8") as f:
            obs, summary = parse_current_weather_rss(f.read(), archive_time)
        obs_frames.append(obs)
        summary_frames.append(summary)
    if not obs_frames:
        logger.info("flatten data_gov_hk_rhrread: no raw data, skipping")
        return 0
    obs = pd.concat(obs_frames, ignore_index=True)
    if not obs.empty:
        obs = obs.drop_duplicates(subset=["issue_datetime_hkt", "element", "place"])
        obs = obs[_in_window(obs["issue_datetime_hkt"])]
        obs = obs.sort_values(["issue_datetime_hkt", "element", "place"])
    write_csv(PROCESSED_HKO / "hko_rhrread_archive.csv", obs)
    summary = pd.concat(summary_frames, ignore_index=True)
    if not summary.empty:
        summary = summary.drop_duplicates(subset=["issue_datetime_hkt"])
        summary = summary[_in_window(summary["issue_datetime_hkt"])]
        summary = summary.sort_values("issue_datetime_hkt")
    write_csv(PROCESSED_HKO / "hko_rhrread_archive_summary.csv", summary)
    logger.info("flatten data_gov_hk_rhrread: %d obs rows, %d summary rows", len(obs), len(summary))
    return len(obs)


def rebuild_hourly_rain(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_hourly_rain").rglob("*.json")):
        frames.append(parse_hourly_rain(_read_json(path)))
    if not frames:
        logger.info("flatten hko_hourly_rain: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["obs_time_hkt", "station_id"])
        df = df.sort_values(["obs_time_hkt", "station_id"])
    write_csv(PROCESSED_HKO / "hko_hourly_rain.csv", df)
    logger.info("flatten hko_hourly_rain: %d rows", len(df))
    return len(df)


def rebuild_warning_current(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_warnsum").rglob("*.json")):
        collected_at = _collected_at_from_filename(path)
        if collected_at is None:
            continue
        frames.append(parse_warning_summary(_read_json(path), collected_at))
    if not frames:
        logger.info("flatten hko_warnsum: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["collected_at_hkt", "warning_key"])
        df = df.sort_values(["collected_at_hkt", "warning_key"])
    write_csv(PROCESSED_HKO / "hko_warning_current.csv", df)
    logger.info("flatten hko_warnsum: %d rows", len(df))
    return len(df)


def rebuild_warning_info(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_warning_info").rglob("*.json")):
        collected_at = _collected_at_from_filename(path)
        if collected_at is None:
            continue
        frames.append(parse_warning_information(_read_json(path), collected_at))
    if not frames:
        logger.info("flatten hko_warning_info: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["collected_at_hkt", "warning_statement_code", "subtype", "update_time"])
        df = df.sort_values(["collected_at_hkt", "update_time"])
    write_csv(PROCESSED_HKO / "hko_warning_info.csv", df)
    logger.info("flatten hko_warning_info: %d rows", len(df))
    return len(df)


def rebuild_rhrread(logger):
    frames = []
    summary_frames = []
    for path in sorted((RAW_ROOT / "hko_rhrread").rglob("*.json")):
        collected_at = _collected_at_from_filename(path)
        if collected_at is None:
            continue
        payload = _read_json(path)
        frames.append(parse_rhrread(payload, collected_at))
        summary_frames.append(parse_rhrread_summary(payload, collected_at))
    if not frames:
        logger.info("flatten hko_rhrread: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["collected_at_hkt", "element", "place"])
        df = df.sort_values(["collected_at_hkt", "element", "place"])
    write_csv(PROCESSED_HKO / "hko_rhrread.csv", df)
    summary_df = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    if not summary_df.empty:
        summary_df = summary_df.drop_duplicates(subset=["collected_at_hkt"])
        summary_df = summary_df.sort_values("collected_at_hkt")
    write_csv(PROCESSED_HKO / "hko_rhrread_summary.csv", summary_df)
    logger.info("flatten hko_rhrread: %d observation rows, %d summary rows", len(df), len(summary_df))
    return len(df)


def rebuild_daily_temp(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_daily_temp").rglob("*")):
        match = re.match(r"(CLMTEMP|CLMMAXT|CLMMINT)_([A-Za-z0-9]+)_(\d{4})_", path.name)
        if not match:
            continue
        data_type, station_code, year = match.group(1), match.group(2), int(match.group(3))
        if path.suffix == ".json":
            frames.append(parse_daily_temp_json(_read_json(path), data_type, station_code, year))
        elif path.suffix == ".csv":
            with open(path, "r", encoding="utf-8-sig") as f:
                frames.append(parse_daily_temp_csv(f.read(), data_type, station_code, year))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["station_code", "date", "data_type"])
        df = df[df["date"].notna() & _in_window(df["date"])]
        df = df.sort_values(["station_code", "date", "data_type"])
    write_csv(PROCESSED_HKO / "hko_daily_temp.csv", df)
    wide = pd.DataFrame(columns=["station_code", "date", "mean_temp_c", "max_temp_c", "min_temp_c"])
    if not df.empty:
        pivot = df.pivot_table(index=["station_code", "date"], columns="data_type", values="value_c", aggfunc="first")
        pivot = pivot.rename(columns={"CLMTEMP": "mean_temp_c", "CLMMAXT": "max_temp_c", "CLMMINT": "min_temp_c"})
        pivot = pivot.reset_index()
        for column in ("mean_temp_c", "max_temp_c", "min_temp_c"):
            if column not in pivot.columns:
                pivot[column] = pd.NA
        wide = pivot[["station_code", "date", "mean_temp_c", "max_temp_c", "min_temp_c"]]
        wide = wide.sort_values(["station_code", "date"])
    write_csv(PROCESSED_HKO / "hko_daily_temp_wide.csv", wide)
    logger.info("flatten hko_daily_temp: %d long rows, %d wide rows", len(df), len(wide))
    return len(df)


def rebuild_daily_rainfall(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_daily_rainfall").rglob("*.csv")):
        match = re.match(r"daily_([A-Za-z0-9]+)_RF_(\d{4})_", path.name)
        if not match:
            continue
        station_code, year = match.group(1), int(match.group(2))
        with open(path, "r", encoding="utf-8-sig") as f:
            frames.append(parse_cis_rainfall_csv(f.read(), station_code, year))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["station_code", "date"])
        df = df[df["date"].notna() & _in_window(df["date"])]
        df = df.sort_values(["station_code", "date"])
    write_csv(PROCESSED_HKO / "hko_daily_rainfall.csv", df)
    logger.info("flatten hko_daily_rainfall: %d rows", len(df))
    return len(df)


def rebuild_ryes(logger):
    frames = []
    for path in sorted((RAW_ROOT / "hko_ryes").rglob("*.json")):
        collected_at = _collected_at_from_filename(path)
        if collected_at is None:
            continue
        frames.append(parse_ryes(_read_json(path), collected_at))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["report_date", "location", "element"])
        start_c = PROJECT_DATA_START_DATE.replace("-", "")
        end_c = PROJECT_DATA_END_DATE.replace("-", "") if PROJECT_DATA_END_DATE else "99999999"
        df = df[df["report_date"].astype(str).between(start_c, end_c)]
        df = df.sort_values(["report_date", "location", "element"])
    write_csv(PROCESSED_HKO / "hko_daily_weather.csv", df)
    logger.info("flatten hko_ryes: %d rows", len(df))
    return len(df)


def rebuild_open_meteo(logger):
    coord_map = {}
    stations_path = META_ROOT / "stations.csv"
    if stations_path.exists():
        stations = pd.read_csv(stations_path)
        for _index, row in stations.iterrows():
            coord_map[row["station_id"]] = (row["latitude"], row["longitude"])
    total = 0
    for source_api, category, hourly_file, daily_file in [
        ("historical_forecast", "om_historical_forecast", "om_historical_forecast_hourly.parquet", "om_historical_forecast_daily.parquet"),
        ("historical_weather", "om_historical_weather", "om_historical_weather_hourly.parquet", "om_historical_weather_daily.parquet"),
    ]:
        hourly_frames = []
        daily_frames = []
        for path in sorted((RAW_ROOT / category).rglob("*.json")):
            match = re.match(r"(historical_forecast|historical_weather)_([A-Za-z0-9_.]+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_", path.name)
            if not match:
                continue
            station_id = match.group(2)
            requested_latitude, requested_longitude = coord_map.get(station_id, (None, None))
            hourly_df, daily_df = parse_open_meteo(
                _read_json(path), source_api, station_id, requested_latitude, requested_longitude
            )
            hourly_frames.append(hourly_df)
            daily_frames.append(daily_df)
        hourly_all = pd.concat(hourly_frames, ignore_index=True) if hourly_frames else pd.DataFrame()
        if not hourly_all.empty:
            hourly_all = hourly_all[_in_window(hourly_all["time_hkt"])]
            hourly_all = hourly_all.drop_duplicates(subset=["station_id", "source_api", "time_hkt"])
            hourly_all = hourly_all.sort_values(["station_id", "time_hkt"])
        write_parquet(PROCESSED_OM / hourly_file, hourly_all)
        daily_all = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
        if not daily_all.empty:
            daily_all = daily_all[_in_window(daily_all["date"])]
            daily_all = daily_all.drop_duplicates(subset=["station_id", "source_api", "date"])
            daily_all = daily_all.sort_values(["station_id", "date"])
        write_parquet(PROCESSED_OM / daily_file, daily_all)
        logger.info("flatten %s: %d hourly rows, %d daily rows", category, len(hourly_all), len(daily_all))
        total += len(hourly_all) + len(daily_all)
    return total


def rebuild_cowin(logger):
    rain_frames = []
    station_frames = []
    for zippath in sorted((RAW_ROOT / "cowin").rglob("*.zip")):
        try:
            year = int(zippath.stem)
        except ValueError:
            continue
        content = zippath.read_bytes()
        rain_frames.append(parse_cowin_rainfall(content, year))
        station_frames.append(parse_cowin_stations(content, year))
    rain = pd.concat(rain_frames, ignore_index=True) if rain_frames else pd.DataFrame()
    if not rain.empty:
        rain = rain.drop_duplicates(subset=["obs_time_hkt", "station_id"]).sort_values(["obs_time_hkt", "station_id"])
    write_parquet(PROCESSED_COWIN / "cowin_hourly_rainfall.parquet", rain)
    stations = build_cowin_station_dimension(station_frames, rain)
    write_parquet(PROCESSED_COWIN / "cowin_stations.parquet", stations)
    logger.info("flatten cowin: %d hourly rainfall rows, %d station rows", len(rain), len(stations))
    return len(rain)


def rebuild_cedd(logger):
    frames = []
    for path in sorted((RAW_ROOT / "cedd").rglob("*.csv")):
        try:
            year = int(path.stem)
        except ValueError:
            continue
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            frames.append(parse_cedd_rainfall(f.read(), year))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["date", "station_id"])
        df = df[df["date"].notna() & _in_window(df["date"])]
        df = df.sort_values(["date", "station_id"])
    write_parquet(PROCESSED_CEDD / "cedd_daily_rainfall.parquet", df)
    stations = pd.DataFrame(columns=["station_id", "station_name", "easting", "northing", "source"])
    if not df.empty:
        stations = df[["station_id", "station_name", "easting", "northing"]].drop_duplicates(subset=["station_id"]).sort_values("station_id")
        stations["source"] = "cedd_geo"
    write_csv(PROCESSED_CEDD / "cedd_stations.csv", stations)
    logger.info("flatten cedd: %d daily rainfall rows, %d gauges", len(df), df["station_id"].nunique() if not df.empty else 0)
    return len(df)


def rebuild_warning_history(logger):
    frames = []
    max_issue = datetime.fromisoformat(PROJECT_DATA_END_DATE).replace(hour=23, minute=59, second=59) if PROJECT_DATA_END_DATE else None
    for path in sorted((RAW_ROOT / "hko_warning_history").rglob("*.dat")):
        collected_at = _collected_at_from_filename(path) or now_hkt()
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        frames.append(parse_rainstorm_dat(
            text, collected_at, min_issue_date=datetime.fromisoformat(PROJECT_DATA_START_DATE), max_issue_date=max_issue
        ))
    if not frames:
        logger.info("flatten hko_warning_history: no raw data, skipping")
        return 0
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.drop_duplicates(subset=["warning_id"]).sort_values("issue_datetime_hkt")
    write_csv(PROCESSED_HKO / "hko_rainstorm_warning_history.csv", df)
    logger.info("flatten hko_warning_history: %d rows", len(df))
    return len(df)


def build_warning_windows(logger):
    columns = [
        "anchor_time_hkt",
        "label_warning_3h",
        "label_warning_6h",
        "label_warning_12h",
        "next_warning_issue_hkt",
        "hours_to_next_warning",
        "next_warning_color",
        "label_basis",
    ]
    history_path = PROCESSED_HKO / "hko_rainstorm_warning_history.csv"
    if not history_path.exists() or history_path.stat().st_size == 0:
        logger.warning("build_warning_windows: %s not found, skipping", history_path)
        return 0
    history = pd.read_csv(history_path)
    if history.empty:
        write_csv(PROCESSED_HKO / "labels_warning_windows.csv", pd.DataFrame(columns=columns))
        logger.info("build labels_warning_windows: 0 rows (no warnings)")
        return 0

    issues = pd.to_datetime(history["issue_datetime_hkt"], errors="coerce", utc=True)
    valid = history.assign(issue_dt=issues).dropna(subset=["issue_dt"]).sort_values("issue_dt")
    valid["issue_dt"] = valid["issue_dt"].dt.tz_convert("Asia/Hong_Kong").dt.tz_localize(None)
    issue_arr = valid["issue_dt"].to_numpy().astype("datetime64[ns]")
    color_arr = valid["warning_color"].to_numpy()

    anchor_end = "{} 23:00:00".format(PROJECT_DATA_END_DATE) if PROJECT_DATA_END_DATE else now_hkt().strftime("%Y-%m-%d 23:00:00")
    anchors = pd.date_range(PROJECT_DATA_START_DATE, anchor_end, freq="h")
    anchor_arr = anchors.to_numpy().astype("datetime64[ns]")
    lower = np.searchsorted(issue_arr, anchor_arr, side="right")
    has_next = lower < len(issue_arr)
    lower_clipped = np.clip(lower, 0, len(issue_arr) - 1)
    next_issue = issue_arr[lower_clipped]
    next_issue_out = np.where(has_next, next_issue, np.datetime64("NaT", "ns")).astype("datetime64[ns]")
    hours_to = ((next_issue - anchor_arr) / np.timedelta64(1, "h"))
    hours_to = np.where(has_next, hours_to, np.nan)
    next_color = np.where(has_next, color_arr[lower_clipped], None)

    df = pd.DataFrame({"anchor_time_hkt": anchors.strftime("%Y-%m-%dT%H:%M:%S+08:00")})
    for lead in (3, 6, 12):
        limit = anchor_arr + np.timedelta64(lead, "h")
        df["label_warning_{}h".format(lead)] = (has_next & (next_issue <= limit)).astype(int)
    next_issue_str = pd.Series(next_issue_out).dt.strftime("%Y-%m-%dT%H:%M:%S+08:00").fillna("")
    df["next_warning_issue_hkt"] = next_issue_str.to_numpy()
    df["hours_to_next_warning"] = np.round(hours_to, 3)
    df["next_warning_color"] = next_color
    df["label_basis"] = "warning_issued_within_next_Nh"
    write_csv(PROCESSED_HKO / "labels_warning_windows.csv", df)
    logger.info(
        "build labels_warning_windows: %d hourly anchors (positive: 3h=%d 6h=%d 12h=%d)",
        len(df), int(df["label_warning_3h"].sum()), int(df["label_warning_6h"].sum()), int(df["label_warning_12h"].sum()),
    )
    return len(df)


def build_rainstorm_labels(logger):
    rain_path = PROCESSED_HKO / "hko_hourly_rain.csv"
    if not rain_path.exists():
        logger.warning("build_rainstorm_labels: %s not found, skipping", rain_path)
        return 0
    df = pd.read_csv(rain_path)
    df = df[df["rain_mm_1h"].notna()]
    rows = []
    for obs_time, group in df.groupby("obs_time_hkt"):
        amber = sorted(group.loc[group["rain_mm_1h"] >= 30.0, "station_id"].tolist())
        red = sorted(group.loc[group["rain_mm_1h"] >= 50.0, "station_id"].tolist())
        black = sorted(group.loc[group["rain_mm_1h"] >= 70.0, "station_id"].tolist())
        rows.append({
            "hour_end_hkt": obs_time,
            "any_selected_station_amber": bool(amber),
            "any_selected_station_red": bool(red),
            "any_selected_station_black": bool(black),
            "stations_triggering_amber": ",".join(amber),
            "stations_triggering_red": ",".join(red),
            "stations_triggering_black": ",".join(black),
            "label_basis": "any_selected_station",
        })
    labels = pd.DataFrame(rows)
    if not labels.empty:
        labels = labels.sort_values("hour_end_hkt")
    write_csv(PROCESSED_HKO / "labels_rainstorm_hourly.csv", labels)
    logger.info("build labels_rainstorm_hourly: %d hourly rows", len(labels))
    return len(labels)


def copy_station_metadata(logger):
    source = META_ROOT / "stations.csv"
    target = PROCESSED_HKO / "stations.csv"
    if source.exists():
        shutil.copyfile(source, target)
        logger.info("copied %s -> %s", source, target)


def main():
    ensure_directories()
    logger = setup_logger(name="flatten", log_file="flatten.log")
    logger.info("starting flatten/rebuild of processed tables from raw files")
    rebuild_fnd(logger)
    rebuild_data_gov_hk(logger)
    rebuild_data_gov_hk_rhrread(logger)
    rebuild_hourly_rain(logger)
    rebuild_warning_current(logger)
    rebuild_warning_info(logger)
    rebuild_rhrread(logger)
    rebuild_daily_temp(logger)
    rebuild_daily_rainfall(logger)
    rebuild_ryes(logger)
    rebuild_open_meteo(logger)
    rebuild_cowin(logger)
    build_cowin_labels(logger)
    rebuild_cedd(logger)
    rebuild_warning_history(logger)
    build_warning_windows(logger)
    build_rainstorm_labels(logger)
    copy_station_metadata(logger)
    generate_all(logger=logger)
    logger.info("flatten complete")


if __name__ == "__main__":
    main()
