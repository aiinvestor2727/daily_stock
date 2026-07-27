from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from html import unescape
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen
import json
import os
import re
import time


HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
GOOGLE_URL = "https://www.google.com/finance/quote/"


class AppHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/quotes":
            self.handle_quotes(parsed.query)
            return
        super().do_GET()

    def handle_quotes(self, query):
        params = parse_qs(query)
        symbols = []
        for raw in params.get("symbols", [""])[0].split(","):
            symbol = raw.strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)

        if not symbols:
            self.write_json({"quotes": [], "errors": ["symbols is required"]}, 400)
            return

        quotes = fetch_quotes(symbols[:80])
        found_symbols = {quote["symbol"] for quote in quotes}
        errors = []
        for symbol in symbols[:80]:
            if symbol in found_symbols:
                continue
            try:
                quotes.append(fetch_quote(symbol))
            except Exception as exc:
                errors.append({"symbol": symbol, "message": str(exc)})

        self.write_json(
            {
                "quotes": quotes,
                "errors": errors,
                "source": "Google Finance (fallback: Yahoo Finance)",
                "fetchedAt": int(time.time()),
            }
        )

    def write_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def fetch_quotes(symbols):
    google_quotes = []
    missing = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_google_quote, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                google_quotes.append(future.result())
            except Exception:
                missing.append(symbol)

    if not missing:
        return google_quotes

    google_symbols = {quote["symbol"] for quote in google_quotes}
    yahoo_quotes = fetch_yahoo_quotes(missing)
    yahoo_quotes = [quote for quote in yahoo_quotes if quote["symbol"] not in google_symbols]
    quotes = google_quotes + yahoo_quotes

    found_symbols = {quote["symbol"] for quote in quotes}
    chart_missing = [symbol for symbol in missing if symbol not in found_symbols]
    if chart_missing:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(fetch_quote, symbol): symbol for symbol in chart_missing}
            for future in as_completed(futures):
                try:
                    quotes.append(future.result())
                except Exception:
                    pass

    return quotes


def fetch_google_quote(symbol):
    quote_id = google_quote_id(symbol)
    url = f"{GOOGLE_URL}{quote(quote_id, safe=':')}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 fund-holdings-local-app",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=12) as response:
        html = response.read().decode("utf-8", errors="ignore")

    parser = TextCollector()
    parser.feed(html)
    tokens = [token for token in parser.tokens if token]
    quote_index = next((i for i, token in enumerate(tokens) if token.upper() == quote_id.upper()), -1)
    if quote_index < 0:
        raise RuntimeError("Google quote page did not include the symbol")

    after_symbol = tokens[quote_index + 1 : quote_index + 24]
    price_index = next((i for i, token in enumerate(after_symbol) if parse_money(token) is not None), -1)
    if price_index < 0:
        raise RuntimeError("Google quote page did not include a price")

    price_token = after_symbol[price_index]
    price = parse_money(price_token)
    if price_token.startswith("¥"):
        currency = "JPY"
    elif price_token.startswith("₩"):
        currency = "KRW"
    else:
        currency = "USD"
    name = find_google_name(after_symbol[:price_index]) or symbol

    after_price = after_symbol[price_index + 1 :]
    pct_token = next((token for token in after_price if re.fullmatch(r"[+-]?\d+(?:\.\d+)?%", token)), None)
    if pct_token is None:
        raise RuntimeError("Google quote page did not include 1D change")

    pct = float(pct_token.replace("%", "").replace("+", ""))
    change = parse_google_change(after_price)
    if change is None:
        raise RuntimeError("Google quote page did not include numeric 1D change")

    previous_close = price - change
    return {
        "symbol": symbol.upper(),
        "name": name,
        "price": price,
        "previousClose": previous_close,
        "change": change,
        "pct": pct,
        "currency": currency,
        "marketState": "",
        "exchange": quote_id.split(":", 1)[1] if ":" in quote_id else "",
        "regularMarketTime": None,
    }


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def handle_data(self, data):
        text = unescape(data).strip()
        if text:
            self.tokens.append(re.sub(r"\s+", " ", text))


