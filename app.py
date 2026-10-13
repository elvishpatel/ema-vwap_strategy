import os, json, time, traceback
from pathlib import Path
from datetime import date, timedelta
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv

from src.fyers_client import FyersClient
from src.strategy import indicators, prepare_options, run_comparison, metrics, equity_curve
from src.telegram_notifier import TelegramNotifier

load_dotenv()
ROOT=Path(__file__).parent
CFG=json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
DATA=ROOT/"data"; REPORTS=ROOT/"reports"
DATA.mkdir(exist_ok=True); REPORTS.mkdir(exist_ok=True)

st.set_page_config(page_title="EMA + VWAP Options Lab", page_icon="📈", layout="wide")
st.title("EMA 9/15 + VWAP | Options Backtesting Lab")
st.caption("15-minute research dashboard · FYERS data · No ML · No order placement")

with st.sidebar:
    st.header("Backtest settings")
    market=st.selectbox("Underlying",["SENSEX","NIFTY","BANKNIFTY"],index=0)
    start=st.date_input("Start date",value=date(2025,1,1),max_value=date.today())
    end=st.date_input("End date",value=min(date.today(),date(2026,9,30)),max_value=date.today())
    st.divider()
    initial_capital=st.number_input("Starting capital (₹)",min_value=1000,value=int(CFG["initial_capital"]),step=1000)
    lot_size=st.number_input("Fallback lot size",min_value=1,value=int(CFG["lot_sizes"][market]),step=1,
                             help="Used only if the option CSV lacks a lot_size. Lot sizes can change historically.")
    st.caption("CSV lot_size column overrides this fallback.")
    slippage=st.number_input("Slippage per side (%)",min_value=0.0,max_value=2.0,value=CFG["slippage_pct_per_side"]*100,step=0.05)/100
    brokerage=st.number_input("Brokerage per order (₹)",min_value=0.0,value=float(CFG["brokerage_per_order"]),step=5.0)
    other_cost=st.number_input("Other charges per side (% turnover)",min_value=0.0,max_value=3.0,value=CFG["other_cost_pct_per_side"]*100,step=0.01)/100
    st.divider()
    st.subheader("Strategy / risk")
    stop_atr=st.number_input("Premium stop ATR multiple",min_value=0.25,max_value=5.0,value=float(CFG["stop_atr"]),step=0.25)
    target_atr=st.number_input("Premium target ATR multiple",min_value=0.25,max_value=8.0,value=float(CFG["target_atr"]),step=0.25)
    max_hold=st.number_input("Maximum holding bars",min_value=1,max_value=30,value=int(CFG["max_holding_bars"]))
    max_dte=st.number_input("Maximum expiry DTE",min_value=0,max_value=45,value=int(CFG["max_dte"]))
    trades_day=st.number_input("Max entries per day",min_value=1,max_value=10,value=int(CFG["max_trades_per_day"]))
    train_pct=st.slider("Training data (%)",min_value=50,max_value=85,value=int(CFG["train_fraction"]*100),step=5)
    min_train_trades=st.number_input("Minimum training trades to rank filter",min_value=5,max_value=200,value=int(CFG["min_train_trades"]),step=5)
    st.divider()
    st.subheader("Context filters")
    htf_bias=st.selectbox("Higher-timeframe bias gate",["none","h1","daily","h1_daily"],
                          index=["none","h1","daily","h1_daily"].index(CFG.get("htf_bias","h1")),
                          help="Only take a trade when the higher timeframe agrees: 1H EMAs, daily EMA, or both.")
    or_end=st.text_input("Opening-range end (HH:MM)",value=CFG.get("or_end_time","09:45"),
                         help="Opening range = first candles of the session, up to this time.")
    st.divider()
    st.subheader("Sizing & exits")
    sizing_mode=st.selectbox("Position sizing",["fixed_lots","risk"],
                             index=["fixed_lots","risk"].index(CFG.get("sizing_mode","risk")),
                             help="'risk' scales quantity so the stop risks a fixed % of equity.")
    risk_pct=st.number_input("Risk per trade (% of equity)",min_value=0.1,max_value=10.0,
                             value=float(CFG.get("risk_pct",0.01))*100,step=0.1)/100
    max_cap_pct=st.number_input("Max premium deployed (% of equity)",min_value=5.0,max_value=100.0,
                                value=float(CFG.get("max_capital_pct",0.6))*100,step=5.0)/100
    exit_on_opposite=st.checkbox("Exit on opposite EMA cross",value=bool(CFG.get("exit_on_opposite",True)))
    stop_mode=st.selectbox("Stop basis",["atr","structure"],
                           index=["atr","structure"].index(CFG.get("stop_mode","atr")),
                           help="'structure' floors the stop at the recent premium swing.")

