# Alpaca Client

Reference for `core/alpaca_client.py`. The module is the only place in
the US bot that speaks HTTP to the Alpaca REST API. Everything else in
`main.py` consumes dicts and lists returned from this client.

## Responsibilities

1. Authenticated HTTP requests to `https://paper-api.alpaca.markets/v2`
   (or the production URL via `ALPACA_BASE_URL`)
2. Retry and exponential backoff on transient failures
3. Robust parsing of the `Retry-After` header
4. In-memory caching of bar data (TTL-based)
5. Translation between Alpaca's wire format and a pandas `DataFrame`
   compatible with `BaseStrategy.analyze`

The class holds one `requests.Session` for HTTP connection pooling and
one `_BarCache` instance. The session is closed by `close()`, called
from `_handle_shutdown` in `main.py`.

## Constructor

Reads credentials from the environment:

- `ALPACA_API_KEY` — required
- `ALPACA_SECRET_KEY` — required
- `ALPACA_BASE_URL` — optional, defaults to
  `https://paper-api.alpaca.markets/v2`

Missing keys raise `ValueError`. The class does not attempt a test
request at construction time — misconfigured credentials surface at the
first API call, which is typically `is_market_open()` at the top of
`run()`.

The `Session` is preloaded with the three auth headers Alpaca requires:

```python
self._session.headers.update({
    'APCA-API-KEY-ID':     self.api_key,
    'APCA-API-SECRET-KEY': self.secret_key,
    'Content-Type':        'application/json',
})
```

## `_request` — the retry/backoff core

Private method used by every public call. Signature:

```python
def _request(method, endpoint, params=None, data=None) -> Any
```

Full body at `core/alpaca_client.py:72-143`. Logical flow:

```
for attempt in range(MAX_RETRIES=3):
    try:
        resp = session.request(method, url, timeout=10, params/json)

        if resp.status_code in {429, 503}:
            # Retryable HTTP error
            wait = parse_retry_after(resp) + random_jitter()
            log warning with body_preview
            sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json() if content else None

    except HTTPError as exc:
        if status == 404: return None     # semantic None for missing resource
        log error with body
        raise

    except (Timeout, ConnectionError) as exc:
        if last attempt: raise
        wait = 2**attempt + random_jitter()
        log warning
        sleep(wait)
```

### Retry semantics

| Condition | Behaviour |
|-----------|-----------|
| HTTP 200–299 | Return JSON body (or `None` for empty responses) |
| HTTP 404 | Return `None` — "resource not found" is a semantic absence, not an error. `get_position('NONEXISTENT')` returns `None`, not raises |
| HTTP 429 | Retry up to 3 times with `Retry-After` + jitter, then raise |
| HTTP 503 | Same as 429 |
| HTTP 400/401/403/5xx (other) | Raise `HTTPError` immediately, no retry |
| `Timeout` | Retry up to 3 times with exponential backoff, then raise |
| `ConnectionError` | Same as Timeout |

The 10-second `REQUEST_TIMEOUT` is a per-attempt timeout — the total
latency budget is `3 × 10s + 2 × (backoff)` ≈ 37 seconds worst case.

### `Retry-After` parsing (bug B9)

RFC 7231 allows `Retry-After` to be either:

- **Integer seconds** — e.g. `Retry-After: 120`
- **HTTP-date** — e.g. `Retry-After: Fri, 31 Dec 2099 23:59:59 GMT`

The pre-B9 code used `int(resp.headers.get('Retry-After', 2**attempt))`,
which crashes on an HTTP-date with `ValueError`, which in turn crashes
the entire `_request` call, which in turn crashes the bot.

Current code (`core/alpaca_client.py:96-105`):

```python
retry_after_hdr = resp.headers.get('Retry-After', '')
try:
    retry_after = int(retry_after_hdr)
except (ValueError, TypeError):
    retry_after = 2 ** attempt
wait = retry_after + random.uniform(0, 1)
```

On any unparseable value (HTTP-date, empty string, `None`, garbage),
the function falls back to exponential backoff. **We do not parse the
HTTP-date**. That's a deliberate simplification: Alpaca has never been
observed sending date-formatted Retry-After, and implementing full
RFC-7231 date parsing would add brittle code for a near-impossible
scenario. The fallback backoff is conservative enough (4s on the 2nd
retry, 8s on the 3rd) to handle any realistic rate-limit window.