def google_quote_id(symbol):
    upper = symbol.upper().strip()
    if ":" in upper:
        return upper
    if upper.endswith(".T"):
        return f"{upper[:-2]}:TYO"
    if upper.endswith(".KS"):
        return f"{upper[:-3]}:KRX"
    exchange_overrides = {
        "AFRM": "NASDAQ",
        "ANET": "NYSE",
        "APP": "NASDAQ",
        "ASML": "NASDAQ",
        "BE": "NYSE",
        "BRK.B": "NYSE",
        "CIEN": "NYSE",
        "CLS": "NYSE",
        "COHR": "NYSE",
        "COIN": "NASDAQ",
        "CRM": "NYSE",
        "CRDO": "NASDAQ",
        "CVNA": "NYSE",
        "DASH": "NASDAQ",
        "DELL": "NYSE",
        "BABA": "NYSE",
        "DIS": "NYSE",
        "DUOL": "NASDAQ",
        "ETN": "NYSE",
        "FICO": "NYSE",
        "FIX": "NYSE",
        "GE": "NYSE",
        "GS": "NYSE",
        "HD": "NYSE",
        "HOOD": "NASDAQ",
        "IBM": "NYSE",
        "JPM": "NYSE",
        "KO": "NYSE",
        "LLY": "NYSE",
        "NVO": "NYSE",
        "MA": "NYSE",
        "NET": "NYSE",
        "NOW": "NYSE",
        "NBIS": "NASDAQ",
        "RBLX": "NYSE",
        "ROKU": "NASDAQ",
        "SAP": "NYSE",
        "SE": "NYSE",
        "SHEL": "NYSE",
        "SHOP": "NASDAQ",
        "SNOW": "NYSE",
        "SONY": "NYSE",
        "SPCX": "NASDAQ",
        "SPOT": "NYSE",
        "STX": "NASDAQ",
        "TOST": "NYSE",
        "ORCL": "NYSE",
        "PATH": "NYSE",
        "TSM": "NYSE",
        "U": "NYSE",
        "UBER": "NYSE",
        "V": "NYSE",
        "VRT": "NYSE",
        "VST": "NYSE",
        "WMT": "NYSE",
        "XYZ": "NYSE",
    }
    return f"{upper}:{exchange_overrides.get(upper, 'NASDAQ')}"


def parse_money(text):
    cleaned = text.replace("$", "").replace("¥", "").replace("₩", "").replace(",", "").replace("+", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_google_change(tokens):
    combined = " ".join(tokens[:8])
    match = re.search(r"\(\s*([+-]?[¥₩$]?\d[\d,]*(?:\.\d+)?)\s*\)\s*1D", combined)
    if match:
        return parse_money(match.group(1))

    one_day_index = next((i for i, token in enumerate(tokens) if "1D" in token), -1)
    if one_day_index >= 1:
        for token in reversed(tokens[:one_day_index]):
            value = parse_money(token)
            if value is not None:
                return value
    return None


def find_google_name(tokens):
    ignored = {
        "check_indeterminate_small",
        "arrow_upward",
        "arrow_downward",
        "Closed:",
        "today Pre-market",
    }
    for token in tokens:
        if token in ignored or ":" in token or token.startswith("+") or token.startswith("-"):
            continue
        if any(char.isalpha() for char in token):
            return token
    return None


def fetch_yahoo_quotes(symbols):
    url = f"{QUOTE_URL}?symbols={quote(','.join(symbols))}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 fund-holdings-local-app",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    results = payload.get("quoteResponse", {}).get("result") or []
    quotes = []
    for item in results:
        symbol = (item.get("symbol") or "").upper()
        price = as_number(item.get("regularMarketPrice"))
        previous_close = as_number(item.get("regularMarketPreviousClose"))
        change = as_number(item.get("regularMarketChange"))
        pct = as_number(item.get("regularMarketChangePercent"))
        if not symbol or price is None:
            continue
        if previous_close is None and change is not None:
            previous_close = price - change
        if change is None and previous_close is not None:
            change = price - previous_close
        if pct is None and previous_close:
            pct = change / previous_close * 100

        quotes.append(
            {
                "symbol": symbol,
                "name": item.get("shortName") or item.get("longName") or symbol,
                "price": price,
                "previousClose": previous_close,
                "change": change or 0,
                "pct": pct or 0,
                "currency": item.get("currency") or "USD",
                "marketState": item.get("marketState") or "",
                "exchange": item.get("exchange") or item.get("fullExchangeName") or "",
                "regularMarketTime": item.get("regularMarketTime"),
            }
        )
    return quotes


def fetch_quote(symbol):
    url = (
        f"{CHART_URL}{quote(symbol)}"
        "?range=5d&interval=1d"
        "&includePrePost=false"
        "&events=div%2Csplits"
    )
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 fund-holdings-local-app",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"quote request failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"quote request failed: {exc.reason}") from exc

    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        error = payload.get("chart", {}).get("error") or {}
        raise RuntimeError(error.get("description") or "quote not found")

    meta = result.get("meta") or {}
    closes = [
        value for value in ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [])
        if isinstance(value, (int, float))
    ]
    currency = meta.get("currency") or "USD"
    name = meta.get("shortName") or meta.get("longName") or symbol
    price = as_number(meta.get("regularMarketPrice"))
    previous_close = as_number(meta.get("previousClose") or meta.get("chartPreviousClose"))
    change = as_number(meta.get("regularMarketChange"))
    pct = as_number(meta.get("regularMarketChangePercent"))

    if price is None and closes:
        price = closes[-1]
    if previous_close is None:
        previous_close = closes[-2] if len(closes) >= 2 else price

    if price is None or previous_close is None:
        raise RuntimeError("price data is incomplete")

    if change is None:
        change = price - previous_close
    if pct is None:
        pct = 0 if previous_close == 0 else change / previous_close * 100

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "previousClose": previous_close,
        "change": change,
        "pct": pct,
        "currency": currency,
        "marketState": meta.get("marketState") or "",
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
        "regularMarketTime": meta.get("regularMarketTime"),
    }


def as_number(value):
    return value if isinstance(value, (int, float)) else None


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Serving real-data fund holdings app at http://{HOST}:{PORT}/")
    server.serve_forever()