def current_config():
    c=CFG.copy()
    c["initial_capital"]=initial_capital
    c["lot_sizes"]=CFG["lot_sizes"].copy(); c["lot_sizes"][market]=int(lot_size)
    c["slippage_pct_per_side"]=slippage
    c["brokerage_per_order"]=brokerage
    c["other_cost_pct_per_side"]=other_cost
    c["stop_atr"]=stop_atr; c["target_atr"]=target_atr; c["max_holding_bars"]=int(max_hold)
    c["max_dte"]=int(max_dte); c["max_trades_per_day"]=int(trades_day)
    c["train_fraction"]=train_pct/100; c["min_train_trades"]=int(min_train_trades)
    c["htf_bias"]=htf_bias; c["or_end_time"]=or_end
    c["sizing_mode"]=sizing_mode; c["risk_pct"]=risk_pct; c["max_capital_pct"]=max_cap_pct
    c["exit_on_opposite"]=exit_on_opposite; c["stop_mode"]=stop_mode
    return c

def client():
    return FyersClient(os.getenv("FYERS_CLIENT_ID",""),os.getenv("FYERS_ACCESS_TOKEN",""))

def load_index(mkt):
    p=DATA/f"{mkt.lower()}_index_15m.csv"
    if not p.exists(): return pd.DataFrame()
    return pd.read_csv(p,parse_dates=["timestamp"])

def load_options():
    p=DATA/"options_15m.csv"
    if not p.exists(): return pd.DataFrame()
    return pd.read_csv(p,parse_dates=["timestamp"])

tabs=st.tabs(["Dashboard & Backtest","Data & FYERS","Live scanner","Strategy notes"])

