# Deployment

Reference for the Freqtrade VPS deploy pipeline
(`docker-compose.freqtrade.yml`, `scripts/deploy_vps.sh`) and the
secrets handling model. The US equity bot (`main.py`) does **not** run
on the VPS — it runs locally on Matthieu's machine, because Alpaca paper
trading does not benefit from 24/7 uptime. This document is about the
crypto side.

## What gets deployed

Two Docker containers running on a single Hostinger VPS:

| Container | Strategy | Market | Config |
|-----------|----------|--------|--------|
| `freqtrade-funding`  | `FundingRateArbitrage`  | Binance futures | `config_futures.json`  |
| `freqtrade-breakout` | `BreakoutTrendFollowing` | Binance spot   | `config_breakout.json` |

Both are built from the official `freqtradeorg/freqtrade:stable` image.
No custom image — the strategy `.py` files are mounted read-only into
the container, not baked in. This keeps deploys fast and reversible.

## Hardening (`docker-compose.freqtrade.yml`)

The compose file factors a shared hardening anchor, applied to both
services:

```yaml
x-freqtrade-hardening: &freqtrade-hardening
  image: freqtradeorg/freqtrade:stable
  restart: unless-stopped
  user: "1000:1000"
  read_only: true
  cap_drop:
    - ALL
  security_opt:
    - no-new-privileges:true
  mem_limit: 2g
  pids_limit: 256
  tmpfs:
    - /tmp
```

Line by line:

- **`image: freqtradeorg/freqtrade:stable`** — pinned tag (not
  `:latest`). Upstream rolls `stable` forward on their own cadence; we
  get updates by pulling explicitly via `docker compose pull`.
- **`restart: unless-stopped`** — restart the container automatically
  on crash or host reboot, but respect a deliberate `docker stop`
  (unlike `always`, which fights manual intervention). This was a
  Phase 2c hardening decision.
- **`user: "1000:1000"`** — the Freqtrade image expects UID 1000 by
  default. Running as a non-root unprivileged user contains container
  escape exploits.
- **`read_only: true`** — the container root filesystem is immutable.
  Any attempt to write outside the mounted volumes fails. Combined
  with the dropped capabilities below, this defends against a large
  class of post-exploitation techniques.
- **`cap_drop: ALL`** — drop every Linux capability. Freqtrade only
  needs to open outbound TCP connections and read/write its data
  volume; it does not need `NET_RAW`, `SYS_ADMIN`, etc.
- **`security_opt: no-new-privileges:true`** — prevents setuid binaries
  inside the container from gaining privileges, even if they exist.
- **`mem_limit: 2g`** — hard RAM cap. Pandas + numpy + ccxt can spike,
  so 2 GB is the chosen headroom (plan draft said 1 GB, increased
  after observing peak usage during backtests).
- **`pids_limit: 256`** — caps the process/thread count. Containment
  against fork bombs and contained processes running wild.
- **`tmpfs: /tmp`** — an in-memory mount over `/tmp` inside the
  container. Needed because `read_only: true` blocks pandas/pip from
  writing tempfiles during imports.

The hardening set is deliberately conservative for a bot that handles
real money. Turning any of these off should require the same review
process as changing `risk/manager.py`.

## Volume layout

```yaml
services:
  freqtrade-funding:
    <<: *freqtrade-hardening
    volumes:
      # Parent: RW (logs, data, sqlite DB, pairlist cache)
      - ./freqtrade_config:/freqtrade/freqtrade_config:rw
      # Strategies: RO (defense in depth)
      - ./freqtrade_config/strategies:/freqtrade/freqtrade_config/strategies:ro
      # Config JSON: RO (nobody mutates at runtime)
      - ./freqtrade_config/config_futures.json:/freqtrade/freqtrade_config/config_futures.json:ro
```

