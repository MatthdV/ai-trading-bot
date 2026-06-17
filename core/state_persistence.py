#!/usr/bin/env python3
"""
State Persistence — Trade journal (append-only JSONL) and bot state snapshot.

Provides crash recovery by persisting trade records and periodic state snapshots
to disk. On restart the bot can reconcile its internal state with the broker.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
JOURNAL_FILE = "trades_journal.jsonl"
STATE_FILE = "bot_state.json"


class StatePersistence:
    """Handles trade journal and bot state persistence."""

    def __init__(self, data_dir: str | None = None) -> None:
        self._data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # H2: restrict data dir to owner-only (VPS multi-tenant safety).
        try:
            os.chmod(self._data_dir, 0o700)
        except OSError as exc:
            logger.warning(f"Could not chmod data dir {self._data_dir}: {exc}")
        self._journal_path = self._data_dir / JOURNAL_FILE
        self._state_path = self._data_dir / STATE_FILE
        logger.info(f"StatePersistence initialized: {self._data_dir}")

    # ------------------------------------------------------------------
    # Trade journal (append-only JSONL)
    # ------------------------------------------------------------------

    def append_trade(self, record: dict[str, Any]) -> None:
        """Append a single trade record to the journal.

        Atomic on POSIX for writes < PIPE_BUF (4 KB) thanks to O_APPEND.
        fsync() is called so a crash can't silently lose the record.
        Raises OSError — the caller (cf. B6) has its own try/except and must
        NOT roll back the already-placed broker orders.
        """
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        line = json.dumps(record) + "\n"
        try:
            # 0o600: journal contains symbol/qty/price — owner-only read.
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            fd = os.open(self._journal_path, flags, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            logger.info(
                f"Trade journaled: {record.get('symbol')} {record.get('side')}"
            )
        except OSError as exc:
            logger.critical(f"Trade journal write failed: {exc}")
            raise

    def load_trades(self) -> list[dict[str, Any]]:
        """Load all trade records from the journal.

        Corrupted lines (tail-truncated after a crash, bad JSON) are skipped
        with a warning — partial history is always better than crashing the
        bot at startup.
        """
        if not self._journal_path.exists():
            return []
        trades: list[dict[str, Any]] = []
        try:
            with open(self._journal_path) as f:
                for lineno, raw in enumerate(f, 1):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            f"Skipping corrupted journal line {lineno}: {exc}"
                        )
        except OSError as exc:
            logger.error(f"Could not read journal {self._journal_path}: {exc}")
        return trades

    # ------------------------------------------------------------------
    # Bot state snapshot
    # ------------------------------------------------------------------

    def save_state(self, state: dict[str, Any]) -> None:
        """Save a full bot state snapshot (overwrites previous).

        Uses the write-to-tmp-then-rename pattern so a crash mid-write can
        never leave a half-written state file on disk. File mode is 0o600
        (owner-only read) for VPS multi-tenant safety.
        """
        state.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
        tmp = self._state_path.with_suffix(".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self._state_path)

    def load_state(self) -> dict[str, Any]:
        """Load the last saved bot state. Returns empty dict if none.

        On corruption/read error: moves the bad file to ``*.corrupted`` for
        forensics and returns an empty dict so the bot can still start. A
        bot that boots with no state is strictly better than a crashloop.
        """
        if not self._state_path.exists():
            return {}
        try:
            with open(self._state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(
                f"State file {self._state_path} corrupted or unreadable: {exc}"
            )
            backup = self._state_path.with_suffix(".corrupted")
            try:
                self._state_path.rename(backup)
                logger.warning(f"Corrupted state moved to {backup}")
            except OSError as rename_exc:
                logger.error(f"Could not move corrupted state: {rename_exc}")
            return {}

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile_with_broker(
        self,
        journal_positions: dict[str, Any],
        broker_positions: dict[str, Any],
    ) -> list[str]:
        """Compare journal positions with broker positions.

        Returns a list of discrepancy messages (empty = all good).
        """
        discrepancies: list[str] = []

        journal_syms = set(journal_positions.keys())
        broker_syms = set(broker_positions.keys())

        for sym in journal_syms - broker_syms:
            discrepancies.append(f"In journal but NOT at broker: {sym}")
        for sym in broker_syms - journal_syms:
            discrepancies.append(f"At broker but NOT in journal: {sym}")
        for sym in journal_syms & broker_syms:
            j_qty = float(journal_positions[sym].get("qty", 0))
            b_qty = float(broker_positions[sym].get("qty", 0))
            if abs(j_qty - b_qty) > 0.001:
                discrepancies.append(
                    f"{sym}: journal qty={j_qty}, broker qty={b_qty}"
                )

        for msg in discrepancies:
            logger.warning(f"RECONCILIATION: {msg}")

        if not discrepancies:
            logger.info("Reconciliation: journal and broker match")

        return discrepancies
