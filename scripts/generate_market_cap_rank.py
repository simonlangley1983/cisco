import json
import re
import requests
from bs4 import BeautifulSoup
from pathlib import Path

DATA_FILE = Path("data/cisco_data.json")
URL = "https://companiesmarketcap.com/"

def get_cisco_rank():
    html = requests.get(URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0"
    }).text

    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("tr")
    for row in rows:
        text = row.get_text(" ", strip=True)

        if "Cisco" in text or "CSCO" in text:
            rank_match = re.match(r"^(\d+)", text)
            if rank_match:
                return int(rank_match.group(1))

    raise RuntimeError("Could not find Cisco rank")

def main():
    rank = get_cisco_rank()

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    data["marketCapRank"] = rank
    data["marketCapRankLabel"] = f"#{rank} globally by market cap"
    data["marketCapRankSource"] = "CompaniesMarketCap"

    DATA_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

    print(f"Cisco market cap rank updated: #{rank}")

if __name__ == "__main__":
    main()
