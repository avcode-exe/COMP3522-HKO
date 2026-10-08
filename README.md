# COMP3522-HKO — Data Collection Repository

Data collection pipeline for the COMP3522 course project (HKO weather forecast reliability, validity, and rainstorm warning prediction).

- **Primary source:** Hong Kong Observatory (HKO) Open Data APIs
- **Secondary sources:** data.gov.hk Historical Archive API, HKU CoWIN, CEDD GEO
- **Independent cross-check only:** Open-Meteo (never ground truth)
- **Plan:** `data-collection-plan.md`
- **Schemas & policies:** `meta/data_dictionary_v0.md`

## Repository layout

```text
raw/                     raw snapshots, never overwritten (raw/<category>/<YYYYMMDD>/<file>)
processed/               self-contained analysis tables, nested by source:
                         processed/hko/, processed/cowin/, processed/open_meteo/, processed/cedd/
                         (CSV; the large hourly tables are parquet; no raw/ references)
meta/                    stations.csv, station_mapping.csv, api_inventory.csv,
                         data_dictionary_v0.md, collection_log.csv
scripts/                 collectors + processing
logs/                    collection.log, flatten.log, quality_checks.log, reports
tests/                   offline unit tests for parsers
```

`processed/` can be copied on its own and used for analysis: every table is fully materialised, keyed consistently (string IDs), and contains no `raw/` paths. Each `processed/` folder also gets a dynamically auto-generated `README.md`. The `raw/`, `processed/`, `logs/`, and `.venv/` directories are **git-ignored** (the data is too large for GitHub; the venv is local-only).

## Setup

A virtual environment already exists at `.venv/` (local only — it is git-ignored; recreate it with `python -m venv .venv` if missing). Install dependencies with:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run every command below from the repository root using the venv Python.

## Data window (important)

The project collects a **fixed historical window: `2023-01-01` → `2025-12-31`**. It is defined once in `scripts/common.py`:

```python
PROJECT_DATA_START_DATE = "2023-01-01"
PROJECT_DATA_END_DATE   = "2025-12-31"
```

All collectors default to this window, and `flatten_hko_data.py` filters every processed table to it. To change the range, edit those two values and re-run the collectors + flatten.

> The five live-only HKO feeds (`fnd`, `hourlyRainfall`, `warnsum`, `warningInfo`, `rhrread`) only ever return *current* data (2026), which is **outside** this window — so they are not used here. Their history is supplied by archives/substitutes (data.gov.hk, CoWIN, CEDD, the HKO warning database). See *Live collectors* below.

## Pull the full dataset (recommended workflow)

Run these in order. Steps 2–3 are the long ones.

**1. (Optional) start clean**

```powershell
Remove-Item -Recurse -Force raw, processed, logs -ErrorAction SilentlyContinue
```

**2. Collect all historical sources** (raw data)

```powershell
# HKO climate / daily backfills
.venv\Scripts\python.exe scripts/collect_hko_daily_temp.py          # daily mean/max/min temp, 14 stations
.venv\Scripts\python.exe scripts/collect_hko_daily_rainfall.py      # CIS per-station daily rainfall (9 stations)
.venv\Scripts\python.exe scripts/collect_hko_ryes.py --start-date 2023-01-01   # daily weather/radiation report (~10 min)
.venv\Scripts\python.exe scripts/collect_hko_warning_history.py     # rainstorm warning issue/cancel times

# data.gov.hk Historical Archive API (past RSS snapshots, real issue times)
.venv\Scripts\python.exe scripts/collect_data_gov_hk_history.py     # 9-day forecast  (1 snapshot/day; ~15-30 min)
.venv\Scripts\python.exe scripts/collect_data_gov_hk_rhrread.py     # current weather report (1 snapshot/day; ~15-30 min)

# Independent hourly / daily rainfall networks
.venv\Scripts\python.exe scripts/collect_cowin.py                   # HKU CoWIN QC hourly rainfall (40 stations)
.venv\Scripts\python.exe scripts/collect_cedd.py                    # CEDD GEO daily rainfall (90 gauges)

# Open-Meteo for every selected station (gridded cross-check)
$stations = 'RF028','RF024','RF015','RF013','H24','RF027','H15','RF022','K03','RF025','RF020','RF001','RF004','RF014'
foreach ($s in $stations) { .venv\Scripts\python.exe scripts/collect_open_meteo.py --station $s --source both }
```

**3. Build the processed tables**