### Body preview in logs

On any retryable response, the first 200 characters of the body are
included in the warning log:

```
WARNING  HTTP 429 on POST orders (attempt 1/3), retry in 2.7s |
         body='{"message":"Too many requests","code":42901000}'
```

This made 429/503 debugging actionable during Phase 2 — without it, a
flurry of retries looks identical to a network outage.

### Exhaustion

If all three attempts fail with `Timeout`/`ConnectionError`, the saved
exception is re-raised by `raise last_exc`. If all three attempts fail
with retryable HTTP statuses (429/503), the loop exits without
returning, and the safety-net `raise RuntimeError` at the end triggers.
In practice that second path never runs because a persistent 429 is
either the bot's fault (we're hammering the API) or an upstream
incident, both of which will surface via the HTTPError path first.

## `_BarCache`

A minimal TTL cache for bar (OHLCV) responses:

```python
class _BarCache:
    def __init__(self, ttl: int = 60):
        self._ttl = ttl
        self._store: dict[str, (timestamp, data)] = {}
```

Keyed on a tuple-like string `{symbol}:{timeframe}:{limit}:{start}:{end}`.
`get` returns the cached data if fresher than `ttl` seconds, else `None`
(and lazily deletes the stale entry).

The 60-second TTL matches the 5-minute main-loop cadence: within one
cycle, multiple strategies analysing the same symbol reuse the same bar
frame; the next cycle gets a fresh fetch.

There is no eviction beyond lazy cleanup on miss. For a 15-symbol
watchlist this is fine (at most 15 entries); if the watchlist grew to
hundreds, a bounded LRU would be appropriate.

## Public API

### Account

```python
get_account() -> dict
    GET /account
    Returns: Alpaca account snapshot (portfolio_value, cash,
             buying_power, status, ...)
```

### Positions

```python
get_positions() -> list[dict]
    GET /positions
    Returns: every open position as raw Alpaca dicts.
             Empty list if no positions.

get_position(symbol: str) -> dict | None
    GET /positions/{symbol}
    Returns: single position dict or None on 404.
```

Note the asymmetry: `get_positions` always returns a list (possibly
empty), but `get_position(SYM)` returns `None` if the symbol is not held
— because Alpaca responds 404 in that case, and `_request` maps 404 to
`None`. Callers should explicitly check for `None`:

```python
pos = alpaca.get_position('AAPL')
if pos is None:
    # Not currently held
    ...
else:
    qty = float(pos['qty'])
```

### Orders

```python
submit_order(symbol, qty, side, type='market', time_in_force='day',
             limit_price=None, stop_price=None) -> dict
    POST /orders
    Returns: order dict from Alpaca including 'id'.

get_orders(status='open') -> list[dict]
    GET /orders?status={status}
    Returns: list of order dicts.

cancel_order(order_id: str) -> None
    DELETE /orders/{order_id}
    Returns: None on success, raises on failure.
```

`submit_order` is the hot path of `_execute_buy`. It supports every order
type Alpaca exposes (`market`, `limit`, `stop`, `stop_limit`, …) via the
`type` parameter plus optional `limit_price` and `stop_price`. The
`time_in_force` default is `day` for market orders and callers override
to `gtc` for protective stop/limit orders so they survive the session.

### Market data

```python
get_bars(symbol, timeframe='1D', limit=100, start=None, end=None) -> list[dict]
    GET /stocks/bars?symbols={symbol}&timeframe={timeframe}&limit={limit}
    Returns: list of bar dicts with keys t/o/h/l/c/v (cached, TTL 60s).

get_ohlcv_dataframe(symbol, timeframe='1D', limit=100, start=None, end=None) -> pd.DataFrame
    Wraps get_bars and converts to a DataFrame with:
      - DatetimeIndex (UTC)
      - columns: open, high, low, close, volume
      - numeric dtypes (pd.to_numeric with coerce)
```

