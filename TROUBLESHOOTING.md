# Troubleshooting

- **FYERS connection fails:** make sure the access token is current and `FYERS_CLIENT_ID` includes the app suffix expected by your FYERS API app. Never paste the token into chat.
- **Expired F&O endpoint returns access/limit error:** this is an API entitlement or quota issue, not a strategy error. Wait for quota reset or verify your FYERS plan. The project cannot create genuine expired option prices when the API does not supply them.
- **No option contracts parsed:** FYERS may have changed symbol naming or response structure. Inspect the error in the Data tab and update `src/fyers_client.py`'s response parser for the current payload.
- **No trades:** check date range, whether both index and option candle CSVs overlap, expiry DTE settings, and whether option timestamps match the 15-minute index timestamps.
- **Lot size:** if the dataset spans lot-size changes, include an accurate `lot_size` value for each historical contract row. A single fallback lot size across all years can distort rupee P&L.
- **Win rate rises but P&L falls:** this is possible when filters remove both losing and profitable trades. Do not optimize win rate alone.


## FYERS error: HTTP 422 / code -50 / "Date range cannot exceed 366 days"
The expiry-date request has a maximum 366-day date span. The updated project splits a longer requested range into 365-day chunks, combines the expiry dates, and deduplicates them. Restart Streamlit after replacing the project files. If the same error persists, confirm you are running the newly extracted project rather than an older folder.