The "nested read-only overlay" pattern is intentional: the parent
directory must be writable so Freqtrade can save `tradesv3.sqlite`,
log files, and pairlist caches. But the `strategies/` subdirectory
does not need to be writable — it contains code. Nested-mounting
`strategies/` as `:ro` on top of the parent `rw` mount means that the
code path is immutable even if Freqtrade (or an exploit inside it)
tries to write a new `.py` file there.

Individual config JSONs are mounted directly (not via a subdirectory)
because each service needs only its own config, and a per-file mount
makes the dependency explicit.

## Environment variables

Both services read three secrets from the shell environment via
`${...}` substitution in the compose file:

```yaml
environment:
  - FREQTRADE__exchange__key=${BINANCE_API_KEY}
  - FREQTRADE__exchange__secret=${BINANCE_API_SECRET}
  - FREQTRADE__telegram__token=${TELEGRAM_BOT_TOKEN}
```

Freqtrade supports the `FREQTRADE__<section>__<key>` env override
syntax, so any JSON config key can be provided this way. This is how
secrets are kept out of the config JSONs — `config_futures.json` has
`"key": ""` and the runtime override populates it from the env.

The compose file is invoked with the VPS-local `.env` file on
`$PWD` (Docker Compose auto-loads `.env`). The `.env` on the VPS is
the **only** place the Binance API secret lives in plain text.

Note: the Telegram `chat_id` is **not** injected via env. It is kept
in the JSON config because it is not secret (it's a public channel ID
and cannot be abused without the token). This matches the comment in
`scripts/start_dryrun.sh`:

```bash
# chat_id est hardcodé dans la config JSON (pas un secret)
```

Actually, as of Phase 2c we went back on that and restored
`FREQTRADE__telegram__chat_id="$TELEGRAM_CHAT_ID"` so the same .env
file drives both bots. The JSON configs hold `"chat_id": ""` and the
env override populates at runtime. This is purely a consistency
improvement, not a security fix.

## `scripts/deploy_vps.sh`

Deploy driver. Usage: `bash scripts/deploy_vps.sh` from the repo root.

### SSH host-key pinning

The script builds a pinned SSH command:

```bash
SSH_KEY="$HOME/.ssh/id_ed25519_vps"
KNOWN_HOSTS_FILE="$HOME/.ssh/known_hosts_vps"
SSH_OPTS="-i $SSH_KEY -o UserKnownHostsFile=$KNOWN_HOSTS_FILE -o StrictHostKeyChecking=yes"
```

Three things:

1. **Dedicated key and known_hosts file** per VPS, so a compromise of
   one host does not implicate the user's other SSH connections.
2. **`StrictHostKeyChecking=yes`** — refuses to connect to any unknown
   host. Unlike `accept-new`, which silently trusts the first-contact
   fingerprint (MITM vector), `yes` requires the fingerprint to have
   been pre-installed out-of-band.
3. **Hard pre-check** on the known_hosts file existing:

   ```bash
   [[ -f "$KNOWN_HOSTS_FILE" ]] || error "known_hosts pinné introuvable..."
   ```

   The error message tells the operator how to initialise the file:

   ```bash
   ssh-keyscan -H 187.124.26.75 >> ~/.ssh/known_hosts_vps
   ```

This should be done **once**, after verifying the fingerprint via the
Hostinger control panel or another out-of-band channel. The bot
refuses to deploy until this has happened.

### Pre-flight checks

Before touching the VPS, the script verifies on the local side:

```bash
[[ -f "$COMPOSE_FILE" ]]                                         || error ...
[[ -f ".env" ]]                                                  || error ...
[[ -f "freqtrade_config/config_futures.json" ]]                  || error ...
[[ -f "freqtrade_config/config_breakout.json" ]]                 || error ...
[[ -f "freqtrade_config/strategies/FundingRateArbitrage.py" ]]   || error ...
[[ -f "freqtrade_config/strategies/BreakoutTrendFollowing.py" ]] || error ...
[[ -f "$KNOWN_HOSTS_FILE" ]]                                     || error ...
```

