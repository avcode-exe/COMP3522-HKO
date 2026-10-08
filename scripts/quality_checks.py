import argparse
from datetime import timedelta

import pandas as pd

from common import (
    LOGS_ROOT,
    PROCESSED_CEDD,
    PROCESSED_COWIN,
    PROCESSED_HKO,
    PROCESSED_OM,
    PSR_VALUES,
    ensure_directories,
    now_hkt,
    setup_logger,
)


def _load(path):
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None


def check_forecast(report):
    df = _load(PROCESSED_HKO / "hko_fnd_daily.csv")
    if df is None or df.empty:
        report.append(("hko_fnd_daily", "SKIP", "processed/hko/hko_fnd_daily.csv not available yet"))
        return
    issues = []
    duplicates = df.duplicated(subset=["issue_date_proxy", "target_date"], keep=False).sum()
    if duplicates:
        issues.append("{} duplicated (issue_date_proxy, target_date) pairs".format(duplicates))
    invalid_dates = df["target_date"].isna().sum()
    if invalid_dates:
        issues.append("{} rows with invalid/missing target_date".format(invalid_dates))
    non_numeric_max = pd.to_numeric(df["forecast_max_temp_c"], errors="coerce").isna().sum()
    non_numeric_min = pd.to_numeric(df["forecast_min_temp_c"], errors="coerce").isna().sum()
    if non_numeric_max:
        issues.append("{} rows with non-numeric forecast_max_temp_c".format(non_numeric_max))
    if non_numeric_min:
        issues.append("{} rows with non-numeric forecast_min_temp_c".format(non_numeric_min))
    bad_psr = (~df["psr"].isin(PSR_VALUES)).sum()
    if bad_psr:
        issues.append("{} rows with PSR outside High/Medium/Low".format(bad_psr))
    lead = pd.to_numeric(df["lead_time_days"], errors="coerce")
    if lead.notna().any():
        issues.append("lead_time_days range: {} to {} days".format(int(lead.min()), int(lead.max())))
    coverage = df.groupby("target_date")["issue_date_proxy"].nunique()
    issues.append("target_dates={}, snapshots={}, distinct issue dates per target: min={} max={}".format(
        df["target_date"].nunique(), df["snapshot_id"].nunique(), int(coverage.min()), int(coverage.max())))
    status = "WARN" if any("duplicated" in i or "invalid" in i or "non-numeric" in i or "outside" in i for i in issues) else "OK"
    report.append(("hko_fnd_daily", status, "; ".join(issues)))


def check_fnd_archive(report):
    df = _load(PROCESSED_HKO / "hko_fnd_archive.csv")
    if df is None or df.empty:
        report.append(("hko_fnd_archive", "SKIP", "processed/hko/hko_fnd_archive.csv not available yet"))
        return
    issues = []
    dup = int(df.duplicated(subset=["issue_datetime_hkt", "target_date"]).sum())
    if dup:
        issues.append("{} duplicate (issue,target)".format(dup))
    bad_order = int((pd.to_numeric(df["forecast_max_temp_c"], errors="coerce") < pd.to_numeric(df["forecast_min_temp_c"], errors="coerce")).sum())
    if bad_order:
        issues.append("{} rows max<min".format(bad_order))
    lead = pd.to_numeric(df["lead_time_days"], errors="coerce")
    issues.append("snapshots={}, rows={}, target_dates={}, lead={}-{}, issue {} .. {}".format(
        df["issue_datetime_hkt"].nunique(), len(df), df["target_date"].nunique(),
        int(lead.min()), int(lead.max()), df["issue_datetime_hkt"].min(), df["issue_datetime_hkt"].max()))
    report.append(("hko_fnd_archive", "WARN" if (dup or bad_order) else "OK", "; ".join(issues)))


