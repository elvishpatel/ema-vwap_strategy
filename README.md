# SENSEX EMA/VWAP Options Research Dashboard

A local Streamlit dashboard for testing a 15-minute 9 EMA / 15 EMA crossover on SENSEX options. Nifty and Bank Nifty are also configurable. It has no ML and no order-placement code.

## Before you start
- Python 3.11 on Windows is recommended.
- A FYERS API app ID and valid access token are required for data downloads.
- This app does not place orders.
- It does not promise to increase win rate. Filters are compared and the training period can select a candidate; the out-of-sample test is reported separately.
- Historical expired F&O data availability and API quotas depend on FYERS account/API entitlements. Standard-plan expired intraday access may be limited (FYERS currently documents 10 expired-F&O intraday requests/day on Standard, with higher access on Prime). Large intraday history downloads may therefore not be possible in one sitting. See FYERS current limits before planning a full backfill.
- Do not share `.env` or your access token.

## Run
1. Extract the ZIP.
2. Double-click `run_dashboard.bat`.
3. Copy `.env.example` to `.env` if it is not created and enter your FYERS credentials.
4. In the dashboard, click **Test FYERS connection**.
5. Download index candles for the target period.
6. Obtain actual option candles via **Download expired option candles** if your API entitlement supports it, or load a CSV from a source that provides real historical option contracts.
7. Select SENSEX, date range and costs, then click **Run backtest**.

## Historical option data: important
A correct option backtest needs actual contract candles including expired options. Today's active option contracts are not a valid substitute for past expired contracts. The downloader uses FYERS expired-F&O endpoints:
- `/data/history/fno/expired/expiry-dates`
- `/data/history/fno/expired/underlying-symbols`
- `/data/history/fno/expired/historical-data`

FYERS can change endpoint behavior, request permissions and payload shapes. The app tests endpoints and shows API errors instead of fabricating data. It caches downloaded candles locally. Because API quotas can restrict historical intraday downloads, the app cannot guarantee that every date/contract in a long period is downloadable on every account.

## Data CSV schema
Option candles require:
`timestamp,symbol,underlying,expiry,strike,option_type,open,high,low,close,volume`
Optional `lot_size` column can specify contract-specific lot size. Timestamps should be India local exchange time; expiry is `YYYY-MM-DD`; option_type is CE or PE. One row per contract/candle.

If you have an option CSV, use the dashboard's upload control. The app saves uploaded data to `data/options_15m.csv`.

## Strategy and validation
- Signals are evaluated at the close of a completed 15-minute underlying candle.
- Entries occur at the next available option candle OPEN, adjusted for adverse slippage.
- Bullish crossover buys CE; bearish crossover buys PE.
- Filters compared: baseline, VWAP, VWAP + EMA slope, VWAP + volume, VWAP + ADX.
- Enhanced filters: `trend` (crossover + VWAP + higher-timeframe bias gate), `trend_orb` (+ opening-range breakout in the trade direction), `trend_orb_bt` (+ continuation break of the prior candle).
- Higher-timeframe bias is built from 1H and/or daily EMAs, lagged one completed bar to avoid look-ahead. Opening range defaults to the first 09:15–09:45 candles.
- Position sizing can be risk-based (fixed % of equity risked at the stop, capped by a max premium-deployed %) or the legacy fixed one-lot.
- Optional structure stop floors the stop at the recent premium swing low.
- Session VWAP resets daily; it is a proxy on an index feed, where volume may not be meaningful. Treat VWAP results as hypotheses.
- Premium stop/target use option-candle ATR; if both stop and target are touched in one candle, stop is assumed first.
- Max trades/day and last entry/force-exit times can be adjusted in the dashboard.
- Chronological split: filters are ranked using training-period metrics only. Test metrics remain separate. Results are still vulnerable to data quality, market regime and execution assumptions.
- Costs are configurable estimates. Confirm current broker/exchange taxes and charges for the date and product.

## Outputs
`reports/` stores trade ledger, filter comparison, and equity curve CSVs.