And a defensive check on `.env` mixing testnet and production keys:

```bash
if grep -q "BINANCE_TESTNET_KEY" .env && ! grep -q "BINANCE_API_KEY" .env; then
    warn ".env contient BINANCE_TESTNET_KEY mais pas BINANCE_API_KEY"
    warn "Le docker-compose attend BINANCE_API_KEY et BINANCE_API_SECRET"
    read -rp "Continuer quand même ? (y/N) " confirm
```

The docker-compose expects `BINANCE_API_KEY` and `BINANCE_API_SECRET`
for live Binance. A `.env` that still has testnet keys suggests the
deploy is targeting the wrong environment. The prompt is a reversible
foot-gun check — the operator can answer `y` if they know what they're
doing (e.g. deploying a dry-run to production VPS with the testnet
account for initial smoke testing).

### Remote directory bootstrap

```bash
ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "
    mkdir -p $VPS_DIR/freqtrade_config/strategies
    mkdir -p $VPS_DIR/freqtrade_config/logs
    mkdir -p $VPS_DIR/freqtrade_config/data
    mkdir -p $VPS_DIR/freqtrade_config/user_data/hyperopts
"
```

Idempotent — safe to run on a fresh VPS and on an already-deployed
one.

### Atomic `.env` transfer

This is the subtle part:

```bash
ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" \
    "umask 077 && mkdir -p $VPS_DIR && cat > $VPS_DIR/.env" < .env
```

The commentary in the script itself explains it well enough to quote:

> `scp` crée le fichier en umask du shell distant (typiquement 0644),
> puis on aurait dû chmod après → race. Utiliser un ssh pipe + umask
> 077 garantit que le fichier EST CRÉÉ directement en 0600.

In English: `scp` creates the destination file with the remote shell's
umask (typically 0022, producing 0644), then a subsequent `chmod 600`
closes the window. Between `scp` and `chmod`, the file is world-
readable — anyone else on the VPS could `cat` it. The fix is to pipe
the contents over `ssh` into a redirected `cat` with `umask 077` set
first. The file is **created** in 0600 on the remote host, never
world-readable at any point.

### File transfer and container lifecycle

```bash
scp $SSH_OPTS "$COMPOSE_FILE" "$VPS_USER@$VPS_HOST:$VPS_DIR/"
scp $SSH_OPTS freqtrade_config/config_futures.json  ...
scp $SSH_OPTS freqtrade_config/config_breakout.json ...
scp $SSH_OPTS freqtrade_config/strategies/FundingRateArbitrage.py   ...
scp $SSH_OPTS freqtrade_config/strategies/BreakoutTrendFollowing.py ...

ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "
    set -e
    cd $VPS_DIR
    docker compose -f $COMPOSE_FILE pull
    docker compose -f $COMPOSE_FILE down --remove-orphans 2>/dev/null || true
    docker compose -f $COMPOSE_FILE up -d
"
```

`docker compose pull` fetches the latest `stable` tag. `down
--remove-orphans` is tolerated to fail (fresh VPS has nothing to bring
down). `up -d` starts both services detached.

### Verification

After the containers start, the script sleeps 5 seconds and prints:

```bash
docker compose -f $COMPOSE_FILE ps
docker compose -f $COMPOSE_FILE logs --tail=30 freqtrade-funding
docker compose -f $COMPOSE_FILE logs --tail=30 freqtrade-breakout
```

This is a lightweight smoke test — you get 30 log lines per container
to eyeball for obvious errors (API key rejected, config parse failed,
strategy import failed). A proper health check would be better, but
Freqtrade's boot sequence is quick enough that 30 lines usually tell
the full story.

## What is not automated

Intentional gaps in the deploy pipeline, each a deliberate trade-off:

1. **No blue-green deploy.** `down` then `up` produces a brief gap
   where neither container is running. For dry-run this is fine; for
   live FundingRateArbitrage, a missed 8-hour funding window is ~0 %
   of annual return, so the gap is acceptable. A zero-downtime deploy
   would add enough complexity that the failure modes become harder
   to reason about.
2. **No automatic rollback.** If `up -d` succeeds but the containers
   crash-loop, you notice via the 30-line log tail and roll back
   manually. A rollback script would need to track the previous
   image tag, which is not trivial since we pin `:stable`.
3. **No secret rotation.** Updating `BINANCE_API_KEY` requires
   re-running the whole deploy. No "hot-reload secrets" flow.
4. **No multi-environment support.** `deploy_vps.sh` has `VPS_HOST`
   hardcoded. A staging VPS would need its own script (or a command-
   line arg). Staging does not exist today.
5. **No pre-commit hook on `deploy_vps.sh` itself.** The script has
   shellcheck-compatible syntax but is not validated by pre-commit.
   `bash -n scripts/deploy_vps.sh` passes; shellcheck is not
   installed locally.

## Secret surface area

Secrets on the VPS, after a successful deploy:

| File | Contents | Mode | Owner |
|------|----------|------|-------|
| `~/.ssh/authorized_keys` | Operator's public key | 0600 | `matthieu` |
| `/home/matthieu/ai-trading-bot/.env` | Binance + Telegram secrets | 0600 | `matthieu` |
| Docker containers' `/proc/<pid>/environ` | Env vars (incl. secrets) | root-only | — |

Secrets in transit during deploy:

| Path | Transport | Encryption |
|------|-----------|------------|
| Local `.env` → VPS `.env` | SSH pipe with umask 077 | SSH encryption |
| Config JSONs → VPS | scp over SSH | SSH encryption |
| Strategy `.py` files → VPS | scp over SSH | SSH encryption |

Secrets **at rest** on the developer laptop:

| Path | Notes |
|------|-------|
| `~/.ssh/id_ed25519_vps` | Passphrase-protected private key |
| `<repo>/.env` | Never committed; `.pre-commit-config.yaml` blocks it |
| `<repo>/data/trades_journal.jsonl` | 0600, contains trade history (owned by US bot, not deployed) |
| `<repo>/data/bot_state.json` | 0600, ditto |

### Leaked keys history

As of commit `47a18ec`, an old `.env` with real Alpaca paper keys was
committed. The keys have been deleted on the Alpaca dashboard. The
history rewrite was **skipped** because:

- The deleted key cannot be used to place trades or withdraw funds
- Pre-commit + gitleaks now block recurrence
- A force-push across the shared branch would be a larger disruption
  than the residual risk

If the Binance live keys ever leak, the response must be:

1. Revoke immediately from the Binance dashboard
2. Generate fresh keys
3. Update VPS `.env` via a fresh deploy
4. Audit the git history for any other leaked secrets
5. Decide whether to rewrite history (the answer for *live* keys is
   probably yes, unlike for paper keys)

## Post-deploy verification

The deploy script's log tail is necessary but not sufficient. After a
deploy, a human should:

1. **Check `/start` in Telegram** — the bot should respond with a menu.
   This verifies the Telegram integration end-to-end.
2. **Call `/status` and `/profit`** — verify the data is sane (no "no
   trades yet" errors unless it's the first start).
3. **Pull the Freqtrade dashboard** at `http://<vps>:8080` if enabled,
   or `docker compose logs -f` for a live log stream.
4. **Grep the logs** for `ERROR`, `CRITICAL`, or `freqtrade.exceptions`
   in the first 5 minutes of operation.
5. **Confirm a first cycle completed** — FundingRateArbitrage needs a
   Binance futures connection plus a funding rate download; a failure
   in either will surface within minutes.

None of the above is scripted yet. It is a manual smoke test because
the VPS does not yet have a separate health-check endpoint.
