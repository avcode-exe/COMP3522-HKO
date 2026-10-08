# Data Dictionary v0 — COMP3522 HKO Data Collection

Version: 0 (Week 1)
Last updated: 2026-10-06
Owner: COMP3522 project team
Data window: 2023-01-01 to 2025-12-31 (HKT) for all historical backfills

---

## 1. Core definitions

| Term | Definition |
|---|---|
| `issue_date_proxy` | Date when the HKO 9-day forecast snapshot was collected (HKT). Used because the `fnd` API does not provide an explicit issue date. |
| `target_date` | The calendar day being forecast (`forecastDate`, format YYYYMMDD). |
| `lead_time_days` | `target_date - issue_date_proxy`. |
| `snapshot_id` | Unique ID of a forecast snapshot, format `YYYYMMDDTHHMMSS` (collection time, HKT). |
| Amber-equivalent hour | Any selected station records >= 30 mm rainfall in one hour. |
| Red-equivalent hour | Any selected station records >= 50 mm rainfall in one hour. |
| Black-equivalent hour | Any selected station records >= 70 mm rainfall in one hour. |
| Official rainstorm warning | HKO Amber/Red/Black rainstorm warning with issue and cancel time. |
| `label_basis` | `any_selected_station` — territory-level labels are TRUE if any selected station crosses the threshold. |

## 2. Timezone policy

- Main analysis time: **HKT (UTC+8)**.
- HKO timestamps usually include `+08:00` offset (e.g. `2026-10-06T17:15:00+08:00`).
- Store both the original timestamp string and the parsed HKT/UTC values where relevant.
- Open-Meteo requests always use `timezone=Asia/Hong_Kong`.
- HKO AWS rainfall represents the **1-hour period ending at the observation time** (e.g. obsTime 15:00 HKT ≈ rainfall 14:00–15:00 HKT). This assumption is recorded here and must be stated in any analysis.

## 3. Unit policy

- Rainfall: **mm**
- Temperature: **Celsius (C)**
- Relative humidity: **percent**
- Radiation: **microsievert per hour**

## 4. Missing-value policy

- Do **not** impute missing forecast values in Month 1. Record as NA.
- Do **not** invent missing issue dates (use `issue_date_proxy` and document the limitation).
- Do **not** fill missing warning cancel/expire times unless the source is clear.
- AWS rainfall `value == "M"` means maintenance/missing. Keep as missing (`maintenance_or_missing=TRUE`, `rain_mm_1h=NA`). **Never convert "M" to 0.**
- HKO daily temperature completeness column: `C` = complete, `#` = incomplete. Keep the flag; do not drop incomplete rows silently.

## 5. Raw storage rules

- Every raw snapshot is stored exactly as received, **never overwritten**.
- Layout: `raw/<category>/<YYYYMMDD>/<filename>`
- Categories: `hko_fnd`, `hko_rhrread`, `hko_warnsum`, `hko_warning_info`, `hko_hourly_rain`, `hko_daily_temp`, `hko_daily_rainfall`, `hko_ryes`, `hko_warning_history`, `data_gov_hk_9day`, `om_historical_forecast`, `om_historical_weather`, `cowin`, `cedd`
- Raw filenames embed the collection timestamp (`YYYYMMDD_HHMMSS`) so the collection time is recoverable even if the payload lacks it.
- Backfill raw files embed their query metadata in the filename, e.g. `CLMMAXT_KP_2024_20261006_171500.json`, `RYES_20261005_20261006_171500.json`, `historical_forecast_RF024_2026-09-29_2026-10-05_20261006_171500.json`.
- **`processed/` is independent of `raw/`.** It is organised by source (`processed/hko/`, `processed/cowin/`, `processed/open_meteo/`), holds no `raw_path` columns, and every table is fully materialised. You can copy `processed/` on its own for analysis. Provenance is tracked outside the data in `meta/collection_log.csv` and `logs/flatten.log`.