with tabs[0]:
    st.subheader(f"{market} | 15-minute options strategy")
    index_df=load_index(market)
    options_df=load_options()
    uploaded=st.file_uploader("Optional: upload actual historical option candles CSV",type=["csv"],key="option_upload")
    if uploaded is not None:
        try:
            options_df=prepare_options(pd.read_csv(uploaded))
            options_df.to_csv(DATA/"options_15m.csv",index=False)
            st.success(f"Loaded {len(options_df):,} option candles and saved to data/options_15m.csv")
        except Exception as e:
            st.error(f"Could not load option CSV: {e}")
    c1,c2,c3=st.columns(3)
    c1.metric("Underlying candles",f"{len(index_df):,}")
    c2.metric("Option candles",f"{len(options_df):,}")
    c3.metric("Unique option contracts",f"{options_df.symbol.nunique():,}" if not options_df.empty and "symbol" in options_df else "0")
    if not index_df.empty:
        index_df["timestamp"]=pd.to_datetime(index_df.timestamp)
        index_df=index_df[(index_df.timestamp.dt.date>=start)&(index_df.timestamp.dt.date<=end)]
    if not index_df.empty and not options_df.empty:
        if st.button("Run backtest",type="primary",use_container_width=True):
            with st.spinner("Running five filters on actual option candles..."):
                try:
                    cfg=current_config()
                    comparison,trades,selected,split_time=run_comparison(index_df,options_df,market,cfg)
                    comparison.to_csv(REPORTS/"filter_comparison.csv",index=False)
                    trades.to_csv(REPORTS/"trade_ledger.csv",index=False)
                    if not trades.empty:
                        equity_curve(trades,trades.iloc[0].get("initial_capital",cfg["initial_capital"])).to_csv(REPORTS/"equity_curve.csv",index=False)
                    st.session_state["comparison"]=comparison
                    st.session_state["trades"]=trades
                    st.session_state["selected"]=selected
                    st.session_state["split_time"]=split_time
                    st.session_state["run_market"]=market
                    st.session_state["run_capital"]=initial_capital
                except Exception as e:
                    st.error(f"Backtest failed: {e}")
                    st.code(traceback.format_exc())
    elif st.button("Run backtest",type="primary",use_container_width=True):
        st.warning("Need both underlying 15-minute candles and actual historical option candles. Use Data & FYERS to download, or upload a CSV.")
    if "comparison" in st.session_state and st.session_state.get("run_market")==market:
        comp=st.session_state["comparison"]
        trades=st.session_state["trades"]
        selected=st.session_state["selected"]
        split_time=st.session_state["split_time"]
        st.divider()
        st.subheader("Results")
        st.info(f"Training-only filter selection: **{selected}** · Train/test boundary: {split_time}. The selected filter is chosen from training results only, not test results.")
        test=comp[comp.period=="test"].copy()
        bestrow=test[test["filter"]==selected]
        if not bestrow.empty:
            r=bestrow.iloc[0]
            a,b,c,d,e=st.columns(5)
            a.metric("Test net P&L",f"₹{r.net_pnl:,.2f}")
            b.metric("Win rate (accuracy)",f"{r.win_rate_pct:.2f}%")
            c.metric("Profit factor",f"{r.profit_factor:.2f}")
            d.metric("Test trades",f"{int(r.trades)}")
            e.metric("Max drawdown",f"₹{r.max_drawdown:,.2f}")
        st.caption("All P&L is simulated, per one lot, using the current cost assumptions and available CSV contracts. It is not a prediction or guarantee.")
        st.markdown("#### Filter comparison — test period")
        show=test[["filter","trades","win_rate_pct","net_pnl","profit_factor","avg_trade","max_drawdown","max_consecutive_losses","skipped_no_contract"]].sort_values("net_pnl",ascending=False)
        st.dataframe(show,use_container_width=True,hide_index=True)
        st.markdown("#### Training-period comparison")
        st.dataframe(comp[comp.period=="train"][["filter","trades","win_rate_pct","net_pnl","profit_factor","avg_trade","max_drawdown","max_consecutive_losses"]].sort_values("profit_factor",ascending=False),use_container_width=True,hide_index=True)
        if not trades.empty:
            selected_test=trades[(trades["filter"]==selected)&(trades["period"]=="test")].copy()
            if not selected_test.empty:
                selected_test["exit_time"]=pd.to_datetime(selected_test.exit_time)
                selected_test=selected_test.sort_values("exit_time")
                selected_test["equity"]=initial_capital+selected_test.net_pnl.cumsum()
                selected_test["drawdown"]=selected_test.equity-selected_test.equity.cummax()
                st.markdown("#### Test equity curve")
                fig=px.line(selected_test,x="exit_time",y="equity",title="Simulated equity (₹)",labels={"exit_time":"Exit time","equity":"Equity (₹)"})
                st.plotly_chart(fig,use_container_width=True)
                st.markdown("#### Monthly test P&L")
                selected_test["month"]=selected_test.exit_time.dt.to_period("M").astype(str)
                monthly=selected_test.groupby("month",as_index=False).net_pnl.sum()
                st.plotly_chart(px.bar(monthly,x="month",y="net_pnl",title="Monthly net P&L (₹)",labels={"month":"Month","net_pnl":"Net P&L (₹)"}),use_container_width=True)
                st.markdown("#### Trade ledger — selected filter, test period")
                st.dataframe(selected_test,use_container_width=True,hide_index=True)
                st.download_button("Download selected test trades CSV",selected_test.to_csv(index=False).encode(),file_name="sensex_selected_test_trades.csv",mime="text/csv")
            else:
                st.warning("No selected-filter trades in the test period. Inspect data coverage, contract mapping and skipped-signal counts.")
        with st.expander("Data coverage / skipped signals"):
            st.dataframe(comp[["filter","period","signals","skipped_no_contract","trades"]],use_container_width=True,hide_index=True)
        st.download_button("Download all results CSV",comp.to_csv(index=False).encode(),file_name="filter_comparison.csv",mime="text/csv")
    else:
        st.info("Download data first, then run the backtest. A valid options result requires historical option-premium candles, including expired contracts.")

