"""Independent DKcapital health watchdog.

Separate from the Discord bot so a crashed bot can still generate alerts.
Read-only monitoring: this process NEVER submits or changes broker orders.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from dkcapital.logging_setup import configure_logging
from dkcapital.mt5_profile_registry import (
    PROFILE_REGISTRY_NAME, profiles_from_registry, telemetry_file,
)

logger = logging.getLogger("dkcapital.watchdog")


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _age_seconds(value: str | None, now: datetime) -> float:
    if not value:
        return float("inf")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return float("inf")
        return max(0, (now - dt.astimezone(UTC)).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def check_health(data_dir: Path, now: datetime, gateway_ok: bool) -> set[str]:
    problems: set[str] = set()
    if not gateway_ok:
        problems.add("GATEWAY_HEALTH_UNAVAILABLE")
    profiles, registry_valid = profiles_from_registry(data_dir)
    if not registry_valid:
        problems.add("MT5_PROFILE_REGISTRY_INVALID")
    for profile in profiles:
        ident = profile["id"]
        prefix = f"MT5_ACCOUNT{ident}"
        telemetry = _read(telemetry_file(data_dir, ident))
        if _age_seconds(telemetry.get("received_at"), now) > 150:
            problems.add(f"{prefix}_TELEMETRY_STALE")
            continue
        if telemetry.get("feed_http") != 200:
            problems.add(f"{prefix}_FEED_NOT_HTTP200")
        if profile["expect_entries"] and telemetry.get("entry_enabled") is not True:
            problems.add(f"{prefix}_NOT_ARMED")
        elif not profile["expect_entries"] and telemetry.get("entry_enabled") is True:
            problems.add(f"{prefix}_UNEXPECTEDLY_ARMED")
        if profile["expected_login"] is not None and telemetry.get("login") != profile["expected_login"]:
            problems.add(f"{prefix}_WRONG_LOGIN")
        if profile["expected_login"] is not None:
            if telemetry.get("mode") != profile["expected_mode"] or telemetry.get("currency") != profile["expected_currency"]:
                problems.add(f"{prefix}_WRONG_ACCOUNT_TYPE")
            if any(type(telemetry.get(k)) is not int or telemetry[k] < 0
                   for k in ("broker_positions_total", "broker_orders_total")):
                problems.add(f"{prefix}_EXPOSURE_UNVERIFIED")
            if telemetry.get("broker_positions_protected") is not True:
                problems.add(f"{prefix}_BROKER_STOPS_UNVERIFIED")
    # Host-systemd evidence is optional for legacy Account 1 deployments, but
    # required once an explicit multi-account registry has been commissioned.
    host = _read(data_dir / "mt5-profile-host-status.json")
    require_host = (data_dir / PROFILE_REGISTRY_NAME).exists()
    if require_host or host:
        if _age_seconds(host.get("checked_at"), now) > 180:
            problems.add("MT5_PROFILE_HOST_AUDIT_STALE")
        else:
            account_states = host.get("accounts")
            account_states = account_states if isinstance(account_states, dict) else {}
            for profile in profiles:
                ident = profile["id"]
                name = f"MT5_ACCOUNT{ident}"
                info = account_states.get(str(ident))
                if not isinstance(info, dict):
                    problems.add(f"{name}_SERVICE_STATUS_MISSING")
                    continue
                if info.get("active") is not True:
                    problems.add(f"{name}_TERMINAL_DOWN")
                restart_at = info.get("last_restart_at")
                if restart_at and _age_seconds(restart_at, now) < 300:
                    problems.add(f"{name}_TERMINAL_RECENTLY_RESTARTED")
    recoverable = [p for p in profiles if p.get("auto_recover")]
    if recoverable:
        recovery_status = _read(data_dir / "mt5-recovery-status.json")
        if _age_seconds(recovery_status.get("updated_at"), now) > 180:
            problems.add("MT5_RECOVERY_SUPERVISOR_STALE")
        else:
            rows = recovery_status.get("accounts") or {}
            for profile in recoverable:
                ident = profile["id"]
                entry = rows.get(str(ident)) if isinstance(rows, dict) else None
                if not isinstance(entry, dict):
                    problems.add(f"MT5_ACCOUNT{ident}_RECOVERY_STATUS_MISSING")
                elif entry.get("result") in {"RESTART_FAILED", "WAITING_FOR_RECONNECT"}:
                    problems.add(f"MT5_ACCOUNT{ident}_{entry['result']}")
    recovery = _read(data_dir / "telegram-recovery-status.json")
    if _age_seconds(recovery.get("checked_at"), now) > 200:
        problems.add("TELEGRAM_CATCHUP_HEARTBEAT_STALE")
    elif not recovery.get("healthy"):
        problems.add("TELEGRAM_CATCHUP_INCOMPLETE")
    discord = _read(data_dir / "discord-heartbeat.json")
    if _age_seconds(discord.get("checked_at"), now) > 200:
        problems.add("DISCORD_BOT_HEARTBEAT_STALE")
    processor = _read(data_dir / "signal-processor-heartbeat.json")
    if _age_seconds(processor.get("checked_at"), now) > 200:
        problems.add("SIGNAL_PROCESSOR_HEARTBEAT_STALE")
    else:
        # A heartbeat alone does not prove that new Telegram events have
        # been consumed by the processor. Alert if the event log has an
        # unprocessed suffix which remains unsettled for more than 60s.
        events_path = data_dir / "telegram-events.jsonl"
        try:
            offset = int(processor.get("event_log_offset", -1))
            if (
                offset >= 0 and events_path.exists()
                and events_path.stat().st_size > offset
                and now.timestamp() - events_path.stat().st_mtime > 60
            ):
                problems.add("SIGNAL_PROCESSOR_EVENT_BACKLOG")
        except (ValueError, TypeError, OSError):
            pass
    return problems


class Watchdog:
    def __init__(self):
        self.data = Path(os.getenv("WATCHDOG_DATA_PATH", "/app/data"))
        self.channel_id = os.getenv("MT5_ALERT_CHANNEL_ID", "").strip()
        self.bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        self.telegram_alert_channel_id = os.getenv("DISCORD_TELEGRAM_ALERT_CHANNEL_ID", "1559168803294937178").strip()
        self.telegram_alert_user_ids = tuple(x.strip() for x in os.getenv("TELEGRAM_ALERT_USER_IDS", "508740193369587722,629451964782084096").split(",") if x.strip().isdigit())
        self.gateway = os.getenv("MT5_WATCHDOG_GATEWAY_URL", "http://mt5-gateway:8765/health")
        self.state_path = self.data / "watchdog-alert-state.json"
        self.http = build_opener(ProxyHandler({}))
        if not self.channel_id.isdigit() or len(self.bot_token) < 20:
            raise RuntimeError("MT5_ALERT_CHANNEL_ID and DISCORD_BOT_TOKEN are required")

    def _request(self, url: str, *, body: bytes | None = None, discord: bool = False):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if discord:
            headers["Authorization"] = "Bot " + self.bot_token
        request = Request(url, data=body, headers=headers,
                          method="POST" if body is not None else "GET")
        with self.http.open(request, timeout=8) as response:
            return response.status, response.read(4096)

    def _gateway_ok(self):
        try:
            status, data = self._request(self.gateway)
            return status == 200 and json.loads(data).get("status") == "ok"
        except (HTTPError, URLError, ValueError, TimeoutError, OSError):
            return False

    def _notify(self, content: str, *, telegram: bool = False, ping: bool = False) -> bool:
        destination = self.telegram_alert_channel_id if telegram else self.channel_id
        url = f"https://discord.com/api/v10/channels/{destination}/messages"
        if ping and telegram:
            content = " ".join(f"<@{uid}>" for uid in self.telegram_alert_user_ids) + "\n" + content
        allowed = {"parse": [], "users": list(self.telegram_alert_user_ids) if ping and telegram else []}
        try:
            status, _ = self._request(
                url,
                body=json.dumps({"content": content, "allowed_mentions": allowed}).encode(),
                discord=True,
            )
            return status in (200, 201)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.warning("Unable to deliver watchdog Discord notification: %s", type(exc).__name__)
            return False

    def _write_state(self, active: set[str], delivered_events: list[str] | None = None) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "active": sorted(active), "checked_at": datetime.now(UTC).isoformat(),
            "delivered_recovery_events": (delivered_events or [])[-300:],
        }))
        temp.replace(self.state_path)

    def cycle(self, *, initial_grace: bool = False) -> set[str]:
        now = datetime.now(UTC)
        problems = check_health(self.data, now, self._gateway_ok())
        prior = set(_read(self.state_path).get("active") or [])
        if initial_grace and not prior:
            # Grant dependent services time to create their first heartbeat
            # without suppressing later persistent errors.
            return problems
        pending = sorted(problems - prior)
        recovered = sorted(prior - problems)
        # Persist only alerts that were actually delivered.
        delivered = set(prior)
        for issue in pending:
            telegram = issue.startswith("TELEGRAM_")
            if self._notify(f"🚨 DKcapital independent watchdog: `{issue}` - inspect " + ("Telegram listener and recovery service." if telegram else "the named MT5 account / shared services."), telegram=telegram, ping=telegram):
                delivered.add(issue)
        for issue in recovered:
            if self._notify(f"✅ DKcapital independent watchdog recovery: `{issue}` cleared.", telegram=issue.startswith("TELEGRAM_")):
                delivered.discard(issue)
        # The host timer retains its last 100 transitions. Deliver each event
        # exactly once across bot/watchdog restarts, even if two transitions
        # occur between 60-second watchdog polls.
        prior_events = _read(self.state_path).get("delivered_recovery_events") or []
        seen = set(prior_events)
        event_ids = list(prior_events)
        recovery_state = _read(self.data / "mt5-recovery-status.json")
        for event in recovery_state.get("events") or []:
            if not isinstance(event, dict):
                continue
            ident, kind, eid = event.get("account"), event.get("result"), event.get("id")
            if type(ident) is not int or kind not in {
                "RESTART_REQUESTED", "RESTART_FAILED", "RECOVERED",
                "WAITING_FOR_RECONNECT"
            } or not isinstance(eid, str) or not eid or eid in seen:
                continue
            if not self._notify(
                f"DKcapital MT5 Account {ident} recovery: {kind} at "
                f"{str(event.get('at') or 'unknown time')[:32]}. "
                "Verify broker orders, EA feed and native protective stops."
            ):
                # Failed deliveries are retried, not marked as delivered.
                continue
            seen.add(eid)
            event_ids.append(eid)
        self._write_state(delivered, event_ids)
        return problems


def main():
    configure_logging(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO")
    watcher = Watchdog()
    started = time.monotonic()
    while True:
        try:
            result = watcher.cycle(initial_grace=time.monotonic()-started < 120)
            logger.info("Independent watchdog status problems=%s", sorted(result))
        except Exception:
            logger.exception("Independent watchdog cycle failed")
        time.sleep(60)


if __name__ == "__main__":
    main()