def check_rhrread_archive(report):
    df = _load(PROCESSED_HKO / "hko_rhrread_archive.csv")
    if df is None or df.empty:
        report.append(("hko_rhrread_archive", "SKIP", "processed/hko/hko_rhrread_archive.csv not available yet"))
        return
    issues = ["snapshots={}, rows={}, stations={}, elements={}, range {} .. {}".format(
        df["issue_datetime_hkt"].nunique(), len(df), df["place"].nunique(),
        sorted(df["element"].unique()), df["issue_datetime_hkt"].min(), df["issue_datetime_hkt"].max())]
    report.append(("hko_rhrread_archive", "OK", "; ".join(issues)))


def check_hourly_rain(report):
    df = _load(PROCESSED_HKO / "hko_hourly_rain.csv")
    if df is None or df.empty:
        report.append(("hko_hourly_rain", "SKIP", "processed/hko/hko_hourly_rain.csv not available yet"))
        return
    issues = []
    bad_units = (df["unit"] != "mm").sum()
    if bad_units:
        issues.append("{} rows with unit != mm".format(bad_units))
    numeric = pd.to_numeric(df["rain_mm_1h"], errors="coerce")
    negative = (numeric < 0).sum()
    if negative:
        issues.append("{} rows with negative rainfall".format(negative))
    impossible = (numeric > 500).sum()
    if impossible:
        issues.append("{} rows with rainfall > 500 mm/h (check validity)".format(impossible))
    maintenance = df["maintenance_or_missing"].sum()
    issues.append("maintenance/missing (M) values: {} (kept as missing, NOT zero)".format(int(maintenance)))
    issues.append("stations={}, obs_times={}, range {} to {}".format(
        df["station_id"].nunique(), df["obs_time_hkt"].nunique(), df["obs_time_hkt"].min(), df["obs_time_hkt"].max()))
    recent = df[df["obs_time_hkt"] >= (now_hkt() - timedelta(days=1)).isoformat()]
    if not recent.empty:
        per_station = recent.groupby("station_id")["obs_time_hkt"].nunique()
        gaps = (per_station < 20).sum()
        if gaps:
            issues.append("{} stations have <20 distinct hours in the last 24h".format(gaps))
    status = "WARN" if any("rows with" in i for i in issues) else "OK"
    report.append(("hko_hourly_rain", status, "; ".join(issues)))


def check_cowin(report):
    path = PROCESSED_COWIN / "cowin_hourly_rainfall.parquet"
    if not path.exists() or path.stat().st_size == 0:
        report.append(("cowin_hourly_rainfall", "SKIP", "not collected yet"))
        return
    df = pd.read_parquet(path)
    if df.empty:
        report.append(("cowin_hourly_rainfall", "SKIP", "empty"))
        return
    issues = []
    numeric = pd.to_numeric(df["rain_mm_1h"], errors="coerce")
    negative = int((numeric < 0).sum())
    if negative:
        issues.append("{} rows with negative rainfall".format(negative))
    n_amber = int((numeric >= 30).sum())
    issues.append("years={}, stations={}, rows={}, qc={}, hours_ge_30mm={}".format(
        sorted(int(y) for y in df["year"].unique()), df["station_id"].nunique(), len(df),
        dict(df["qcscore"].value_counts()), n_amber))
    status = "WARN" if negative else "OK"
    report.append(("cowin_hourly_rainfall", status, "; ".join(issues)))


def check_cedd(report):
    path = PROCESSED_CEDD / "cedd_daily_rainfall.parquet"
    if not path.exists() or path.stat().st_size == 0:
        report.append(("cedd_daily_rainfall", "SKIP", "not collected yet"))
        return
    df = pd.read_parquet(path)
    if df.empty:
        report.append(("cedd_daily_rainfall", "SKIP", "empty"))
        return
    issues = []
    numeric = pd.to_numeric(df["rain_mm_1d"], errors="coerce")
    negative = int((numeric < 0).sum())
    if negative:
        issues.append("{} rows with negative rainfall".format(negative))
    issues.append("gauges={}, dates={} to {}, rows={}, incomplete={}, max_mm={:.1f}".format(
        df["station_id"].nunique(), df["date"].min(), df["date"].max(), len(df),
        int(df["is_incomplete"].sum()), numeric.max()))
    report.append(("cedd_daily_rainfall", "WARN" if negative else "OK", "; ".join(issues)))