with tabs[1]:
    st.subheader("Data setup")
    st.warning("Expired options history may be quota-limited by FYERS plan. The downloader will report API errors and will not fabricate prices.")
    st.markdown("#### 1. Check FYERS credentials")
    st.code("FYERS_CLIENT_ID=YOUR_APP_ID-100\nFYERS_ACCESS_TOKEN=YOUR_ACCESS_TOKEN",language="text")
    if st.button("Test FYERS connection"):
        try:
            result=client().profile_check()
            st.success("FYERS profile endpoint responded successfully.")
            st.json({k:v for k,v in result.items() if k not in {"data"}})
        except Exception as e: st.error(str(e))
    st.markdown("#### 2. Download index candles")
    if st.button(f"Download {market} 15-minute index candles"):
        try:
            with st.spinner("Downloading index candles..."):
                df=client().history_range(CFG["symbols"][market],start.isoformat(),end.isoformat(),CFG["resolution"])
                if df.empty: st.warning("No candles returned. Verify symbol, dates, holidays and API access.")
                else:
                    df.to_csv(DATA/f"{market.lower()}_index_15m.csv",index=False)
                    st.success(f"Saved {len(df):,} candles to data/{market.lower()}_index_15m.csv")
                    st.dataframe(df.tail(10),use_container_width=True)
        except Exception as e: st.error(str(e))
    st.markdown("#### 3. Download expired option candles")
    st.write("This downloader gets expiry dates and contract symbols, selects a small number of strikes around the underlying level for each expiry, and requests real contract candles. Large periods can exceed account request quotas.")
    contracts_per_side=st.slider("Strikes to keep on each side of ATM",1,4,2)
    if st.button(f"Download {market} expired option candles (limited strike set)",type="secondary"):
        try:
            api=client()
            idx=load_index(market)
            if idx.empty:
                st.error("Download index candles first; the downloader uses index closes to select near-ATM strikes.")
            else:
                idx["timestamp"]=pd.to_datetime(idx.timestamp)
                idx=idx[(idx.timestamp.dt.date>=start)&(idx.timestamp.dt.date<=end)]
                expiries=api.expired_expiry_dates(CFG["symbols"][market],start.isoformat(),end.isoformat())
                status=st.empty(); progress=st.progress(0)
                downloaded=[]; requested=0; failures=[]
                # Each expiry contributes near-ATM CE/PE symbols; the candle range is capped to max DTE before expiry.
                for ei,exp in enumerate(expiries):
                    expdate=pd.to_datetime(exp).date()
                    eligible=idx[idx.timestamp.dt.date<=expdate]
                    if eligible.empty: continue
                    anchor=eligible.iloc[-1]
                    spot=float(anchor.close)
                    symbols=api.expired_contracts(CFG["symbols"][market],exp)
                    parsed=[]
                    for sym in symbols:
                        p=api.parse_option_symbol(sym, exp)
                        if p: parsed.append({"symbol":sym,**p})
                    if not parsed:
                        failures.append(f"{exp}: FYERS returned no option contracts; skipped this expiry")
                        continue
                    pdf=pd.DataFrame(parsed)
                    # nearest strikes across distinct strikes; then include CE and PE at selected strikes
                    unique=sorted(pdf.strike.unique(),key=lambda x:abs(x-spot))
                    keep=set(unique[:max(1,2*contracts_per_side+1)])
                    pdf=pdf[pdf.strike.isin(keep)]
                    lo=max(start,expdate-timedelta(days=int(max_dte)+3))
                    hi=min(end,expdate)
                    for _, con in pdf.iterrows():
                        requested+=1
                        status.text(f"Expiry {ei+1}/{len(expiries)} · request {requested} · {con['symbol']}")
                        try:
                            candles=api.expired_option_history(con["symbol"],lo.isoformat(),hi.isoformat(),CFG["resolution"])
                            if not candles.empty:
                                candles["symbol"]=con["symbol"]; candles["underlying"]=market
                                candles["expiry"]=exp; candles["strike"]=float(con["strike"])
                                candles["option_type"]=con["option_type"]
                                candles["lot_size"]=int(lot_size)
                                downloaded.append(candles)
                        except Exception as ex:
                            failures.append(f"{con['symbol']}: {str(ex)[:220]}")
                        progress.progress(min(1.0,(ei+1)/max(1,len(expiries))))
                        time.sleep(0.22)
                if downloaded:
                    new=pd.concat(downloaded,ignore_index=True)
                    out=DATA/"options_15m.csv"
                    if out.exists():
                        old=pd.read_csv(out,parse_dates=["timestamp"])
                        new=pd.concat([old,new],ignore_index=True).drop_duplicates(["timestamp","symbol"],keep="last")
                    new.to_csv(out,index=False)
                    st.success(f"Saved/updated {len(new):,} option candles in data/options_15m.csv from {requested} contract requests.")
                    st.caption("Check that the selected contracts cover your desired dates and that the API did not truncate the download.")
                else:
                    st.error("No option candles downloaded.")
                if failures:
                    with st.expander(f"Request notes / skipped contracts ({len(failures)})"):
                        st.text("\n".join(failures[:100]))
        except Exception as e:
            st.error(f"Expired option download failed: {e}")
            st.caption("Check API access, symbol format, endpoint entitlement and expired-F&O request quota.")
    st.markdown("#### Data inventory")
    for p in sorted(DATA.glob("*.csv")):
        try:
            df=pd.read_csv(p)
            st.write(f"**{p.name}** — {len(df):,} rows · {len(df.columns)} columns")
        except Exception: st.write(p.name)

