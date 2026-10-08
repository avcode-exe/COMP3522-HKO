import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collect_hko_daily_temp import parse_daily_temp_csv, parse_daily_temp_json
from collect_hko_fnd import parse_fnd
from collect_hko_hourly_rain import parse_hourly_rain
from collect_open_meteo import parse_open_meteo
from collect_hko_rhrread import parse_rhrread, parse_rhrread_summary
from collect_hko_ryes import parse_ryes
from collect_hko_warnings import parse_warning_information, parse_warning_summary

HKT = timezone(timedelta(hours=8))
COLLECTED_AT = datetime(2026, 10, 6, 17, 5, 0, tzinfo=HKT)


class TestParseFnd(unittest.TestCase):
    def test_parse_fnd(self):
        payload = {
            "generalSituation": "Fine.",
            "weatherForecast": [
                {
                    "forecastDate": "20261007",
                    "week": "Wednesday",
                    "forecastWind": "N force 4.",
                    "forecastWeather": "Mainly fine.",
                    "forecastMaxtemp": {"value": 30, "unit": "C"},
                    "forecastMintemp": {"value": 23, "unit": "C"},
                    "forecastMaxrh": {"value": 75, "unit": "percent"},
                    "forecastMinrh": {"value": 45, "unit": "percent"},
                    "ForecastIcon": 81,
                    "PSR": "Low",
                }
            ],
        }
        df = parse_fnd(payload, COLLECTED_AT)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["target_date"], "2026-10-07")
        self.assertEqual(row["issue_date_proxy"], "2026-10-06")
        self.assertEqual(row["lead_time_days"], 1)
        self.assertEqual(row["forecast_max_temp_c"], 30)
        self.assertEqual(row["forecast_min_temp_c"], 23)
        self.assertEqual(row["psr"], "Low")
        self.assertEqual(row["snapshot_id"], "20261006T170500")

    def test_parse_fnd_invalid_date(self):
        payload = {"weatherForecast": [{"forecastDate": "bad", "PSR": "High"}]}
        df = parse_fnd(payload, COLLECTED_AT)
        self.assertIsNone(df.iloc[0]["target_date"])
        self.assertIsNone(df.iloc[0]["lead_time_days"])


class TestParseHourlyRain(unittest.TestCase):
    def test_parse_hourly_rain(self):
        payload = {
            "obsTime": "2026-10-06T17:15:00+08:00",
            "hourlyRainfall": [
                {"automaticWeatherStation": "King's Park", "automaticWeatherStationID": "RF024", "value": "35", "unit": "mm"},
                {"automaticWeatherStation": "The Peak", "automaticWeatherStationID": "RF028", "value": "M", "unit": "mm"},
            ],
        }
        df = parse_hourly_rain(payload)
        self.assertEqual(len(df), 2)
        kp = df[df["station_id"] == "RF024"].iloc[0]
        self.assertEqual(kp["rain_mm_1h"], 35.0)
        self.assertTrue(kp["station_amber_flag"])
        self.assertFalse(kp["station_red_flag"])
        peak = df[df["station_id"] == "RF028"].iloc[0]
        self.assertTrue(peak["maintenance_or_missing"])
        self.assertTrue(pd.isna(peak["rain_mm_1h"]))
        self.assertFalse(peak["station_amber_flag"])


class TestParseWarnings(unittest.TestCase):
    def test_parse_warning_summary(self):
        payload = {
            "WRAIN": {
                "name": "Rainstorm Warning Signal",
                "code": "WRAINR",
                "type": "Red",
                "actionCode": "ISSUE",
                "issueTime": "2020-09-24T11:15:00+08:00",
                "updateTime": "2020-09-24T11:15:00+08:00",
            }
        }
        df = parse_warning_summary(payload, COLLECTED_AT)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["warning_key"], "WRAIN")
        self.assertEqual(df.iloc[0]["code"], "WRAINR")

    def test_parse_warning_summary_empty(self):
        df = parse_warning_summary({}, COLLECTED_AT)
        self.assertEqual(len(df), 0)

    def test_parse_warning_information(self):
        payload = {
            "details": [
                {
                    "warningStatementCode": "WRAIN",
                    "subtype": "WRAINR",
                    "updateTime": "2020-09-24T11:15:00+08:00",
                    "contents": ["Red Rainstorm Warning Signal has been issued at 11:15 a.m."],
                }
            ]
        }
        df = parse_warning_information(payload, COLLECTED_AT)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["subtype"], "WRAINR")
        self.assertIn("Red Rainstorm", df.iloc[0]["contents_text"])

    def test_parse_warning_information_empty(self):
        df = parse_warning_information({}, COLLECTED_AT)
        self.assertEqual(len(df), 0)