## 6. Processed table schemas

`processed/` is organised by source (`hko/`, `cowin/`, `open_meteo/`) and is self-contained (no `raw/` references). Each folder contains a dynamically auto-generated `README.md` describing its files and columns (`scripts/generate_processed_readmes.py`, also run automatically by `flatten_hko_data.py`).

### `processed/hko/hko_fnd_daily.csv`

| Column | Type | Description |
|---|---|---|
| `snapshot_id` | string | unique snapshot ID (`YYYYMMDDTHHMMSS`) |
| `collected_at_hkt` | datetime | when the API was called (HKT) |
| `issue_date_proxy` | date | date part of collection time |
| `target_date` | date | `forecastDate` |
| `lead_time_days` | integer | `target_date - issue_date_proxy` |
| `week` | string | day of week |
| `forecast_weather_text` | string | raw HKO forecast text |
| `forecast_max_temp_c` / `forecast_max_temp_unit` | float / string | forecast maximum temperature |
| `forecast_min_temp_c` / `forecast_min_temp_unit` | float / string | forecast minimum temperature |
| `forecast_wind` | string | forecast wind text |
| `forecast_max_rh_percent` / `forecast_min_rh_percent` | float | forecast relative humidity |
| `forecast_icon` | integer | HKO forecast icon code |
| `psr` | string | Probability of Significant Rain: High / Medium / Low (categorical — do NOT convert to mm) |
| `general_situation` | string | HKO general situation text |

### `processed/hko/hko_fnd_archive.csv`

Historical HKO 9-day forecast snapshots backfilled from the **data.gov.hk Historical Archive API** (the archived RSS `SeveralDaysWeatherForecast_v2.xml`). Collected 2023-01-01 to 2025-12-31 at 1 snapshot/day by default (`--per-day 4` gets the full ~4/day archive). This is the **Task 1 reliability dataset**, and unlike the live API it carries the **real bulletin issue time**.

| Column | Type | Description |
|---|---|---|
| `issue_datetime_hkt` | datetime | real bulletin issue time (parsed from "Bulletin updated at HH:MM HKT") |
| `archive_time_hkt` | datetime | data.gov.hk archive capture timestamp |
| `issue_date` | date | issue date |
| `target_date` | date | forecast target date |
| `lead_time_days` | integer | `target_date - issue_date` (0–9) |
| `week` | string | day of week |
| `forecast_weather_text` | string | forecast weather text |
| `forecast_wind` | string | forecast wind text |
| `forecast_max_temp_c` / `forecast_min_temp_c` | integer | forecast temperature range (C) |
| `forecast_max_rh_percent` / `forecast_min_rh_percent` | integer | forecast R.H. range (%) |
| `psr` | string | Probability of Significant Rain (raw text) |
| `general_situation` | string | general situation text |
| `source` | string | `data_gov_hk_9day` |

### `processed/hko/hko_hourly_rain.csv`

| Column | Type | Description |
|---|---|---|
| `obs_time_hkt` | datetime | HKO observation time (end of 1-hour rainfall window) |
| `station_id` | string | AWS station ID (e.g. RF024) |
| `station_name` | string | AWS station name |
| `value_raw` | string | original value, may be `"M"` |
| `rain_mm_1h` | float | numeric rainfall in mm (NA if `"M"`) |
| `unit` | string | should be `mm` |
| `maintenance_or_missing` | boolean | TRUE if value is `"M"` |
| `station_amber_flag` / `station_red_flag` / `station_black_flag` | boolean | station-level rainstorm labels (>= 30 / 50 / 70 mm) |

### `processed/hko/hko_warning_current.csv`

