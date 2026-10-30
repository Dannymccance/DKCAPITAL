"""Read-only export of raw Telegram event log and current parsed DK Capital signals."""
from __future__ import annotations
import argparse
import csv
import html
import json
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    items = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Malformed source event line {number}; archive not exported") from exc
                if isinstance(event, dict):
                    items.append(event)
    return items


def read_records(event_log: Path, state_file: Path) -> tuple[list[dict], list[dict]]:
    events = _read_jsonl(event_log)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    signals = state.get("signals") or {}
    if isinstance(signals, dict):
        signals = list(signals.values())
    if not isinstance(signals, list):
        raise ValueError("Unexpected parsed signal-state format")
    parsed = [x for x in signals if isinstance(x, dict)]
    return events, parsed


def _scalar(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _write_table(out: Path, name: str, rows: list[dict], keys: list[str], label: str) -> tuple[Path, Path]:
    csv_file = out / f"{name}.csv"
    html_file = out / f"{name}.html"
    with csv_file.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for item in rows:
            writer.writerow({key: _scalar(item.get(key)) for key in keys})
    columns = "".join(f"<th>{html.escape(k)}</th>" for k in keys)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(_scalar(row.get(key)))}</td>" for key in keys) + "</tr>"
        for row in rows
    )
    html_file.write_text(
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>" + html.escape(label) +
        "</title><style>body{font:14px system-ui;margin:2rem;color:#171717}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;"
        "text-align:left;vertical-align:top}td{overflow-wrap:anywhere}th{background:#eee;position:sticky;top:0}"
        "</style><h1>" + html.escape(label) + "</h1><p>Records from local event and parsed-signal stores. "
        "Not a guarantee of completeness.</p><p>" + str(len(rows)) + " records</p><table><tr>" +
        columns + "</tr>" + body + "</table></html>", encoding="utf-8"
    )
    csv_file.chmod(0o600)
    html_file.chmod(0o600)
    return csv_file, html_file


def export(event_log: Path, state_file: Path, out: Path) -> list[Path]:
    events, parsed = read_records(event_log, state_file)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Include all raw incoming events, including edits/deletions and unmatched messages.
    event_fields = ["event_type", "observed_at", "date", "edit_date", "chat_id",
                    "chat_title", "message_id", "reply_to_message_id", "is_forwarded",
                    "forwarded_from_chat_id", "text", "has_media", "raw_json"]
    event_rows = [
        {**event, "raw_json": json.dumps(event, ensure_ascii=False, sort_keys=True)}
        for event in events
    ]
    # Parsed view includes exact current signal state and full history JSON.
    parsed_fields = ["signal_id", "source_style", "source_chat_title", "chat_id",
                     "origin_chat_id", "root_message_id", "opened_at", "publish_ready_at",
                     "last_update_at", "symbol", "direction", "entry_low", "entry_high",
                     "original_sl", "current_sl", "tps", "status", "history", "raw_json"]
    parsed_rows = [
        {**signal, "raw_json": json.dumps(signal, ensure_ascii=False, sort_keys=True)}
        for signal in parsed
    ]
    result = []
    result.extend(_write_table(out, "gws-telegram-events", event_rows, event_fields, "DK Capital Telegram Event Archive"))
    result.extend(_write_table(out, "gws-parsed-signals", parsed_rows, parsed_fields, "DK Capital Parsed Signal Archive"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Export DK Capital events and signals as CSV and HTML")
    parser.add_argument("--event-log", type=Path, default=Path("/app/data/telegram-events.jsonl"))
    parser.add_argument("--signal-state", type=Path, default=Path("/app/data/signal-state.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    options = parser.parse_args()
    for output in export(options.event_log, options.signal_state, options.output_dir):
        print(output)


if __name__ == "__main__":
    main()
