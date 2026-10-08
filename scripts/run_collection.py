import argparse
import time
from datetime import timedelta

from common import ensure_directories, load_stations, log_collection_event, now_hkt, project_end_year, project_start_year, setup_logger
from collect_hko_daily_rainfall import collect_daily_rainfall
from collect_hko_daily_temp import collect_daily_temperature
from collect_hko_fnd import collect_fnd
from collect_hko_hourly_rain import collect_hourly_rain
from collect_open_meteo import collect_open_meteo
from collect_hko_rhrread import collect_rhrread
from collect_hko_ryes import collect_ryes, collect_ryes_range
from collect_hko_warning_history import collect_warning_history
from collect_hko_warnings import collect_warning_information, collect_warning_summary

LIVE_TASKS = [
    ("hko_fnd", 360, collect_fnd),
    ("hko_hourly_rain", 60, collect_hourly_rain),
    ("hko_warnsum", 30, collect_warning_summary),
    ("hko_warning_info", 30, collect_warning_information),
    ("hko_rhrread", 60, collect_rhrread),
]


def run_live_once(logger):
    logger.info("running live collection pass")
    for name, _interval, func in LIVE_TASKS:
        try:
            func(logger)
        except Exception as exc:
            logger.exception("live task %s failed: %s", name, exc)
            log_collection_event(name, "error", 0, notes=str(exc))
    logger.info("live collection pass complete")


def run_daily(logger, start_year=None, end_year=None, om_start_date=None, om_end_date=None, ryes_start_date=None, ryes_end_date=None):
    logger.info("running daily backfill")
    try:
        collect_daily_temperature(
            data_types=("CLMTEMP", "CLMMAXT", "CLMMINT"),
            station_codes=None,
            start_year=start_year,
            end_year=end_year,
            rformat="json",
            logger=logger,
        )
    except Exception as exc:
        logger.exception("daily task hko_daily_temp failed: %s", exc)
        log_collection_event("hko_daily_temp", "error", 0, notes=str(exc))
    try:
        collect_daily_rainfall(
            station_codes=None,
            start_year=start_year or project_start_year(),
            end_year=end_year,
            logger=logger,
        )
    except Exception as exc:
        logger.exception("daily task hko_daily_rainfall failed: %s", exc)
        log_collection_event("hko_daily_rainfall", "error", 0, notes=str(exc))
    try:
        if ryes_start_date:
            collect_ryes_range(ryes_start_date, ryes_end_date, logger)
        else:
            collect_ryes(date_str=None, logger=logger)
    except Exception as exc:
        logger.exception("daily task hko_ryes failed: %s", exc)
        log_collection_event("hko_ryes", "error", 0, notes=str(exc))
    try:
        collect_warning_history(logger)
    except Exception as exc:
        logger.exception("daily task hko_warning_history failed: %s", exc)
        log_collection_event("hko_warning_history", "error", 0, notes=str(exc))
    try:
        stations = load_stations()
        for _index, row in stations.iterrows():
            try:
                collect_open_meteo(
                    station_id=row["station_id"],
                    start_date=om_start_date,
                    end_date=om_end_date,
                    source="both",
                    logger=logger,
                )
            except Exception as exc:
                logger.exception("daily task open_meteo station=%s failed: %s", row["station_id"], exc)
                log_collection_event("open_meteo", "error", 0, notes="{}: {}".format(row["station_id"], exc))
    except Exception as exc:
        logger.exception("daily task open_meteo failed: %s", exc)
        log_collection_event("open_meteo", "error", 0, notes=str(exc))
    logger.info("daily backfill complete")


def run_loop(logger):
    next_run = {name: now_hkt() for name, _interval, _func in LIVE_TASKS}
    logger.info("starting collection loop (Ctrl+C to stop)")
    try:
        while True:
            now = now_hkt()
            due = [task for task in LIVE_TASKS if now >= next_run[task[0]]]
            for name, interval_minutes, func in due:
                try:
                    func(logger)
                except Exception as exc:
                    logger.exception("loop task %s failed: %s", name, exc)
                    log_collection_event(name, "error", 0, notes=str(exc))
                next_run[name] = now_hkt() + timedelta(minutes=interval_minutes)
            sleep_seconds = min((t - now_hkt()).total_seconds() for t in next_run.values())
            time.sleep(max(sleep_seconds, 1.0))
    except KeyboardInterrupt:
        logger.info("collection loop stopped by user")


def main():
    ensure_directories()
    logger = setup_logger()
    parser = argparse.ArgumentParser(description="COMP3522 HKO data collection orchestrator")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run all live collectors once (default)")
    mode.add_argument("--loop", action="store_true", help="run live collectors on the recommended schedule")
    mode.add_argument("--daily", action="store_true", help="run daily backfill (daily temperature, RYES, Open-Meteo)")
    mode.add_argument("--all", action="store_true", help="run live pass + daily backfill")
    parser.add_argument("--start-year", type=int, default=project_start_year(), help="backfill start year (default {} = project data start)".format(project_start_year()))
    parser.add_argument("--end-year", type=int, default=project_end_year(), help="backfill end year (default project data end)")
    parser.add_argument("--om-start-date", default=None, help="Open-Meteo backfill start date YYYY-MM-DD (default 7 days ago)")
    parser.add_argument("--om-end-date", default=None, help="Open-Meteo backfill end date YYYY-MM-DD (default yesterday)")
    parser.add_argument("--ryes-start-date", default=None, help="RYES backfill start date YYYY-MM-DD (default: yesterday only)")
    parser.add_argument("--ryes-end-date", default=None, help="RYES backfill end date YYYY-MM-DD (default yesterday)")
    args = parser.parse_args()

    if args.loop:
        run_loop(logger)
    elif args.daily:
        run_daily(logger, start_year=args.start_year, end_year=args.end_year, om_start_date=args.om_start_date, om_end_date=args.om_end_date, ryes_start_date=args.ryes_start_date, ryes_end_date=args.ryes_end_date)
    elif args.all:
        run_live_once(logger)
        run_daily(logger, start_year=args.start_year, end_year=args.end_year, om_start_date=args.om_start_date, om_end_date=args.om_end_date, ryes_start_date=args.ryes_start_date, ryes_end_date=args.ryes_end_date)
    else:
        run_live_once(logger)


if __name__ == "__main__":
    main()
