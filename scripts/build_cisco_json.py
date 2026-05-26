from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_FILE = DATA_DIR / "cisco_daily.json"

PEERS = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "GOOGL": "Google / Alphabet",
    "AVGO": "Broadcom",
}


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ["_".join(str(x) for x in col if str(x)) for col in df.columns.to_flat_index()]
    return df


def standardise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = flatten_columns(df).reset_index()
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]

    if "datetime" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"datetime": "date"})

    for base in ["open", "high", "low", "close", "adj_close", "volume"]:
        if base not in df.columns:
            matches = [c for c in df.columns if c.startswith(base + "_")]
            if matches:
                df = df.rename(columns={matches[0]: base})

    return df


def download_history(ticker: str, attempts: int = 3) -> pd.DataFrame:
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            df = yf.download(
                ticker,
                start="1990-01-01",
                auto_adjust=False,
                progress=False,
                threads=False,
                group_by="column",
            )

            if df.empty:
                raise RuntimeError(f"No data returned for {ticker}")

            df = standardise_columns(df)

            if "date" not in df.columns or "close" not in df.columns:
                raise RuntimeError(f"{ticker} missing date/close. Columns: {list(df.columns)}")

            close_count = pd.to_numeric(df["close"], errors="coerce").notna().sum()
            if close_count < 2:
                raise RuntimeError(f"{ticker} has fewer than two usable close prices")

            return df

        except Exception as exc:
            last_error = exc
            print(f"Attempt {attempt}/{attempts} failed for {ticker}: {exc}")
            time.sleep(5)

    raise RuntimeError(f"Failed to download usable data for {ticker}: {last_error}")


def none_or_float(value):
    if pd.isna(value):
        return None
    return round(float(value), 6)


def none_or_int(value):
    if pd.isna(value):
        return None
    return int(value)


def to_records(df: pd.DataFrame) -> list[dict]:
    out = []

    for _, row in df.iterrows():
        if pd.isna(row.get("date")) or pd.isna(row.get("close")):
            continue

        out.append(
            {
                "date": pd.to_datetime(row.get("date")).strftime("%Y-%m-%d"),
                "open": none_or_float(row.get("open")),
                "high": none_or_float(row.get("high")),
                "low": none_or_float(row.get("low")),
                "close": none_or_float(row.get("close")),
                "adj_close": none_or_float(row.get("adj_close")),
                "volume": none_or_int(row.get("volume")),
            }
        )

    out.sort(key=lambda x: x["date"])
    return out


def daily_changes(records: list[dict]) -> list[dict]:
    changes = []

    for previous, current in zip(records, records[1:]):
        if previous["close"] is not None and current["close"] is not None and previous["close"] != 0:
            item = current.copy()
            item["daily_change_pct"] = ((current["close"] - previous["close"]) / previous["close"]) * 100
            changes.append(item)

    return changes


def calendar_periods(records: list[dict], key_len: int) -> list[dict]:
    grouped = {}

    for row in records:
        grouped.setdefault(row["date"][:key_len], []).append(row)

    periods = []

    for period, rows in grouped.items():
        rows = [r for r in rows if r["close"] is not None]
        if len(rows) < 2:
            continue

        start = rows[0]
        end = rows[-1]

        if start["close"] and end["close"]:
            periods.append(
                {
                    "period": period,
                    "start_date": start["date"],
                    "end_date": end["date"],
                    "change_pct": ((end["close"] - start["close"]) / start["close"]) * 100,
                }
            )

    return periods


def longest_streak(records: list[dict], direction: str) -> dict:
    best = []
    current = []

    for previous, row in zip(records, records[1:]):
        if previous["close"] is None or row["close"] is None:
            continue

        match = row["close"] > previous["close"] if direction == "gain" else row["close"] < previous["close"]

        if match:
            current = [previous, row] if not current else current + [row]
        else:
            if len(current) > len(best):
                best = current
            current = []

    if len(current) > len(best):
        best = current

    if not best:
        return {"trading_days": 0, "start_date": None, "end_date": None}

    return {"trading_days": len(best) - 1, "start_date": best[0]["date"], "end_date": best[-1]["date"]}


def build_meta(records: list[dict]) -> dict:
    if len(records) < 2:
        raise RuntimeError(f"Need at least two CSCO records, got {len(records)}")

    changes = daily_changes(records)
    if not changes:
        raise RuntimeError("No daily changes calculated. CSCO close prices are missing or invalid.")

    monthly = calendar_periods(records, 7)
    yearly = calendar_periods(records, 4)

    if not monthly or not yearly:
        raise RuntimeError("Not enough valid CSCO data for monthly/yearly statistics.")

    high = max((r for r in records if r["high"] is not None), key=lambda r: r["high"])
    high_close = max((r for r in records if r["close"] is not None), key=lambda r: r["close"])
    biggest_gain = max(changes, key=lambda r: r["daily_change_pct"])
    biggest_decline = min(changes, key=lambda r: r["daily_change_pct"])
    best_month = max(monthly, key=lambda r: r["change_pct"])
    worst_month = min(monthly, key=lambda r: r["change_pct"])
    best_year = max(yearly, key=lambda r: r["change_pct"])
    worst_year = min(yearly, key=lambda r: r["change_pct"])

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance via yfinance",
        "symbol": "CSCO",
        "peer_symbols": PEERS,
        "record_count": len(records),
        "first_date": records[0]["date"],
        "last_date": records[-1]["date"],
        "all_time_high_intraday": {"date": high["date"], "high": high["high"]},
        "all_time_high_close": {"date": high_close["date"], "close": high_close["close"]},
        "biggest_single_day_gain": {"date": biggest_gain["date"], "daily_change_pct": round(biggest_gain["daily_change_pct"], 6)},
        "biggest_single_day_decline": {"date": biggest_decline["date"], "daily_change_pct": round(biggest_decline["daily_change_pct"], 6)},
        "best_month": {"month": best_month["period"], "change_pct": round(best_month["change_pct"], 6), "start_date": best_month["start_date"], "end_date": best_month["end_date"]},
        "worst_month": {"month": worst_month["period"], "change_pct": round(worst_month["change_pct"], 6), "start_date": worst_month["start_date"], "end_date": worst_month["end_date"]},
        "best_year": {"year": best_year["period"], "change_pct": round(best_year["change_pct"], 6), "start_date": best_year["start_date"], "end_date": best_year["end_date"]},
        "worst_year": {"year": worst_year["period"], "change_pct": round(worst_year["change_pct"], 6), "start_date": worst_year["start_date"], "end_date": worst_year["end_date"]},
        "longest_gain_streak": longest_streak(records, "gain"),
        "longest_decline_streak": longest_streak(records, "decline"),
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading CSCO...")
    cisco_records = to_records(download_history("CSCO"))
    print(f"CSCO records: {len(cisco_records):,}")
    print(f"CSCO range: {cisco_records[0]['date']} to {cisco_records[-1]['date']}")

    peer_records = {}

    for symbol in PEERS:
        try:
            print(f"Downloading {symbol}...")
            peer_records[symbol] = to_records(download_history(symbol))
            print(f"{symbol} records: {len(peer_records[symbol]):,}")
        except Exception as exc:
            print(f"WARNING: {symbol} failed and will be skipped: {exc}")
            peer_records[symbol] = []

    payload = {
        "meta": build_meta(cisco_records),
        "records": cisco_records,
        "peer_records": peer_records,
    }

    OUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