def check_daily_temp(report):
    df = _load(PROCESSED_HKO / "hko_daily_temp.csv")
    if df is None or df.empty:
        report.append(("hko_daily_temp", "SKIP", "processed/hko/hko_daily_temp.csv not available yet"))
        return
    issues = []
    numeric = pd.to_numeric(df["value_c"], errors="coerce")
    impossible = ((numeric < -10) | (numeric > 45)).sum()
    if impossible:
        issues.append("{} rows with temperature outside [-10, 45] C".format(int(impossible)))
    issues.append("stations={}, dates={} to {}, rows={}".format(
        df["station_code"].nunique(), df["date"].min(), df["date"].max(), len(df)))
    per_station_type = df.groupby(["station_code", "data_type"])["date"].nunique()
    issues.append("min series length per station/type: {} days".format(int(per_station_type.min())))
    status = "WARN" if impossible else "OK"
    report.append(("hko_daily_temp", status, "; ".join(issues)))


def check_daily_rainfall(report):
    df = _load(PROCESSED_HKO / "hko_daily_rainfall.csv")
    if df is None or df.empty:
        report.append(("hko_daily_rainfall", "SKIP", "processed/hko/hko_daily_rainfall.csv not available yet"))
        return
    issues = []
    bad_units = (df["unit"] != "mm").sum()
    if bad_units:
        issues.append("{} rows with unit != mm".format(bad_units))
    numeric = pd.to_numeric(df["rain_mm_1d"], errors="coerce")
    negative = (numeric < 0).sum()
    if negative:
        issues.append("{} rows with negative rainfall".format(negative))
    trace_days = int(df["is_trace"].sum())
    issues.append("stations={}, dates={} to {}, trace days={}".format(
        df["station_code"].nunique(), df["date"].min(), df["date"].max(), trace_days))
    status = "WARN" if (bad_units or negative) else "OK"
    report.append(("hko_daily_rainfall", status, "; ".join(issues)))


def check_warnings(report):
    df = _load(PROCESSED_HKO / "hko_warning_current.csv")
    if df is None or df.empty:
        report.append(("hko_warning_current", "SKIP", "no warning snapshots collected yet (empty warnsum is normal when no warnings are active)"))
        return
    issues = []
    invalid_issue = pd.to_datetime(df["issue_time"], errors="coerce", utc=True).isna().sum()
    if invalid_issue:
        issues.append("{} rows with unparseable issue_time".format(int(invalid_issue)))
    no_expire = df["expire_time"].isna().sum()
    issues.append("rows without expire_time: {}".format(int(no_expire)))
    issues.append("action_codes={}".format(sorted(df["action_code"].dropna().unique().tolist())))
    issues.append("warning_keys={}".format(sorted(df["warning_key"].dropna().unique().tolist())))
    status = "WARN" if invalid_issue else "OK"
    report.append(("hko_warning_current", status, "; ".join(issues)))


def check_warning_history(report):
    df = _load(PROCESSED_HKO / "hko_rainstorm_warning_history.csv")
    if df is None or df.empty:
        report.append(("hko_rainstorm_warning_history", "SKIP", "processed/hko/hko_rainstorm_warning_history.csv not available yet"))
        return
    issues = []
    issue_ts = pd.to_datetime(df["issue_datetime_hkt"], errors="coerce", utc=True)
    cancel_ts = pd.to_datetime(df["cancel_datetime_hkt"], errors="coerce", utc=True)
    bad_order = int((cancel_ts <= issue_ts).sum())
    if bad_order:
        issues.append("{} rows with cancel <= issue".format(bad_order))
    bad_colors = int((~df["warning_color"].isin(["Amber", "Red", "Black"])).sum())
    if bad_colors:
        issues.append("{} rows with unexpected warning_color".format(bad_colors))
    issues.append("events={}, episodes={}, colors={}, range {} to {}".format(
        len(df), df["episode_id"].nunique(),
        dict(df["warning_color"].value_counts()),
        df["issue_datetime_hkt"].min(), df["issue_datetime_hkt"].max()))
    status = "WARN" if (bad_order or bad_colors) else "OK"
    report.append(("hko_rainstorm_warning_history", status, "; ".join(issues)))