```powershell
.venv\Scripts\python.exe scripts/flatten_hko_data.py
```

This rebuilds every `processed/` table from `raw/`, deduplicates, builds the rainstorm labels and warning windows, copies station metadata, and auto-generates the processed READMEs.

**4. Verify**

```powershell
.venv\Scripts\python.exe scripts/quality_checks.py
.venv\Scripts\python.exe tests/test_collectors.py
```

`quality_checks.py` prints a per-table report (0 warnings expected) and saves it to `logs/quality_checks_<timestamp>.txt`.

## Live collectors (forward monitoring only)

These hit the live HKO APIs and return **current** data (not history), so they are separate from the 2023–2025 dataset. Use them if/when you want to accumulate forward data:

```powershell
.venv\Scripts\python.exe scripts/run_collection.py --once    # one live pass: fnd, hourlyRainfall, warnsum, warningInfo, rhrread
.venv\Scripts\python.exe scripts/run_collection.py --loop    # continuous: warnings every 30 min, rain+current every hour, fnd every 6 h (Ctrl+C to stop)
```

## Script reference

| Script | What it pulls | Default window | Key options | Raw → processed |
|---|---|---|---|---|
| `collect_hko_daily_temp.py` | HKO daily mean/max/min temperature (`opendata.php`) | project window | `--data-type ALL\|CLMTEMP\|CLMMAXT\|CLMMINT`, `--stations`, `--start-year`, `--end-year` | `raw/hko_daily_temp` → `hko/hko_daily_temp(.csv/_wide.csv)` |
| `collect_hko_daily_rainfall.py` | HKO CIS per-station daily rainfall | project window | `--stations`, `--start-year`, `--end-year` | `raw/hko_daily_rainfall` → `hko/hko_daily_rainfall.csv` |
| `collect_hko_ryes.py` | HKO daily weather & radiation report (`RYES`) | `--start-date` → end of window | `--date`, `--start-date`, `--end-date` | `raw/hko_ryes` → `hko/hko_daily_weather.csv` |
| `collect_hko_warning_history.py` | Rainstorm warning issue/cancel times (scraped `rstorm.dat`) | project window | – | `raw/hko_warning_history` → `hko/hko_rainstorm_warning_history.csv` |
| `collect_data_gov_hk_history.py` | Archived 9-day forecast RSS (real issue times) | project window | `--start-date`, `--end-date`, `--per-day 1..4` | `raw/data_gov_hk_9day` → `hko/hko_fnd_archive.csv` |
| `collect_data_gov_hk_rhrread.py` | Archived Current Weather Report RSS | project window | `--start-date`, `--end-date`, `--per-day 1..24` | `raw/data_gov_hk_rhrread` → `hko/hko_rhrread_archive.csv` (+ `_summary`) |
| `collect_cowin.py` | HKU CoWIN QC hourly rainfall | project window | `--start-year`, `--end-year` | `raw/cowin` → `cowin/cowin_hourly_rainfall.parquet`, `cowin_stations`, `cowin_labels_rainstorm_hourly` |
| `collect_cedd.py` | CEDD GEO raingauge daily rainfall | project window | `--start-year`, `--end-year` | `raw/cedd` → `cedd/cedd_daily_rainfall.parquet`, `cedd_stations` |
| `collect_open_meteo.py` | Open-Meteo historical forecast + archive | project window | `--station` (or `--latitude/--longitude`), `--source forecast\|archive\|both`, `--start-date`, `--end-date` | `raw/om_historical_*` → `open_meteo/*.parquet` |
| `collect_hko_fnd.py` | Live 9-day forecast snapshot | live | – | `raw/hko_fnd` → `hko/hko_fnd_daily.csv` |
| `collect_hko_hourly_rain.py` | Live AWS hourly rainfall | live | – | `raw/hko_hourly_rain` → `hko/hko_hourly_rain.csv` |
| `collect_hko_warnings.py` | Live `warnsum` / `warningInfo` | live | `--part summary\|info\|both` | `raw/hko_warnsum`, `raw/hko_warning_info` |
| `collect_hko_rhrread.py` | Live current weather report | live | – | `raw/hko_rhrread` → `hko/hko_rhrread.csv` (+ `_summary`) |

## Processing, documentation, checks

