# State Persistence

Reference for `core/state_persistence.py`. The module implements the
on-disk journal and state snapshot that let the bot survive restarts and
detect drift between its own view of the world and the broker's.

## Purpose

The live bot has three kinds of state:

1. **Ephemeral caches** — `_last_atr`, `_stop_order_ids`, `_current_stops`
   in `TradingBot`. Rebuilt from scratch on every startup; no
   persistence.
2. **Durable history** — every trade the bot has ever executed. Stored
   in `trades_journal.jsonl`, append-only.
3. **Latest snapshot** — a single dict describing the bot's view of the
   world at the end of the last cycle. Stored in `bot_state.json`,
   overwritten atomically.

On startup, `main.py::_recover_state` reads the snapshot, calls
`reconcile_with_broker` against the live Alpaca positions, and alerts on
any discrepancy. The journal is not read at startup — it is the
audit trail, not the recovery source.

## File locations

Both files live under `DEFAULT_DATA_DIR`, which is
`<repo_root>/data/`. The constructor creates the directory if it does not
exist and chmods it to `0o700` (owner-only) as a VPS multi-tenant safety
measure.

```
data/
├── trades_journal.jsonl       # append-only, 0o600
├── bot_state.json             # overwritten atomically, 0o600
├── bot_state.tmp              # write-and-rename staging (transient)
├── bot_state.corrupted        # renamed on load failure (forensic)
└── critical_alerts.jsonl      # Telegram-failure fallback (owned by main.py)
```

All files are `0o600`. The journal contains symbol, side, quantity,
price, and strategy name — commercially sensitive. The state file
contains portfolio value and open position details. Neither should be
world-readable on a shared host.

## `StatePersistence` class

Single constructor parameter: `data_dir` (defaults to `<repo>/data`).
The class holds paths only; there is no open file handle between calls.
This means:

- Each `append_trade` opens, writes, fsyncs, closes.
- Each `save_state` writes to a temp file then renames.
- The class is **safe to instantiate multiple times** in the same process
  (although we only do it once in practice). No locking is needed because
  all writes go through O_APPEND or atomic rename.

## `append_trade(record: dict) -> None`

Appends a single JSON object to `trades_journal.jsonl`, one line per
record. The contract:

```python
sp.append_trade({
    "symbol": "AAPL",
    "side": "buy",
    "qty": 42,
    "price": 178.43,
    "strategy": "MomentumStrategy",
    "stop_loss": 175.12,
    "take_profit": 184.95,
    "reason": "strength=0.72",
})
```

A `timestamp` field is automatically added (UTC ISO 8601) unless the
caller already provided one.

### Atomicity

The implementation (at `core/state_persistence.py:44-67`):

```python
flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
fd = os.open(self._journal_path, flags, 0o600)
with os.fdopen(fd, "a") as f:
    f.write(line)
    f.flush()
    os.fsync(f.fileno())
```

Three guarantees:

1. **`O_APPEND` atomicity.** On POSIX, a single `write(2)` smaller than
   `PIPE_BUF` (4 KB on Linux, 512 B on macOS historically, 4 KB on
   modern macOS) is atomic with `O_APPEND` — no interleaving even under
   concurrent writers. A typical trade record is ~300 bytes, well under
   the limit.
2. **`fsync` durability.** The fsync call forces the write through the
   OS page cache to stable storage before the function returns. A
   power loss after `append_trade` returns will not lose the record.
3. **`0o600` on file creation.** Using `os.open` with the mode
   argument means the file is **created** with 0600, not created at 0644
   then chmod'd. There is no window where a world-readable file exists.

### Error handling

If the write fails (disk full, permission denied), `append_trade` logs
CRITICAL and **re-raises** the `OSError`. This is intentional — the
caller in `main.py::_execute_buy` has its own try/except in "Section 2"
(see [`execution-flow.md`](execution-flow.md) §"Step 5"). When that
try/except catches the raised OSError it:

1. Logs CRITICAL with the symbol
2. Fires a Telegram risk alert ("Trade journal failed for SYM")
3. Does **not** return False (the broker orders are already live)

So a journal failure is loud but non-fatal. The trade exists at the
broker even if the journal missed it — a human can reconstruct the
missing record from Alpaca's trade history.

## `load_trades() -> list[dict]`

Reads the whole journal into memory. Handles corruption gracefully:

```python
for lineno, raw in enumerate(f, 1):
    line = raw.strip()
    if not line:
        continue
    try:
        trades.append(json.loads(line))
    except json.JSONDecodeError as exc:
        logger.warning(f"Skipping corrupted journal line {lineno}: {exc}")
```

Corrupted lines are **skipped with a warning**, not fatal. This is
important because the most common corruption is a truncated tail: a
crash mid-write leaves the last line incomplete. Skipping lets the bot
load all the intact records before the crash and continue operating.

If the file itself is unreadable (`OSError`), the function logs and
returns an empty list — the bot treats "no history" as equivalent to
"couldn't read history" at startup. The state snapshot is the primary
recovery mechanism, not the journal.

## `save_state(state: dict) -> None`

Writes a full bot state snapshot. Overwrites the previous version
atomically via the temp-file-then-rename pattern:

```python
state.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
tmp = self._state_path.with_suffix(".tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
fd = os.open(tmp, flags, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(state, f, indent=2)
os.replace(tmp, self._state_path)
```

Guarantees:

- The final `bot_state.json` is never partially written. `os.replace`
  is atomic on POSIX and on Windows (since Python 3.3).
- A crash mid-write leaves at most a `bot_state.tmp` file; the main
  state file is untouched.
- `0o600` on the temp file, preserved across the rename.
- `saved_at` is auto-populated if the caller didn't provide one.

`save_state` does **not** raise on filesystem errors — it propagates
`OSError`, but the two callers in `main.py` (`run()` at end of cycle,
and `_handle_shutdown()`) both wrap it in a try/except that logs and
continues. A failure to snapshot is logged but the bot keeps running.

### Snapshot schema (convention)

There is no formal schema, but both `main.py` call sites use these
fields:

```json
{
  "saved_at": "2026-04-09T15:30:00+00:00",
  "portfolio_value": 100423.17,
  "cash": 12456.88,
  "open_positions": [
    {"symbol": "AAPL", "qty": 42, "entry_price": 178.43},
    ...
  ]
}
```

`_handle_shutdown` additionally writes:

```json
{
  "shutdown": true,
  "shutdown_at": "...",
  "open_positions_count": 3,
  "failed_cancels": 0
}
```

The startup `_recover_state` only looks at `saved_at` and
`open_positions` — other fields are for observability.

## `load_state() -> dict`

Reads `bot_state.json` back as a dict. Returns `{}` if the file does
not exist or is unreadable.

### Corruption handling

If the file exists but `json.load` fails (truncated write, manual edit
gone wrong), the function:

1. Logs ERROR with the filename and the exception
2. **Renames** the bad file to `bot_state.corrupted` so the next save
   does not overwrite it
3. Returns `{}`

The rename is best-effort: if it fails, the bot still starts clean
because `{}` is returned either way. The preserved `.corrupted` file is
available for post-mortem analysis.

This is the reason the bot will "start fresh" after an unclean
shutdown where the state file was truncated — it's strictly safer than
crashlooping on a partially-readable state.

## `reconcile_with_broker(journal_positions, broker_positions) -> list[str]`

Compares two position dicts and returns a list of discrepancy messages.
Empty list means everything matches.

### Input shape

Both arguments are dicts keyed by symbol, with any value that has a
`qty` key accessible via `.get("qty", 0)`:

```python
journal_positions = {"AAPL": {"qty": 42}}
broker_positions  = {"AAPL": {"qty": 42}, "MSFT": {"qty": 10}}
```

### Three kinds of discrepancy

```
discrepancies = []

# In journal but not at broker (sold outside the bot?)
for sym in journal_syms - broker_syms:
    "In journal but NOT at broker: SYM"

# At broker but not in journal (bought outside the bot?)
for sym in broker_syms - journal_syms:
    "At broker but NOT in journal: SYM"

# Both have it but quantity differs
for sym in journal_syms & broker_syms:
    if abs(j_qty - b_qty) > 0.001:
        "SYM: journal qty=X, broker qty=Y"
```

The 0.001 tolerance is for float rounding — Alpaca returns integer share
counts for stocks, but the API types are strings that we float-convert,
so exact equality can fail on weird inputs.

### Logging

Every discrepancy is logged at WARNING level prefixed with
`RECONCILIATION: `. If the lists match, a single INFO is logged:
`Reconciliation: journal and broker match`. This gives you a clean line
in the logs to confirm that startup recovery succeeded.

### Policy

`reconcile_with_broker` is **policy-free**: it detects, it doesn't
decide. The caller (`main.py::_recover_state`) decides what to do —
current policy is "alert via Telegram with the first 5 discrepancies,
continue running". See [`execution-flow.md`](execution-flow.md)
§"Startup" for rationale.

## What is not persisted

Intentionally kept in-memory only:

- **Risk audit log** (`RiskManager._audit_log`). See
  [`risk-management.md`](risk-management.md) §"Audit log". If you need
  persistence, snapshot it via `save_state`, do not add I/O to
  `risk/manager.py`.
- **Strategy indicator caches** (`_last_atr`). Reconstructed on next
  scan.
- **Stop order ids** (`_stop_order_ids`). Reconstructed via
  `get_orders(status='open')` on next cycle.
- **Current stop prices** (`_current_stops`). Reconstructed the first
  time a trailing stop is updated (but not before — the first cycle
  after a restart will not ratchet stops until a stop order has been
  re-associated).

The design accepts "trailing stop ratchet pauses briefly after a
restart" in exchange for a simpler recovery path. In normal operation
the gap is a single 5-minute cycle.

## Testing

`tests/test_strategies.py` does not yet cover `StatePersistence`
directly because the test harness does not mock filesystem I/O. The
module was tested manually during Phase 2c development with a temporary
data directory:

```python
sp = StatePersistence("/tmp/test_sp")
sp.append_trade({"symbol": "TEST", "side": "buy", "qty": 10, "price": 100})
print(len(sp.load_trades()))            # → 1
sp.save_state({"foo": "bar"})
print(sp.load_state())                  # → {"foo": "bar", "saved_at": "..."}
```

Permissions verification:

```sh
ls -la /tmp/test_sp/
# drwx------  ... .
# -rw-------  ... trades_journal.jsonl
# -rw-------  ... bot_state.json
```

Corruption resilience was verified by truncating the journal tail
mid-line and by manually corrupting the state JSON — both produced a
WARNING log and an empty return, not a crash.

A proper test suite for `state_persistence.py` is backlog — see
[`testing.md`](testing.md) §"Coverage gaps".
