import json
import re
import time
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "latest-quotes.json"


def main():
    tickers = read_unique_tickers()
    quotes = server.fetch_quotes(tickers)
    by_symbol = {item["symbol"]: item for item in quotes}
    missing = [ticker for ticker in tickers if ticker not in by_symbol]
    if missing:
        raise RuntimeError(f"Quote data is missing for: {', '.join(missing)}")

    payload = {
        "quotes": [by_symbol[ticker] for ticker in tickers],
        "source": "GitHub Actions",
        "fetchedAt": int(time.time()),
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {OUTPUT} with {len(tickers)} quotes")


def read_unique_tickers():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    tickers = []
    for tickers_src in re.findall(r'makeFund\("[^"]+", "[^"]+", \[([^\]]+)\]\)', html):
        for ticker in re.findall(r'"([^"]+)"', tickers_src):
            if ticker not in tickers:
                tickers.append(ticker)
    if not tickers:
        raise RuntimeError("No fund tickers found in index.html")
    return tickers


if __name__ == "__main__":
    main()