class TestParseRhrread(unittest.TestCase):
    def test_parse_rhrread(self):
        payload = {
            "rainfall": {"data": [{"unit": "mm", "place": "Central & Western District", "max": 0, "main": "FALSE"}]},
            "temperature": {"data": [{"place": "King's Park", "value": 26, "unit": "C"}]},
            "humidity": {"recordTime": "2026-10-06T17:00:00+08:00", "data": [{"unit": "percent", "value": 62, "place": "Hong Kong Observatory"}]},
            "uvindex": {"data": [{"place": "King's Park", "value": 0.7, "desc": "low"}], "recordDesc": "During the past hour"},
            "updateTime": "2026-10-06T17:00:00+08:00",
        }
        df = parse_rhrread(payload, COLLECTED_AT)
        elements = set(df["element"])
        self.assertEqual(elements, {"rainfall_max_1h", "temperature", "humidity", "uvindex"})
        self.assertEqual(len(df), 4)
        summary = parse_rhrread_summary(payload, COLLECTED_AT)
        self.assertEqual(len(summary), 1)


class TestParseDailyTemp(unittest.TestCase):
    def test_parse_daily_temp_json(self):
        payload = {
            "type": ["最高氣溫 (攝氏度) - 京士柏", "Maximum Temperature (°C) - King's Park"],
            "fields": ["年/Year", "月/Month", "日/Day", "數值/Value", "數據完整性/data Completeness"],
            "data": [["2024", "1", "1", "22.3", "C"], ["2024", "1", "2", "20.3", "#"]],
        }
        df = parse_daily_temp_json(payload, "CLMMAXT", "KP", 2024)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["date"], "2024-01-01")
        self.assertEqual(df.iloc[0]["value_c"], 22.3)
        self.assertEqual(df.iloc[0]["data_type"], "CLMMAXT")
        self.assertEqual(df.iloc[1]["data_completeness"], "#")

    def test_parse_daily_temp_csv(self):
        text = '﻿"最高氣溫 (攝氏度) - 京士柏"\n"Maximum Temperature (°C) - King\'s Park"\n年/Year,月/Month,日/Day,數值/Value,"數據完整性/data Completeness"\n2024,1,1,22.3,C\n2024,1,2,19.8,#\n'
        df = parse_daily_temp_csv(text, "CLMMAXT", "KP", 2024)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["value_c"], 22.3)
        self.assertEqual(df.iloc[1]["data_completeness"], "#")


class TestParseRyes(unittest.TestCase):
    def test_parse_ryes(self):
        payload = {
            "ReportTimeInfoDate": "20261005",
            "BulletinDate": "20261006",
            "BulletinTime": "0015",
            "KingsParkLocationName": "King's Park",
            "KingsParkMaxTemp": "30.2",
            "KingsParkMinTemp": "24.2",
            "KingsParkReadingsSunShine": "9.7",
            "KingsParkReadingsMaxUVIndex": "9",
            "KingsParkReadingsMeanUVIndex": "4",
            "HKOReadingsMaxTemp": "31.2",
            "HKOReadingsMinTemp": "25.1",
            "HKOReadingsRainfall": "0",
            "HKOReadingsMaxRH": "86",
            "HKOReadingsMinRH": "57",
            "ChekLapKokLocationName": "Chek Lap Kok",
            "ChekLapKokMaxTemp": "30.2",
            "ChekLapKokMinTemp": "25.0",
        }
        df = parse_ryes(payload, COLLECTED_AT)
        self.assertEqual(df["report_date"].iloc[0], "20261005")
        kp_max = df[(df["location"] == "King's Park") & (df["element"] == "max_temp_c")].iloc[0]
        self.assertEqual(kp_max["value"], 30.2)
        hko_rain = df[(df["location"] == "Hong Kong Observatory") & (df["element"] == "rainfall_mm")].iloc[0]
        self.assertEqual(hko_rain["value"], 0.0)
        sunshine = df[(df["location"] == "King's Park") & (df["element"] == "sunshine_hours")].iloc[0]
        self.assertEqual(sunshine["value"], 9.7)


class TestParseOpenMeteo(unittest.TestCase):
    def test_parse_open_meteo(self):
        payload = {
            "latitude": 22.31,
            "longitude": 114.17,
            "timezone": "Asia/Hong_Kong",
            "hourly_units": {"time": "iso8601", "precipitation": "mm", "temperature_2m": "°C"},
            "hourly": {"time": ["2026-10-01T00:00", "2026-10-01T01:00"], "precipitation": [0.0, 1.2], "temperature_2m": [26.0, 25.8]},
            "daily_units": {"time": "iso8601", "precipitation_sum": "mm"},
            "daily": {"time": ["2026-10-01"], "precipitation_sum": [1.2]},
        }
        hourly_df, daily_df = parse_open_meteo(payload, "historical_forecast", "RF024")
        self.assertEqual(len(hourly_df), 2)
        self.assertEqual(hourly_df.iloc[1]["precipitation"], 1.2)
        self.assertFalse(hourly_df.iloc[0]["issue_time_known"])
        self.assertEqual(len(daily_df), 1)
        self.assertEqual(daily_df.iloc[0]["precipitation_sum"], 1.2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