def check_warning_windows(report):
    df = _load(PROCESSED_HKO / "labels_warning_windows.csv")
    if df is None or df.empty:
        report.append(("labels_warning_windows", "SKIP", "not generated yet"))
        return
    issues = []
    bad = int(((df["label_warning_3h"] > df["label_warning_6h"]) | (df["label_warning_6h"] > df["label_warning_12h"])).sum())
    if bad:
        issues.append("{} rows violate 3h<=6h<=12h".format(bad))
    issues.append("anchors={}, positives 3h/6h/12h={}/{}/{}, range {} .. {}".format(
        len(df), int(df["label_warning_3h"].sum()), int(df["label_warning_6h"].sum()), int(df["label_warning_12h"].sum()),
        df["anchor_time_hkt"].min(), df["anchor_time_hkt"].max()))
    report.append(("labels_warning_windows", "WARN" if bad else "OK", "; ".join(issues)))


def check_labels(report):
    df = _load(PROCESSED_HKO / "labels_rainstorm_hourly.csv")
    if df is None or df.empty:
        report.append(("labels_rainstorm_hourly", "SKIP", "no hourly rainfall data to label yet"))
        return
    issues = []
    issues.append("hours={}, amber={}, red={}, black={}".format(
        len(df), int(df["any_selected_station_amber"].sum()),
        int(df["any_selected_station_red"].sum()),
        int(df["any_selected_station_black"].sum())))
    report.append(("labels_rainstorm_hourly", "OK", "; ".join(issues)))


def check_open_meteo(report):
    for name in ("om_historical_forecast_hourly", "om_historical_weather_hourly"):
        path = PROCESSED_OM / (name + ".parquet")
        if not path.exists():
            report.append((name, "SKIP", "processed/{}.parquet not available yet".format(name)))
            continue
        df = pd.read_parquet(path)
        issues = ["stations={}, hours={} to {}".format(
            df["station_id"].nunique(), df["time_hkt"].min(), df["time_hkt"].max())]
        report.append((name, "OK", "; ".join(issues)))


def check_leakage(report):
    report.append((
        "leakage_policy",
        "INFO",
        "Task 3(b) models must only use features available at or before T_issue - lead_time. "
        "Never use features from T_issue - 1h to predict a warning issued at T_issue unless the task is nowcasting with stated lead time.",
    ))


def run_quality_checks(output_path=None):
    ensure_directories()
    logger = setup_logger(name="quality", log_file="quality_checks.log")
    report = []
    check_forecast(report)
    check_fnd_archive(report)
    check_rhrread_archive(report)
    check_hourly_rain(report)
    check_cowin(report)
    check_cedd(report)
    check_daily_temp(report)
    check_daily_rainfall(report)
    check_warnings(report)
    check_warning_history(report)
    check_warning_windows(report)
    check_labels(report)
    check_open_meteo(report)
    check_leakage(report)

    lines = []
    lines.append("HKO data quality checks - {}".format(now_hkt().isoformat(timespec="seconds")))
    lines.append("=" * 80)
    warn_count = 0
    for table, status, detail in report:
        lines.append("[{:<6}] {:<28} {}".format(status, table, detail))
        if status == "WARN":
            warn_count += 1
    lines.append("=" * 80)
    lines.append("{} warning(s)".format(warn_count))
    text = "\n".join(lines)
    print(text)

    if output_path is None:
        output_path = LOGS_ROOT / ("quality_checks_{}.txt".format(now_hkt().strftime("%Y%m%d_%H%M%S")))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    logger.info("quality checks written to %s (%d warnings)", output_path, warn_count)
    return report, warn_count


def main():
    parser = argparse.ArgumentParser(description="Run data quality checks on processed tables")
    parser.parse_args()
    run_quality_checks()


if __name__ == "__main__":
    main()
