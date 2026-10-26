# Deploy the DK Capital Discord MT5 dashboard only

The dashboard's **Average weekly** field (PR #88) calculates mean realised
XAUUSD pips per Isle of Man calendar week using retained, broker-confirmed
MT5 deals. It does not display the user's pip target or affect trading.

## Prerequisites (one-time GitHub setup)

This is a controlled GitHub Actions deployment to `/opt/dkcapital` on the
existing VPS. A deploy account must already have SSH access, write access to
the checkout, and permission to operate the existing Docker Compose stack.
Do not use a newly invented IP, username, token or key. **Never paste private
keys, passwords, the MT5 feed token or Discord bot token into a chat or commit.**

GitHub currently reports the deploy secrets as empty. Configure these
using **DKCAPITAL > Settings > Environments > mt5-recovery-deployment >
Environment secrets** (or the repository's Actions secrets if an
environment-level secret is not used):

| Secret name | Value |
| --- | --- |
| `DKCAPITAL_VPS_HOST` | Actual SSH hostname/IP of the DK Capital VPS |
| `DKCAPITAL_VPS_USER` | Existing authorized deploy account |
| `DKCAPITAL_VPS_SSH_KEY` | Full private SSH key corresponding to the public key on the VPS |
| `DKCAPITAL_VPS_HOST_KEY` | Verified `known_hosts` line for this host |

Use a dedicated ED25519 key for CI deployment. Generate it on your trusted
machine, add **only the public half** to the deploy account's
`~/.ssh/authorized_keys`, and paste the private half into GitHub's
encrypted `DKCAPITAL_VPS_SSH_KEY` secret. Do not check in either key.
A CI SSH key cannot require an interactive passphrase. Restrict who can
read or change GitHub deployment secrets.

Verify the server's ED25519 SSH host-key fingerprint using the VPS
provider's **trusted console** or an existing trusted SSH session before
creating the known_hosts entry. Never disable `StrictHostKeyChecking` to
make deployment pass, and do not use an unverified `ssh-keyscan` result.
If an external CI runner cannot reach the SSH port, address the
network allowlist or runner connectivity securely instead of exposing
MT5/VNC ports publicly.

The server's `/opt/dkcapital/.env` must already configure
`MT5_DASHBOARD_CHANNEL_ID`. Keep that file and the existing
`dkcapital-data` Docker volume intact. No Discord or MT5 secrets
are needed in the new dashboard code.

## Run the isolated deployment

Open **Actions > Deploy DK Capital Discord dashboard > Run workflow**,
select `main`, and run it. The workflow:

1. Checks out reviewed `main` and runs all Python tests.
2. Verifies non-empty SSH secrets and the pinned SSH host key.
3. Refuses to proceed if the VPS Git working tree is dirty or not on `main`.
4. Fast-forwards the existing checkout.
5. Builds and restarts **only** the `signal-processor` and `discord-bot`
   Docker Compose services. Both must be updated for edited Telegram signals
   to appear on Discord.
6. Checks that the processor exports edited-signal publication timing,
   the Discord bot imports the new publisher and historical pip module,
   and both reporting services are running with their shared volume.

It deliberately does **not** restart the Telegram listener, MT5 gateway,
MT5 service/EA, VNC or any other service. The processor rebuilds its
signal snapshot from the existing event log using the original Telegram
timestamps, preserving MT5 entry-age and execution checks.

When a Telegram post starts as `XAU USD SELL NOW` and is later edited
to contain a valid entry zone, the parser retains `opened_at` from the
original post for MT5 and separately records `publish_ready_at` when
the first parsable signal was observed. Discord's replay cutoff uses
`publish_ready_at` for *new* cards and prioritizes them before routine
edits to old cards. Historical posts that already contained a complete
signal before the cutoff are not replayed. Deliberately deleted Discord
cards retain a suppression marker and are not reposted by later edits.

Check the final GitHub Actions job result, then check the existing
Discord MT5 Demo Account 1 dashboard. Its performance section should
show `Average weekly: +X.X pips` or `unavailable` if no verified
broker entries are present. Check that the account remains GBP DEMO,
MT5 telemetry is fresh, and the counter has an appropriate start date.

The history archive is at
`/app/data/mt5-account1-pip-history.json` inside the persistent
Docker volume. Preserve it on updates and backups. The EA exports only
90 days and at most 1,000 broker deals per snapshot; if any older
history was unavailable when archiving began, do not imply the metric
covers those unavailable trades.

If credentials are not configured, the job intentionally stops before
SSH. If the bot restarts but no Discord update appears, inspect the
Discord bot's logs, dashboard channel ID, broker telemetry timestamp,
and dashboard message permissions. Do not modify EA controls to repair
a read-only reporting issue.
