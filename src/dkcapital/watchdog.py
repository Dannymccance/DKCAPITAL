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
    telemetry = _read(data_dir / "mt5-account1.json")
    if _age_seconds(telemetry.get("received_at"), now) > 150:
        problems.add("MT5_ACCOUNT1_TELEMETRY_STALE")
    else:
        if telemetry.get("feed_http") != 200:
            problems.add("MT5_ACCOUNT1_FEED_NOT_HTTP200")
        if telemetry.get("entry_enabled") is not True:
            problems.add("MT5_ACCOUNT1_NOT_ARMED")
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
    return problems


class Watchdog:
    def __init__(self):
        self.data = Path(os.getenv("WATCHDOG_DATA_PATH", "/app/data"))
        self.channel_id = os.getenv("MT5_ALERT_CHANNEL_ID", "").strip()
        self.bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
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

    def _notify(self, content: str) -> bool:
        url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
        try:
            status, _ = self._request(
                url,
                body=json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode(),
                discord=True,
            )
            return status in (200, 201)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.warning("Unable to deliver watchdog Discord notification: %s", type(exc).__name__)
            return False

    def _write_state(self, active: set[str]) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "active": sorted(active), "checked_at": datetime.now(UTC).isoformat()
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
            if self._notify(f"🚨 DKcapital independent watchdog: \`{issue}\` — inspect Account 1 services."):
                delivered.add(issue)
        for issue in recovered:
            if self._notify(f"✅ DKcapital independent watchdog recovery: \`{issue}\` cleared."):
                delivered.discard(issue)
        self._write_state(delivered)
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