| Column | Type | Description |
|---|---|---|
| `collected_at_hkt` | datetime | collection time |
| `warning_key` | string | top-level key, e.g. `WRAIN` |
| `name` / `code` / `type` | string | warning name, code (e.g. `WRAINR`), type (e.g. `Red`) |
| `action_code` | string | ISSUE / CANCEL / UPDATE etc. |
| `issue_time` / `update_time` / `expire_time` | datetime | ISO 8601 with +08:00; `expire_time` may be missing |

### `processed/hko/hko_warning_info.csv`

| Column | Type | Description |
|---|---|---|
| `collected_at_hkt` | datetime | collection time |
| `warning_statement_code` | string | e.g. `WRAIN` |
| `subtype` | string | e.g. `WRAINA` / `WRAINR` / `WRAINB` |
| `update_time` | datetime | warning update time |
| `contents_text` | string | joined warning text lines |

### `processed/hko/hko_rainstorm_warning_history.csv`

Rainstorm warning issue/cancel times scraped from the HKO warning database (`rstorm.dat`), filtered to the project window (2023-01-01 to 2025-12-31).

| Column | Type | Description |
|---|---|---|
| `warning_id` | string | unique event ID, e.g. `RS-20200521T0230-R` |
| `episode_id` | string | linked escalation episode, e.g. `RS-EP-20200521T0110` |
| `episode_warning_index` | integer | position within the episode (1 = first color issued) |
| `warning_color` | string | Amber / Red / Black |
| `warning_code` | string | WRAINA / WRAINR / WRAINB |
| `issue_datetime_hkt` | datetime | official issue time (ISO 8601 +08:00) |
| `cancel_datetime_hkt` | datetime | official cancel time (ISO 8601 +08:00) |
| `duration_minutes` | integer | cancel minus issue |
| `year` / `month` | integer | issue year / month |
| `source_url` | string | `https://www.hko.gov.hk/dps/wxinfo/climat/warndb/rstorm.dat` |
| `raw_text` | string | original tab-delimited record |
| `notes` | string | e.g. hour-24 normalization |
| `collected_at_hkt` | datetime | scrape time |

Episode rule: a record continues the current episode when its `issue_datetime_hkt` equals the previous record's `cancel_datetime_hkt` (Amber -> Red -> Black escalation); otherwise a new episode starts. Each color issue is still a separate row (plan §10.2 Option A plus `episode_id`).

### `processed/hko/labels_warning_windows.csv`

Task 3(b) prediction-window labels over an hourly anchor grid (plan §25.2). For each hour the label is 1 if any rainstorm warning is **issued within the next N hours** — the leakage-safe framing where features may only use data at or before the anchor.

| Column | Type | Description |
|---|---|---|
| `anchor_time_hkt` | datetime | prediction anchor time (hourly, HKT) |
| `label_warning_3h` | integer | 1 if a warning is issued in (anchor, anchor+3h] |
| `label_warning_6h` | integer | 1 if a warning is issued in (anchor, anchor+6h] |
| `label_warning_12h` | integer | 1 if a warning is issued in (anchor, anchor+12h] |
| `next_warning_issue_hkt` | datetime | issue time of the next warning after the anchor (reference only) |
| `hours_to_next_warning` | float | hours from the anchor to that issue (reference only) |
| `next_warning_color` | string | Amber / Red / Black of the next warning (reference only) |
| `label_basis` | string | `warning_issued_within_next_Nh` |

Grid: hourly from `PROJECT_DATA_START_DATE` (2023-01-01) to `PROJECT_DATA_END_DATE` (2025-12-31). The `next_warning_*` / `hours_to_next_warning` columns are provided for convenience only and must **not** be used as predictive features (they leak the future). Generated in the processing layer (`flatten_hko_data.py`).

### `processed/hko/hko_rhrread.csv` (long format)

| Column | Type | Description |
|---|---|---|
| `collected_at_hkt` | datetime | collection time |
| `element` | string | `rainfall_max_1h` / `temperature` / `humidity` / `uvindex` |
| `place` | string | district or station name |
| `value` / `unit` | float / string | observation value and unit |
| `record_time` | datetime | observation record time (if provided) |
| `extra` | string | `main` maintenance flag for rainfall; UV description for uvindex |