`get_ohlcv_dataframe` is what `_fetch_and_analyze` calls. If `get_bars`
returns `[]` (no data, HTTP 404, or an empty response), the function
returns an empty DataFrame with the expected columns so the strategy
can short-circuit cleanly on `len(candles) < min_candles`.

```python
get_latest_quote(symbol) -> dict     # GET /stocks/{symbol}/quotes/latest
get_latest_trade(symbol) -> dict     # GET /stocks/{symbol}/trades/latest
```

`get_latest_trade` is used by the trailing-stop loop to get a price that
is fresher than the last daily bar.

### Market status

```python
is_market_open() -> bool             # GET /clock → 'is_open' field
get_clock() -> dict                  # GET /clock (full response)
get_assets(status='active') -> list  # GET /assets
```

`is_market_open` is the top-of-loop gate — if the market is closed, the
bot sleeps 5 minutes and loops.

## Session lifecycle

```python
client = AlpacaClient()          # opens session
...                              # many requests
client.close()                   # closes session, releases pool
```

`close()` is called from `_handle_shutdown` (`main.py:724`) as the
**last** step of the shutdown sequence. Calling any method after
`close()` raises a `requests` error — this is why `close()` is the
last thing we do.

## Thread safety

`requests.Session` is documented as safe to share across threads, and
we rely on that in `_scan_all_symbols` (which runs up to 5 threads, each
calling `get_ohlcv_dataframe`). The `_BarCache` is a plain dict and is
**not** thread-safe — two threads writing the same key concurrently
could lose an entry. In practice this is fine because:

- Each worker fetches a distinct symbol; `cache_key` is unique per
  thread
- A lost cache entry just means the next call will refetch

If you parallelise further (e.g. multiple strategies per symbol in
parallel), add a `threading.Lock` to `_BarCache`.

## Error handling for callers

The bot's error-handling policy delegates to `_request`:

| Caller behaviour | Assumption |
|------------------|------------|
| `_execute_buy` wraps orders in try/except, returns False | Any raised exception is unrecoverable — abort the buy |
| `_scan_all_symbols` wraps fetch in try/except, skips symbol | Transient failure — try again next cycle |
| `_handle_shutdown` wraps each cancel in its own try | One cancel failing must not block the rest |
| `is_market_open` not wrapped | A raise here is fatal — the bot can't function without knowing market state |

The only time `_request` raises cleanly is after exhausting 3 retries.
Non-retryable 4xx errors raise on the first attempt. Either way, the
caller sees a `requests.HTTPError` or a `requests.Timeout` /
`requests.ConnectionError`.

## What this module does not do

- **No rate-limit coordination.** We rely on Alpaca's per-account limits
  (200 req/min on paper) being much higher than the bot's actual
  traffic (~15 symbols × 5-minute cycle ≈ 3 req/min scan + ~10
  req/cycle for account/positions/orders).
- **No WebSocket streaming.** The bot polls. This simplifies reasoning
  but means intraday price moves between cycles are invisible. Trailing
  stops are "delayed" by up to 5 minutes.
- **No order tracking beyond ID storage.** Once an order is submitted
  we only check it again if we need to cancel it (shutdown or trailing
  ratchet). Fill monitoring happens implicitly via `get_positions` on
  the next cycle.
- **No retry deduplication.** If a retryable HTTP call succeeded at the
  server but we never got the response, a retry can create a duplicate
  order. Alpaca supports client order IDs for idempotency, but the bot
  does not use them — this is a known gap and is mitigated in practice
  by the main-loop's `get_position(symbol)` check at the top of
  `_execute_buy`, which would catch a double-submit from the next
  cycle.

## Testing

`tests/test_bot.py::test_alpaca_connection` hits the real API (using
`.env` credentials) and verifies that `get_account` and `is_market_open`
respond. It is the only integration test in the suite. All other tests
mock nothing and run against pure Python (indicators, risk manager,
backtester).

The B9 fix was independently verified at implementation time by
constructing fake response objects with each header variant
(`"120"`, `"Fri, 31 Dec 2099 23:59:59 GMT"`, `""`, `None`, `"garbage"`)
and checking that the parse-or-fallback path does not raise. A proper
unit test for `_request` with a mock `Session` is in the backlog.
