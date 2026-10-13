from datetime import datetime, timedelta
from pathlib import Path
import re
import tempfile
import time
import requests
import pandas as pd

DATA_BASE = "https://api-t1.fyers.in/data"

class FyersClient:
    def __init__(self, client_id, access_token):
        if not client_id or not access_token:
            raise ValueError("Set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN in .env")
        self.client_id = client_id.strip()
        self.access_token = access_token.strip()
        self.headers = {"Authorization": f"{self.client_id}:{self.access_token}"}
        self.session = requests.Session()

    def _get(self, path, params):
        r = self.session.get(f"{DATA_BASE}{path}", params=params, headers=self.headers, timeout=35)
        try:
            body = r.json()
        except Exception:
            body = {"message": r.text[:500]}
        if r.status_code >= 400 or body.get("s") == "error":
            raise RuntimeError(f"FYERS {path} failed (HTTP {r.status_code}): {body}")
        return body

    def profile_check(self):
        # Profile endpoint is on API v3 host.
        r = self.session.get("https://api-t1.fyers.in/api/v3/profile", headers=self.headers, timeout=20)
        try: data = r.json()
        except Exception: data = {"message": r.text[:300]}
        if r.status_code >= 400 or data.get("s") == "error":
            raise RuntimeError(f"FYERS profile check failed: {data}")
        return data

    @staticmethod
    def _candles(body):
        rows = body.get("candles", [])
        if not rows:
            return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
        data = pd.DataFrame(rows)
        if data.shape[1] < 6:
            return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
        data = data.iloc[:, :6]
        data.columns = ["epoch","open","high","low","close","volume"]
        data["timestamp"] = pd.to_datetime(data["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return data[["timestamp","open","high","low","close","volume"]].drop_duplicates("timestamp").sort_values("timestamp")

    def history(self, symbol, start, end, resolution="15"):
        body = self._get("/history", {
            "symbol": symbol, "resolution": str(resolution), "date_format": "1",
            "range_from": str(start), "range_to": str(end)
        })
        return self._candles(body)

    def history_range(self, symbol, start, end, resolution="15", pause=0.18):
        a = datetime.strptime(str(start), "%Y-%m-%d").date()
        b = datetime.strptime(str(end), "%Y-%m-%d").date()
        if b < a: raise ValueError("End date is earlier than start date.")
        chunks, cur = [], a
        while cur <= b:
            last = min(cur + timedelta(days=89), b)
            df = self.history(symbol, cur.isoformat(), last.isoformat(), resolution)
            if not df.empty: chunks.append(df)
            cur = last + timedelta(days=1)
            time.sleep(pause)
        if not chunks:
            return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
        return pd.concat(chunks, ignore_index=True).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _walk_values(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k, v
                yield from FyersClient._walk_values(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from FyersClient._walk_values(v)

    @staticmethod
    def _strings(obj):
        """Yield all string leaves from a FYERS response payload."""
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for value in obj.values():
                yield from FyersClient._strings(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from FyersClient._strings(value)

    def expired_expiry_dates(self, underlying_symbol, start, end):
        # FYERS rejects expiry-date queries spanning more than 366 days.
        # Split long requests into <=366-day spans, then merge and deduplicate dates.
        start_dt = datetime.strptime(str(start), "%Y-%m-%d").date()
        end_dt = datetime.strptime(str(end), "%Y-%m-%d").date()
        if end_dt < start_dt:
            raise ValueError("End date is earlier than start date.")
        all_dates = set()
        cur = start_dt
        while cur <= end_dt:
            chunk_end = min(cur + timedelta(days=365), end_dt)
            body = self._get("/history/fno/expired/expiry-dates", {
                "symbol": underlying_symbol,
                "range_from": cur.isoformat(),
                "range_to": chunk_end.isoformat(),
                "date_format": "1"
            })
            chunk_dates = set()
            for k, v in self._walk_values(body):
                if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    if k.lower() in {"expiry", "expiry_date", "expirydate", "date"} or "expir" in k.lower():
                        chunk_dates.add(v)
            # Some response payloads provide a bare list of date strings.
            for _, v in self._walk_values(body):
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
                            chunk_dates.add(item)
            all_dates.update(chunk_dates)
            cur = chunk_end + timedelta(days=1)
            time.sleep(0.15)
        if not all_dates:
            raise RuntimeError(
                "Expiry endpoint returned no parseable dates across the requested range. "
                "Check FYERS response format, entitlement, symbol, and date range."
            )
        return sorted(all_dates)

    def expired_contracts(self, underlying_symbol, expiry_date):
        body = self._get("/history/fno/expired/underlying-symbols", {
            "symbol": underlying_symbol, "expiry_date": str(expiry_date)
        })
        # FYERS returns the contracts under data.contracts.options.  Search all
        # string leaves so this also remains compatible with minor nesting changes.
        symbols = {
            value.strip()
            for value in self._strings(body)
            if ":" in value and re.search(r"(?:CE|PE)$", value.upper())
        }
        # An otherwise successful endpoint can legitimately have no listed
        # options for an expiry (for example, a discontinued or unavailable
        # weekly series).  This is not an API error; callers can skip it and
        # continue collecting the rest of the requested history.
        return sorted(symbols)

    @staticmethod
    def parse_option_symbol(symbol, expiry_date=None):
        """Extract strike and option type from an expired-F&O contract symbol.

        FYERS expired SENSEX symbols have no separator between expiry and strike,
        e.g. ``BSE:SENSEX2510373200PE`` for the 2025-01-03 73,200 PE.  Knowing
        the expiry lets us remove its variable-width ``YYMDD`` token reliably.
        """
        s = symbol.upper().split(":")[-1]
        typ_match = re.search(r"(CE|PE)$", s)
        if not typ_match:
            return None
        option_type = typ_match.group(1)
        before = s[:typ_match.start()]
        numeric = re.sub(r"^[A-Z]+", "", before)
        if not numeric:
            return None

        if expiry_date is not None:
            expiry = pd.Timestamp(expiry_date)
            # FYERS' weekly-expiry encoding uses an unpadded month: YYMDD.
            expiry_token = f"{expiry:%y}{expiry.month}{expiry:%d}"
            if numeric.startswith(expiry_token):
                numeric = numeric[len(expiry_token):]

        # A fallback keeps older, differently encoded payloads usable.  Indian
        # index option strikes are normally five digits; use four only where it
        # is the only remaining plausible value.
        if not numeric.isdigit():
            return None
        if len(numeric) > 5:
            numeric = numeric[-5:]
        if len(numeric) < 4:
            return None
        strike = float(numeric)
        return {"strike": strike, "option_type": option_type}

    def expired_option_history(self, contract_symbol, start, end, resolution="15"):
        body = self._get("/history/fno/expired/historical-data", {
            "symbol": contract_symbol, "resolution": str(resolution), "date_format": "1",
            "range_from": str(start), "range_to": str(end),
            "include_greeks": "0", "include_oi": "0"
        })
        df = self._candles(body)
        return df

    # ------------------------------------------------------------------
    # Live (current-session) market data
    # ------------------------------------------------------------------
    SYM_MASTERS = {
        "BSE": "https://public.fyers.in/sym_details/BSE_FO.csv",
        "NSE": "https://public.fyers.in/sym_details/NSE_FO.csv",
    }

    def quotes(self, symbols):
        """Fetch live snapshots. `symbols` is a list of FYERS symbols
        (e.g. 'BSE:SENSEX-INDEX', 'BSE:SENSEX25OCT78000CE'). Returns the raw
        'd' list from the FYERS quotes payload (best-effort; keys vary)."""
        if isinstance(symbols, str):
            symbols = [symbols]
        out, batch = [], symbols
        for i in range(0, len(batch), 50):  # FYERS caps symbols per quotes call
            chunk = batch[i:i + 50]
            body = self._get("/quotes", {"symbols": ",".join(chunk)})
            out.extend(body.get("d") or [])
        return out

    def ltp(self, symbols):
        """Return {symbol: last_price} for the requested symbols."""
        prices = {}
        for row in self.quotes(symbols):
            v = row.get("v") or {}
            symbol = row.get("n") or v.get("symbol")
            price = v.get("lp") if isinstance(v, dict) else None
            if symbol is not None and price is not None:
                prices[symbol] = float(price)
        return prices

    def symbol_master(self, exchange="BSE", cache_hours=12):
        """Download and cache the public F&O symbol master CSV. Columns are
        positional in FYERS' file, so we map the fields we need by position."""
        path = Path(tempfile.gettempdir()) / f"fyers_sym_master_{exchange}.csv"
        if path.exists() and (time.time() - path.stat().st_mtime) < cache_hours * 3600:
            return pd.read_csv(path, low_memory=False)
        url = self.SYM_MASTERS.get(exchange.upper())
        if not url:
            raise ValueError(f"No symbol master URL for exchange {exchange}")
        r = self.session.get(url, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        return pd.read_csv(path, low_memory=False)

    def options_expiries(self, underlying_symbol):
        """List upcoming expiry dates for an underlying via the option-chain
        endpoint. FYERS returns them under 'data' with varying key names."""
        body = self._get("/options-chain-v3", {"symbol": underlying_symbol, "strikecount": 1})
        dates = set()
        for k, v in self._walk_values(body):
            if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                if "expir" in k.lower() or k.lower() == "date":
                    dates.add(v)
        return sorted(dates)

    def live_option_contracts(self, underlying="SENSEX", exchange="BSE"):
        """Parse the symbol master into live option contracts for one underlying.

        Returns a DataFrame with columns symbol, strike, option_type. The expiry
        itself is embedded in the symbol; callers match it against the current
        week/month rather than relying on the CSV.
        """
        df = self.symbol_master(exchange)
        # Column layout differs over time; locate the symbol column by content.
        sym_col = None
        for c in df.columns:
            sample = df[c].astype(str).head(50)
            if sample.str.contains(":", na=False).mean() > 0.5:
                sym_col = c
                break
        if sym_col is None:
            raise RuntimeError("Could not find a symbol column in the FYERS symbol master.")
        syms = df[sym_col].astype(str)
        mask = syms.str.contains(underlying, case=False, na=False) & syms.str.match(r".*[CP]E$", na=False)
        rows = []
        for s in syms[mask].unique():
            parsed = self._parse_live_option(s)
            if parsed:
                rows.append({"symbol": s, **parsed})
        if not rows:
            return pd.DataFrame(columns=["symbol", "strike", "option_type"])
        return pd.DataFrame(rows).drop_duplicates("symbol").reset_index(drop=True)

    @staticmethod
    def _parse_live_option(symbol):
        """Decode a current-series option symbol, e.g. BSE:SENSEX25OCT78000CE.

        Returns the strike and option type; the token prefix is stripped so
        minor spelling differences in the expiry field do not matter.
        """
        s = symbol.upper().split(":")[-1]
        m = re.search(r"(\d+)(CE|PE)$", s)
        if not m:
            return None
        strike_str, opt = m.group(1), m.group(2)
        # Leading digits may include a 1-2 digit day token for weekly series;
        # index strikes are 3-5 digits, so keep the last 5.
        numeric = strike_str[-5:]
        if not numeric.isdigit():
            return None
        try:
            strike = float(numeric)
        except ValueError:
            return None
        return {"strike": strike, "option_type": opt}