### `processed/hko/hko_rhrread_summary.csv`

One row per collection: `collected_at_hkt`, `update_time`, `icon`, `icon_update_time`, `rainfall_from_00_to_12_mm`, `mintemp_from_00_to_09_c`, `rainfall_last_month_mm`, `rainfall_january_to_last_month_mm`, `warning_message`, `tc_message`.

### `processed/hko/hko_rhrread_archive.csv` (long format)

Historical **Current Weather Report** backfilled from the **data.gov.hk Historical Archive API** (the archived RSS `https://rss.weather.gov.hk/rss/CurrentWeather.xml`, hourly since ~2019-07). This is the historical equivalent of `rhrread`: HKO HQ air temperature + relative humidity, plus air temperature at ~27 stations, each with a real bulletin issue time. Collected 2023-01-01 → 2025-12-31 at 1 snapshot/day (`--per-day 24` for hourly).

| Column | Type | Description |
|---|---|---|
| `issue_datetime_hkt` | datetime | bulletin issue time (parsed from "Bulletin updated at HH:MM HKT") |
| `archive_time_hkt` | datetime | data.gov.hk archive capture time |
| `element` | string | `temperature` / `humidity` |
| `place` | string | station name (Hong Kong Observatory, King's Park, …) |
| `value` | integer | temperature (C) or relative humidity (%) |
| `unit` | string | `C` / `percent` |
| `source` | string | `data_gov_hk_rhrread` |

### `processed/hko/hko_rhrread_archive_summary.csv`

One row per snapshot: `issue_datetime_hkt`, `archive_time_hkt`, `warning_text` ("Please be reminded that: …", may be NA), `station_count`, `source`.

### `processed/hko/hko_daily_temp.csv` (long format)

| Column | Type | Description |
|---|---|---|
| `station_code` | string | temperature station code (e.g. `KP`) |
| `date` | date | observation date |
| `data_type` | string | `CLMTEMP` / `CLMMAXT` / `CLMMINT` |
| `value_c` | float | temperature in Celsius |
| `unit` | string | `C` |
| `data_completeness` | string | `C` = complete, `#` = incomplete |
| `station_title_en` | string | English dataset title from API |
| `source_api` | string | `hko_opendata_api` |

### `processed/hko/hko_daily_temp_wide.csv`

Pivoted: `station_code`, `date`, `mean_temp_c`, `max_temp_c`, `min_temp_c`.

### `processed/hko/hko_daily_rainfall.csv` (CIS, long format)

Per-station daily total rainfall from the HKO Climatological Information Services (CIS) daily CSV downloads.

| Column | Type | Description |
|---|---|---|
| `station_code` | string | CIS/climate station code (e.g. `KP`) |
| `date` | date | observation date |
| `element` | string | `daily_total_rainfall` |
| `value_raw` | string | original value, may be `Trace` |
| `rain_mm_1d` | float | daily rainfall in mm (`Trace` -> 0.0, non-numeric -> NA) |
| `unit` | string | `mm` |
| `data_completeness` | string | `C` = complete, `#` = incomplete |
| `is_trace` | boolean | TRUE when the raw value is `Trace` (< 0.05 mm) |
| `source_api` | string | `hko_cis_csvfile` |

Coverage (2023–2026): available for KP, VP1, TKL, HPV, SSP, SHA, LFS, PLC, HKA. Not available (HTTP 404) for NGP, HKS, STY, KTG, SE1 — those stations are skipped and logged as `skip` in `collection_log.csv`.

### `processed/cowin/cowin_hourly_rainfall.parquet` (HKU CoWIN, long format)

Quality-controlled hourly rainfall from the HKU Community Weather Information Network (CoWIN), the rescue source for historical hourly rainfall (2023-2025). Downloaded as yearly zips from `https://cowin.hku.hk/public/data/{YEAR}.zip` and parsed from `60rf.csv`. Stored as **parquet** (~1.03M rows).

| Column | Type | Description |
|---|---|---|
| `obs_time_hkt` | datetime | observation time (ISO 8601 +08:00; CoWIN obstime is local standard time = HKT) |
| `station_id` | string | CoWIN station ID (numeric community/school network) |
| `rain_mm_1h` | float | rainfall in the past 60 minutes (mm) |
| `qcscore` | string | CoWIN QC flag: G = good, M / F / X = flagged |
| `year` | integer | year |
| `source` | string | `hk_cowin` |

Coverage: 2023-2025, 40 stations, 549,702 rows. This is a **community/school network**, not the official HKO AWS network — it is an independent hourly rainfall source used for Task 3(a) labels, not a replacement for HKO observations. Citation required: acknowledge CoWIN and Lam et al. (2021).

### `processed/cowin/cowin_stations.parquet`

CoWIN station metadata from each year's `available.csv`: `station_id` (string), `station_name`, `latitude`, `longitude`, `elevation`, monthly coverage columns (Jan-Dec), `year`, `source`, and `metadata_missing` (TRUE for the few rainfall station IDs absent from `available.csv`). Stored as **parquet** to keep `station_id` a string so it joins to `cowin_hourly_rainfall.parquet` out of the box.

### `processed/cowin/cowin_labels_rainstorm_hourly.csv` (territory-level, CoWIN-based)

| Column | Type | Description |
|---|---|---|
| `hour_end_hkt` | datetime | hour ending timestamp |
| `any_station_amber` / `_red` / `_black` | boolean | TRUE if any CoWIN station (QC score G) crosses 30 / 50 / 70 mm |
| `n_amber` / `n_red` / `n_black` | integer | number of stations crossing the threshold |
| `label_basis` | string | `any_cowin_station_qc_G` |

This is distinct from `labels_rainstorm_hourly.csv` (HKO forward-collected basis). It enables historical Task 3(a) labels despite HKO's missing hourly archive.

### `processed/cedd/cedd_daily_rainfall.parquet`

Daily total rainfall from the CEDD Geotechnical Engineering Office (GEO) raingauge network (the government landslide-warning gauges). Downloaded as yearly CSVs (`Data/RG/{year}.csv`), collected 2023-2026.

| Column | Type | Description |
|---|---|---|
| `date` | date | observation date |
| `station_id` | string | CEDD raingauge number (e.g. `H01`) |
| `station_name` | string | gauge location name |
| `easting` / `northing` | integer | HK1980 grid coordinates |
| `value_raw` | string | original value (may end with `#` = incomplete, or be `***` = missing) |
| `rain_mm_1d` | float | daily rainfall (mm); `Trace` -> 0.0, `***` -> NA |
| `is_trace` | boolean | TRUE if the raw value is `Trace` |
| `is_incomplete` | boolean | TRUE if the raw value carried a `#` completeness marker |
| `source` | string | `cedd_geo` |

Coverage: **90 gauges**, 2023-01-01 to 2026-06-30 (2026 is year-to-date). This is a **daily** dataset (not hourly) — a denser, government-verified complement to the HKO CIS daily rainfall (`hko_daily_rainfall`, 9 stations).

### `processed/cedd/cedd_stations.csv`

CEDD raingauge metadata: `station_id`, `station_name`, `easting`, `northing`, `source`.

### `processed/hko/hko_daily_weather.csv` (RYES, long format)

| Column | Type | Description |
|---|---|---|
| `report_date` | string | `ReportTimeInfoDate` (YYYYMMDD, the reported day) |
| `bulletin_date` / `bulletin_time` | string | `BulletinDate` / `BulletinTime` (issue of the bulletin) |
| `location` | string | location name (e.g. `King's Park`, `Hong Kong Observatory`) |
| `element` | string | e.g. `max_temp_c`, `min_temp_c`, `rainfall_mm`, `max_rh_percent`, `sunshine_hours`, `radiation_microsieverts_per_hour` |
| `value` / `unit` | float / string | value and unit |
| `source_api` | string | `hko_opendata_api` |

### `processed/open_meteo/om_historical_forecast_hourly.parquet` / `om_historical_weather_hourly.parquet`

Stored as **parquet** (not CSV) because each holds ~830k rows; the plan recommends parquet for these large tables.

| Column | Type | Description |
|---|---|---|
| `station_id` | string | linked HKO station (or `custom_lat_lon`) |
| `latitude` / `longitude` | float | requested station coordinates (the query point) |
| `grid_latitude` / `grid_longitude` | float | Open-Meteo model grid-cell coordinates returned by the API (the API snaps the query to its grid) |
| `timezone` | string | `Asia/Hong_Kong` |
| `source_api` | string | `historical_forecast` / `historical_weather` |
| `issue_time_known` | boolean | always FALSE — Open-Meteo does not expose the forecast run/issue time |
| `time_hkt` | datetime | local time (Asia/Hong_Kong) |
| `precipitation`, `temperature_2m`, `relative_humidity_2m`, `cloud_cover`, `wind_speed_10m`, `surface_pressure` | float | hourly variables (only requested variables are present) |

### `processed/open_meteo/om_historical_forecast_daily.parquet` / `om_historical_weather_daily.parquet`

Stored as **parquet**. Same station/source columns plus `date`, `precipitation_sum`, `temperature_2m_max`, `temperature_2m_min`.

### `processed/hko/labels_rainstorm_hourly.csv` (territory-level)

| Column | Type | Description |
|---|---|---|
| `hour_end_hkt` | datetime | hour ending timestamp |
| `any_selected_station_amber` / `_red` / `_black` | boolean | TRUE if any selected station crosses the threshold |
| `stations_triggering_amber` / `_red` / `_black` | string | comma-separated station IDs |
| `label_basis` | string | `any_selected_station` |

### `processed/hko/stations.csv`

Copy of `meta/stations.csv` (station metadata).

## 7. Station metadata (`meta/stations.csv`)

`station_id` (AWS rainfall ID), `station_name`, `region`, `temperature_station_code`, `rainfall_station_id`, `latitude`, `longitude`, `station_type`, `selected_for_task1`, `selected_for_task3`, `selected`, `coordinates_verified`, `notes`.

**Coordinate status:** all coordinates are currently **approximate** (`coordinates_verified=FALSE`). Verify against the data.gov.hk dataset `hk-hko-rss-network-of-weather-stations-in-hong-kong` (CSDI geoportal datasetId `hko_rcd_1634995599372_15888`) before relying on Open-Meteo station matching. AWS rainfall IDs were verified against the live `hourlyRainfall.php` response on 2026-10-06.

## 8. Known API limitations (must be stated in the data-collection report)

1. **HKO 9-day forecast API may not provide an explicit issue date.** Live `fnd` snapshots use the collection time as an issue-date proxy (`issue_date_proxy`). Historical forecasts with **real issue times** are backfilled from the data.gov.hk Historical Archive API into `hko_fnd_archive` (2023–2025); see item 7.
2. **HKO hourly rainfall API is latest-only and HKO publishes no historical hourly archive.** The API gives provisional past-hour AWS rainfall (the `date` parameter is ignored). Historical hourly rainfall was rescued from the HKU **CoWIN** network (`cowin_hourly_rainfall`, 2023-2025, 40 QC'd community stations) and used to build `cowin_labels_rainstorm_hourly`. Because CoWIN is a community network (not HKO AWS), Task 3(a) labels are reported with `label_basis=any_cowin_station_qc_G`; HKO forward-collected hours continue on the `any_selected_station` basis. Per-station HKO **daily** rainfall (`hko_daily_rainfall`) remains a secondary fallback, and a denser government-verified daily source is also collected from the **CEDD GEO raingauge network** (90 gauges, 2023-2026 → `cedd_daily_rainfall`). Neither provides hourly resolution.
3. **HKO forecasts are not fully station-specific.** The 9-day forecast is a general product; we compare it against selected station observations and report results by region.
4. **Open-Meteo is not official.** Open-Meteo data are used only as an independent cross-check. They are not used to define official HKO rainstorm warnings and do not replace HKO station observations. Open-Meteo does not expose forecast issue/run time (`issue_time_known=FALSE`). **Verified 2026-10-07:** in this configuration the Historical Forecast API returns values identical to the Archive API for 2023-2025 (`om_historical_forecast_*` duplicates `om_historical_weather_*`), i.e. both are reanalysis rather than genuine per-lead-time forecasts. Genuine archived forecast runs would require Open-Meteo's Previous Runs API (`previous-runs-api.open-meteo.com`).
5. **Rainstorm warnings escalate (Amber -> Red -> Black).** Current collectors store each color issue as a separate warning event; an `episode_id` linking escalations may be added later.
6. **Historical rainstorm warning issue/cancel times** are not available from the current warning APIs (they only expose current warnings). They are instead scraped from the HKO warning database tab-delimited file `https://www.hko.gov.hk/dps/wxinfo/climat/warndb/rstorm.dat` (page `warndb3.shtml`) into `processed/hko_rainstorm_warning_history.csv`. The raw `.dat` holds 1998–present; the processed table is filtered to 2023-01-01 to 2025-12-31 per the project window.
7. **Historical 9-day forecasts ARE available** via the **data.gov.hk Historical Archive API** (`list-file-versions` + `get-file` for the RSS resource `https://rss.weather.gov.hk/rss/SeveralDaysWeatherForecast_v2.xml`), from at least 2023-01 at ~4 snapshots/day, each carrying a **real bulletin issue time**. Collected into `hko_fnd_archive` by `scripts/collect_data_gov_hk_history.py`. (This supersedes the earlier assumption that data.gov.hk had no archive.)
8. **Historical Current Weather Report (`rhrread` equivalent) IS available** via the same Historical Archive API for the RSS `https://rss.weather.gov.hk/rss/CurrentWeather.xml` (dataset `hk-hko-rss-current-weather-report`, hourly since ~2019-07): HKO HQ air temperature + humidity and ~27 station temperatures, with real issue times. Collected into `hko_rhrread_archive` by `scripts/collect_data_gov_hk_rhrread.py`. (Note: the archive does **not** track the `rhrread` JSON API URL — only the RSS — so a dedicated parser is used.)

## 9. Collection schedule

| Task | Source | Frequency |
|---|---|---|
| AWS hourly rainfall | `hourlyRainfall.php` | every hour |
| Warning summary | `warnsum` | every 30 min (5–10 min during rainstorms) |
| Warning information | `warningInfo` | every 30 min (5–10 min during rainstorms) |
| 9-day forecast snapshot | `fnd` | every 6 hours (00:05 / 06:05 / 12:05 / 18:05 HKT); minimum once daily |
| Current weather report | `rhrread` | every hour |
| Daily temperature backfill | `CLMTEMP`/`CLMMAXT`/`CLMMINT` | once daily (or ad hoc) |
| RYES daily report | `RYES` | after 01:30 HKT next day |
| Open-Meteo backfill | Historical Forecast / Archive API | once daily batch |

## 10. Leakage policy (Task 3(b))

For a warning issued at `T_issue`, a model predicting the warning `L` hours ahead may only use features available at or before `T_issue - L`. Never allow features from `T_issue - 1h` to predict a warning issued at `T_issue` unless the task is nowcasting and the lead time is clearly stated.