with tabs[2]:
    st.subheader("Live scanner")
    st.caption("Detects a confirmed setup on the last CLOSED 15-minute candle and sends it to Telegram. "
               "This tab tests the connection and lets you scan once; for continuous scanning, run run_scanner.bat.")
    live_market=st.selectbox("Live market",["SENSEX","NIFTY","BANKNIFTY"],index=0,key="live_mkt")
    live_filter=st.selectbox("Live filter",["trend","trend_orb","trend_orb_bt","vwap","baseline"],
                             index=0,key="live_flt")
    lc1,lc2=st.columns(2)
    if lc1.button("Send test Telegram message"):
        try:
            TelegramNotifier().send("✅ Test message from the EMA/VWAP dashboard.")
            st.success("Test message sent. Check your Telegram.")
        except Exception as e:
            st.error(f"Telegram test failed: {e}")
    if lc2.button("Scan once now (last closed candle)"):
        try:
            import live_scanner as ls
            api=client()
            details,bar,reason=ls.scan_once(api,CFG,TelegramNotifier(),live_market,live_filter,dry_run=False,seen=set())
            if details:
                st.success(f"Setup found on bar {bar} — alert sent.")
                st.json(details)
            else:
                st.info(f"No alert for the last closed candle ({bar}). Reason: {reason}.")
        except Exception as e:
            st.error(f"Scan failed: {e}")
            st.caption("Check .env credentials and that the market is open / data is available.")
    st.divider()
    st.markdown("#### How to run it live, for free")
    st.markdown("""
1. Create a Telegram bot with **@BotFather** and get the bot token; get your chat id from **@userinfobot**.
2. Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
3. Run `run_scanner.bat`, or schedule it at 09:15 with Windows **Task Scheduler**.
4. To run 24/7 without keeping your PC on, use a free **Oracle Cloud Always Free** VM (see README).
""")

with tabs[3]:
    st.subheader("Strategy rules and caveats")
    st.markdown("""
**Signal**
- Bullish 9 EMA crosses above 15 EMA → buy CE.
- Bearish 9 EMA crosses below 15 EMA → buy PE.
- Optional confirmations: close beyond session VWAP; EMA slope; relative volume; ADX.
- Signal is evaluated after the underlying candle closes; entry is the next option candle open.

**Enhanced "trend" filters**
- `trend` — crossover + VWAP + higher-timeframe bias gate (1H/daily EMAs). Only trades in the direction the higher timeframe agrees with. Cuts the count but sharpens quality.
- `trend_orb` — additionally requires the session opening range (default first 09:15–09:45 candles) to be broken in the trade direction.
- `trend_orb_bt` — additionally requires a continuation break of the prior candle's high/low.
- Bias is lagged by one completed higher-timeframe bar, so no look-ahead.
- Opening range and bias are recomputed inside the train/test split to avoid leakage.

**Risk/execution**
- Premium ATR stop/target, configurable max holding bars, max entries per day, latest entry time, and forced session exit.
- Position sizing: `risk` mode scales quantity so the stop risks a fixed % of equity, capped by a maximum premium-deployed %; `fixed_lots` keeps the legacy one-lot behaviour.
- Optional structure stop: floors the stop at the recent premium swing low.
- Adverse slippage is applied on both entry and exit; brokerage and other turnover-based charges are deducted.
- If a candle hits both stop and target, stop is assumed first.
- Option positions are not intentionally held overnight.
- The option contract is selected from the available CSV by nearest eligible expiry and nearest strike to index close. The backtest cannot use a contract that is absent from the dataset.

**Critical limitations**
- The index's candle volume is not the same as traded volume in an index future or ETF. VWAP on an index can be a weak or misleading filter.
- The downloaded contract set is intentionally limited to near-ATM strikes; results may skip signals where a matching contract candle is missing.
- Lot size history and charges may change. CSV `lot_size` is preferable to a single static fallback for multi-year tests.
- A higher win rate can be obtained by over-filtering but may lower net profit and leave too few trades. Compare win rate, expectancy, profit factor, drawdown and sample size together.
- This is a research tool, not a guarantee of profitable trading.
""")
