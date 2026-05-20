#!/usr/bin/env python3
"""
Build Cisco (CSCO) daily share-price files.

Repo structure expected:

/
  data/
  scripts/build_cisco_json.py

Run from repo root:

    python scripts/build_cisco_json.py

Requires:

    pip install requests

Outputs:
  data/cisco_daily.json
  data/cisco_daily_compact.json
  data/cisco_daily.csv
  data/cisco_summary.json
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


SYMBOL = "CSCO"

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

OUT_JSON = DATA_DIR / "cisco_daily.json"
OUT_COMPACT_JSON = DATA_DIR / "cisco_daily_compact.json"
OUT_CSV = DATA_DIR / "cisco_daily.csv"
OUT_SUMMARY = DATA_DIR / "cisco_summary.json"


def yahoo_chart_url() -> str:
    period2 = int(datetime.now(timezone.utc).timestamp())
    return (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{SYMBOL}?period1=0&period2={period2}"
        "&interval=1d&events=history&includeAdjustedClose=true"
    )


@dataclass
class PriceRecord:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adjusted_close: float | None
    volume: int | None
    previous_close: float | None
    daily_change: float | None
    daily_change_pct: float | None
    close_from_first_pct: float | None


def round_or_none(value: Any, places: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def download_yahoo_chart() -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }

    response = requests.get(yahoo_chart_url(), headers=headers, timeout=30)
    response.raise_for_status()

    payload = response.json()
    chart = payload.get("chart", {})
    error = chart.get("error")

    if error:
        raise RuntimeError(f"Yahoo returned an error: {error}")

    result = chart.get("result")

    if not result:
        raise RuntimeError("Yahoo returned no chart result.")

    return result[0]


def parse_records(chart: dict[str, Any]) -> list[PriceRecord]:
    timestamps = chart.get("timestamp", [])
    indicators = chart.get("indicators", {})
    quote = indicators.get("quote", [{}])[0]
    adjclose = indicators.get("adjclose", [{}])[0].get("adjclose", [])

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    records: list[PriceRecord] = []
    previous_close: float | None = None
    first_close: float | None = None

    for i, ts in enumerate(timestamps):
        close = round_or_none(closes[i] if i < len(closes) else None)

        if close is None:
            continue

        if first_close is None:
            first_close = close

        if previous_close is None:
            daily_change = None
            daily_change_pct = None
        else:
            daily_change = round(close - previous_close, 6)
            daily_change_pct = round((daily_change / previous_close) * 100, 6)

        close_from_first_pct = (
            round(((close - first_close) / first_close) * 100, 6)
            if first_close
            else None
        )

        date = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()

        records.append(
            PriceRecord(
                date=date,
                open=round_or_none(opens[i] if i < len(opens) else None),
                high=round_or_none(highs[i] if i < len(highs) else None),
                low=round_or_none(lows[i] if i < len(lows) else None),
                close=close,
                adjusted_close=round_or_none(adjclose[i] if i < len(adjclose) else None),
                volume=int_or_none(volumes[i] if i < len(volumes) else None),
                previous_close=previous_close,
                daily_change=daily_change,
                daily_change_pct=daily_change_pct,
                close_from_first_pct=close_from_first_pct,
            )
        )

        previous_close = close

    if not records:
        raise RuntimeError("No usable price records were parsed.")

    return records


def longest_streak(records: list[PriceRecord], direction: str) -> dict[str, Any]:
    best_count = 0
    best_start = None
    best_end = None
    current_count = 0
    current_start = None

    for rec in records:
        pct = rec.daily_change_pct

        if pct is None:
            continue

        is_match = pct > 0 if direction == "gain" else pct < 0

        if is_match:
            if current_count == 0:
                current_start = rec.date
            current_count += 1

            if current_count > best_count:
                best_count = current_count
                best_start = current_start
                best_end = rec.date
        else:
            current_count = 0
            current_start = None

    return {
        "direction": direction,
        "trading_days": best_count,
        "start_date": best_start,
        "end_date": best_end,
    }


def best_worst_period(records: list[PriceRecord], period_length: int) -> tuple[dict[str, Any], dict[str, Any]]:
    periods: dict[str, dict[str, Any]] = {}

    for rec in records:
        if rec.close is None:
            continue

        period = rec.date[:period_length]

        if period not in periods:
            periods[period] = {
                "period": period,
                "start_date": rec.date,
                "end_date": rec.date,
                "start_close": rec.close,
                "end_close": rec.close,
            }
        else:
            periods[period]["end_date"] = rec.date
            periods[period]["end_close"] = rec.close

    rows = []

    for item in periods.values():
        change_pct = round(
            ((item["end_close"] - item["start_close"]) / item["start_close"]) * 100,
            6,
        )
        rows.append({**item, "change_pct": change_pct})

    return max(rows, key=lambda x: x["change_pct"]), min(rows, key=lambda x: x["change_pct"])


def build_summary(records: list[PriceRecord]) -> dict[str, Any]:
    first = records[0]
    latest = records[-1]

    records_with_change = [r for r in records if r.daily_change_pct is not None]
    records_with_high = [r for r in records if r.high is not None]
    records_with_close = [r for r in records if r.close is not None]

    biggest_gain = max(records_with_change, key=lambda r: r.daily_change_pct or 0)
    biggest_decline = min(records_with_change, key=lambda r: r.daily_change_pct or 0)
    all_time_high = max(records_with_high, key=lambda r: r.high or 0)
    all_time_closing_high = max(records_with_close, key=lambda r: r.close or 0)
    best_month, worst_month = best_worst_period(records, 7)
    best_year, worst_year = best_worst_period(records, 4)

    return {
        "symbol": SYMBOL,
        "source": "Yahoo Finance chart endpoint",
        "source_url": yahoo_chart_url(),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "first_date": first.date,
        "latest_date": latest.date,
        "latest_close": latest.close,
        "all_time_high_intraday": {
            "date": all_time_high.date,
            "high": all_time_high.high,
        },
        "all_time_high_close": {
            "date": all_time_closing_high.date,
            "close": all_time_closing_high.close,
        },
        "biggest_single_day_gain": {
            "date": biggest_gain.date,
            "daily_change_pct": biggest_gain.daily_change_pct,
            "daily_change": biggest_gain.daily_change,
            "close": biggest_gain.close,
        },
        "biggest_single_day_decline": {
            "date": biggest_decline.date,
            "daily_change_pct": biggest_decline.daily_change_pct,
            "daily_change": biggest_decline.daily_change,
            "close": biggest_decline.close,
        },
        "best_month": {"month": best_month["period"], **{k: v for k, v in best_month.items() if k != "period"}},
        "worst_month": {"month": worst_month["period"], **{k: v for k, v in worst_month.items() if k != "period"}},
        "best_year": {"year": best_year["period"], **{k: v for k, v in best_year.items() if k != "period"}},
        "worst_year": {"year": worst_year["period"], **{k: v for k, v in worst_year.items() if k != "period"}},
        "longest_gain_streak": longest_streak(records, "gain"),
        "longest_decline_streak": longest_streak(records, "decline"),
    }


def write_json(records: list[PriceRecord], summary: dict[str, Any]) -> None:
    payload = {
        "meta": summary,
        "records": [asdict(record) for record in records],
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_compact_json(records: list[PriceRecord], summary: dict[str, Any]) -> None:
    fields = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
        "previous_close",
        "daily_change",
        "daily_change_pct",
        "close_from_first_pct",
    ]

    payload = {
        "meta": summary,
        "fields": fields,
        "records": [
            [
                rec.date,
                rec.open,
                rec.high,
                rec.low,
                rec.close,
                rec.adjusted_close,
                rec.volume,
                rec.previous_close,
                rec.daily_change,
                rec.daily_change_pct,
                rec.close_from_first_pct,
            ]
            for rec in records
        ],
    }

    OUT_COMPACT_JSON.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def write_csv(records: list[PriceRecord]) -> None:
    fieldnames = list(asdict(records[0]).keys())

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


def write_summary(summary: dict[str, Any]) -> None:
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> int:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        chart = download_yahoo_chart()
        records = parse_records(chart)
        summary = build_summary(records)

        write_json(records, summary)
        write_compact_json(records, summary)
        write_csv(records)
        write_summary(summary)

        print(f"Created {OUT_JSON.relative_to(REPO_ROOT)} with {len(records):,} records.")
        print(f"Created {OUT_COMPACT_JSON.relative_to(REPO_ROOT)}.")
        print(f"Created {OUT_CSV.relative_to(REPO_ROOT)}.")
        print(f"Created {OUT_SUMMARY.relative_to(REPO_ROOT)}.")
        print(f"Date range: {summary['first_date']} to {summary['latest_date']}")
        print(
            "All-time intraday high: "
            f"{summary['all_time_high_intraday']['high']} "
            f"on {summary['all_time_high_intraday']['date']}"
        )

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