```powershell
# Rebuild all processed tables from raw (idempotent; also regenerates the docs below)
.venv\Scripts\python.exe scripts/flatten_hko_data.py

# Process and publish to Hugging Face in one step (optional; see below)
.venv\Scripts\python.exe scripts/flatten_hko_data.py --push-hf

# Auto-generate processed/README.md and one README per source folder (dynamic, from the data)
.venv\Scripts\python.exe scripts/generate_processed_readmes.py

# Quality checks (report printed + saved to logs/)
.venv\Scripts\python.exe scripts/quality_checks.py

# Offline parser unit tests
.venv\Scripts\python.exe tests/test_collectors.py
```

The generated `processed/**/README.md` files are dynamic: they discover the files and columns actually present and document each table's format, row/column counts, purpose, sources, time coverage, join keys, and a per-column schema. Flatten calls the generator automatically, so they always match the current data.

## Publish to Hugging Face (optional)

Processed data can be published to a Hugging Face **dataset** repo (default: `NotASI/COMP3522-HKO`, public). Authentication uses the `HF_TOKEN` environment variable or a cached `huggingface-cli login`.

```powershell
# process, then upload processed/ in one step
.venv\Scripts\python.exe scripts/flatten_hko_data.py --push-hf

# or upload the current processed/ on its own
.venv\Scripts\python.exe scripts/upload_to_huggingface.py

# choose a different repo / private
.venv\Scripts\python.exe scripts/upload_to_huggingface.py --repo-id <user>/<name> --private
```

The whole `processed/` folder (including the auto-generated READMEs) is uploaded and nested by source. Requires `huggingface_hub` (in `requirements.txt`).

## Key data rules (see `meta/data_dictionary_v0.md` for the full dictionary)

- Store every raw snapshot exactly as received; never overwrite.
- All analysis times are HKT (UTC+8); Open-Meteo requests use `timezone=Asia/Hong_Kong`.
- The live `fnd` API has no official issue date; `issue_date_proxy` = collection date. The data.gov.hk archive (`hko_fnd_archive`) **does** carry real issue times.
- AWS hourly rainfall `value == "M"` means maintenance/missing — keep as missing, never convert to 0.
- PSR (High/Medium/Low) is categorical; do not convert it to rainfall mm.
- Open-Meteo is a cross-check only (`issue_time_known=FALSE`); HKO remains the ground truth.
- Station coordinates in `meta/stations.csv` are approximate (`coordinates_verified=FALSE`); verify against data.gov.hk dataset `hk-hko-rss-network-of-weather-stations-in-hong-kong` before relying on Open-Meteo station matching.

## Known limitations

1. HKO hourly rainfall API is latest-only (the `date` parameter is ignored) and HKO publishes no historical hourly archive. Historical hourly rainfall is rescued from the **HKU CoWIN** network (2023-2025, `scripts/collect_cowin.py`), used to build `processed/cowin/cowin_labels_rainstorm_hourly.csv`. CoWIN is a QC'd community/school network (not HKO AWS); citation of CoWIN and Lam et al. (2021) is required when publishing.
2. Current warning APIs expose only active warnings, but **historical rainstorm warning issue/cancel times** are scraped from the HKO warning database file `rstorm.dat` into `processed/hko/hko_rainstorm_warning_history.csv` (raw 1998–present; processed 2023-01-01 → 2025-12-31), including `episode_id` escalation linkage.
3. HKO 9-day forecasts are territory-wide products, not station-specific.
4. Open-Meteo does not expose forecast issue/run times, and in this configuration the Historical Forecast API returns values **identical to the Archive API** for 2023–2025 (verified 2026-10-07) — i.e. the `om_historical_forecast_*` tables currently duplicate `om_historical_weather_*` (reanalysis). Genuine per-lead-time archived forecasts would use Open-Meteo's Previous Runs API.
5. Historical 9-day forecasts are backfilled via the **data.gov.hk Historical Archive API** (`scripts/collect_data_gov_hk_history.py` → `processed/hko/hko_fnd_archive.csv`, 2023–2025, 1 snapshot/day by default `--per-day 4` for the full ~4/day archive, real issue times).
6. The historical window is **2023-01-01 to 2025-12-31**. The live-API tables (`rhrread`, `warnsum`, `warningInfo`, HKO `hourlyRainfall`) hold no data in this window; `rhrread`/`fnd` history are covered by their data.gov.hk archives, and hourly rainfall / warnings by substitutes (CoWIN/CEDD; the HKO warning database).
7. CoWIN 2024 is missing 6 hours (`2024-04-17 02:00–07:00`) — a gap in CoWIN's own data.
