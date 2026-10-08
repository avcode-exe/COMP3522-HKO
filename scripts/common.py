import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HKT = timezone(timedelta(hours=8))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = PROJECT_ROOT / "raw"
PROCESSED_ROOT = PROJECT_ROOT / "processed"
PROCESSED_HKO = PROCESSED_ROOT / "hko"
PROCESSED_COWIN = PROCESSED_ROOT / "cowin"
PROCESSED_OM = PROCESSED_ROOT / "open_meteo"
PROCESSED_CEDD = PROCESSED_ROOT / "cedd"
META_ROOT = PROJECT_ROOT / "meta"
LOGS_ROOT = PROJECT_ROOT / "logs"

HEADERS = {"User-Agent": "COMP3522-HKO-data-collection/1.0"}

HKO_WEATHER_API = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
HKO_OPENDATA_API = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
HKO_HOURLY_RAIN_API = "https://data.weather.gov.hk/weatherAPI/opendata/hourlyRainfall.php"
CIS_BASE = "https://www.hko.gov.hk/cis/csvfile"
HKO_WARN_DB_PAGE = "https://www.hko.gov.hk/en/wxinfo/climat/warndb/warndb3.shtml"
HKO_WARN_DB_RAINSTORM_DAT = "https://www.hko.gov.hk/dps/wxinfo/climat/warndb/rstorm.dat"
COWIN_BASE = "https://cowin.hku.hk/public/data"
CEDD_RG_BASE = "https://www.ginfo.cedd.gov.hk/geoopendata/Data/RG"
DATA_GOV_HK_ARCHIVE_API = "https://api.data.gov.hk/v1/historical-archive"
HKO_FND_RSS_URL = "https://rss.weather.gov.hk/rss/SeveralDaysWeatherForecast_v2.xml"
HKO_CURRENT_WEATHER_RSS_URL = "https://rss.weather.gov.hk/rss/CurrentWeather.xml"
OM_HISTORICAL_FORECAST_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OM_ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

PROJECT_DATA_START_DATE = "2023-01-01"
PROJECT_DATA_END_DATE = "2025-12-31"


def project_start_year():
    return int(PROJECT_DATA_START_DATE[:4])


def project_end_year():
    if PROJECT_DATA_END_DATE:
        return int(PROJECT_DATA_END_DATE[:4])
    return now_hkt().year


def default_end_date():
    if PROJECT_DATA_END_DATE:
        return datetime.fromisoformat(PROJECT_DATA_END_DATE).date()
    return (now_hkt() - timedelta(days=1)).date()

PSR_VALUES = {"High", "Medium", "Low"}

RAINSTORM_THRESHOLDS_MM = {"amber": 30.0, "red": 50.0, "black": 70.0}


def now_hkt():
    return datetime.now(HKT)


def ensure_directories():
    for path in (RAW_ROOT, PROCESSED_ROOT, PROCESSED_HKO, PROCESSED_COWIN, PROCESSED_OM, PROCESSED_CEDD, META_ROOT, LOGS_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def get_with_retry(url, params=None, max_tries=5, timeout=30, initial_wait=2):
    wait = initial_wait
    last_error = None
    for attempt in range(max_tries):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response
            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = "HTTP {}".format(response.status_code)
                time.sleep(wait)
                wait *= 2
                continue
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            if attempt == max_tries - 1:
                raise
            time.sleep(wait)
            wait *= 2
    raise RuntimeError("request failed after {} attempts: {}".format(max_tries, last_error))


def _raw_filepath(category, filename):
    folder = RAW_ROOT / category / now_hkt().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename


def save_raw_json(category, payload, filename=None):
    if filename is None:
        filename = now_hkt().strftime("%Y%m%d_%H%M%S") + ".json"
    path = _raw_filepath(category, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(path)


def save_raw_text(category, filename, text):
    path = _raw_filepath(category, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def save_raw_bytes(category, filename, data):
    path = _raw_filepath(category, filename)
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def append_to_csv(path, df):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        df.to_csv(path, mode="a", header=False, index=False)
    else:
        df.to_csv(path, index=False)
    return str(path)


def write_csv(path, df):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return str(path)


def write_parquet(path, df):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return str(path)


def append_parquet(path, df):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        existing = pd.read_parquet(path)
        df = pd.concat([existing, df], ignore_index=True)
    df.to_parquet(path, index=False)
    return str(path)


def read_table(path):
    path = Path(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def setup_logger(name="collection", log_file="collection.log"):
    ensure_directories()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOGS_ROOT / log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def log_collection_event(category, status, rows_collected, output_path="", notes=""):
    ensure_directories()
    row = pd.DataFrame([{
        "timestamp_hkt": now_hkt().isoformat(timespec="seconds"),
        "category": category,
        "status": status,
        "rows_collected": int(rows_collected),
        "output_path": output_path,
        "notes": notes,
    }])
    append_to_csv(META_ROOT / "collection_log.csv", row)


def load_stations(selected_only=True):
    path = META_ROOT / "stations.csv"
    df = pd.read_csv(path)
    if selected_only:
        df = df[df["selected"] == True].copy()
    return df
