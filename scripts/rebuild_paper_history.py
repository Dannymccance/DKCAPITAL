from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "The legacy XAUUSDT proxy replay has been removed. "
        "DK Capital paper backfills must use genuine XAU/USD market data. "
        "Use the free Dukascopy XAUUSD backfill tool and run: "
        "docker compose --profile tools run --rm paper-backfill-last"
    )


if __name__ == "__main__":
    main()
