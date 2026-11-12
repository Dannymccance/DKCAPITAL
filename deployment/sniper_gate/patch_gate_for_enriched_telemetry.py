#!/usr/bin/env python3
"""Stage the independent telemetry feed as the Sniper gate input.

Changes ONLY files within the downloaded external Python sidecar directory.
Never writes to MT5, the current EA, the bridge unit, or live routing.
"""
from pathlib import Path
import argparse
import shutil


def swap_one(src: str, old: str, new: str, filename: str) -> str:
    if old in src:
        if src.count(old) != 1:
            raise RuntimeError(f"Unexpected repeated source pattern in {filename}: {old}")
        return src.replace(old, new, 1)
    if new in src:
        return src
    raise RuntimeError(f"Unknown {filename} version: missing expected text: {old}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument('--apply', action='store_true', help='Without this, validate and preview only')
    a = ap.parse_args()
    directory = a.directory
    gate_file = directory / 'sniper_gate.py'
    install_file = directory / 'install_gate.py'
    gate = gate_file.read_text()
    install = install_file.read_text()

    old = "DEFAULT_FILES/'discord_limit_deals.csv'"
    new = "DEFAULT_FILES/'discord_limit_deals_enriched.csv'"
    gate = swap_one(gate, old, new, gate_file.name)
    old = "    rows, header=read_csv(path)\n    cols={namekey(x) for x in header}\n    if not any(x in cols for x in ('reason','dealreason','closereason')):"
    new = "    rows, header=read_csv(path)\n    if time.time()-Path(path).stat().st_mtime>30:\n        raise GuardError('stale enriched deal history (over 30 seconds old)')\n    cols={namekey(x) for x in header}\n    if not any(x in cols for x in ('reason','dealreason','closereason')):"
    gate = swap_one(gate, old, new, gate_file.name)

    old = "    args=[PYTHON,str(INSTALL/'sniper_gate.py'),'--mode',mode,'--input',str(inp),'--output',str(LIVE),'--db',str(db)]"
    new = "    args=[PYTHON,str(INSTALL/'sniper_gate.py'),'--mode',mode,'--input',str(inp),'--output',str(LIVE),'--db',str(db), '--deals',str(LIVE.parent/'discord_limit_deals_enriched.csv'),'--magic','26110430']"
    install = swap_one(install, old, new, install_file.name)
    old = "files/'discord_limit_deals.csv'"
    new = "files/'discord_limit_deals_enriched.csv'"
    if old in install:
        install = install.replace(old, new)
    elif new not in install:
        raise RuntimeError('Unexpected installer: deal filename not found')
    old = "lambda:sl_history(files/'discord_limit_deals_enriched.csv',time.time(),2)"
    new = "lambda:sl_history(files/'discord_limit_deals_enriched.csv',time.time(),2,26110430)"
    install = swap_one(install, old, new, install_file.name)
    old = "sl_history(LIVE.parent/'discord_limit_deals.csv',time.time(),2)"
    new = "sl_history(LIVE.parent/'discord_limit_deals_enriched.csv',time.time(),2,26110430)"
    install = swap_one(install, old, new, install_file.name)
    # NOTE: do not change broker offset without inspecting the published identity file.
    print('Sidecar code validated: uses independent enriched SL telemetry, magic 26110430, stale-data guard.')
    print('No EA code, running bridge, or MT5 terminal changes needed.')
    if not a.apply:
        print('Dry run only. Pass --apply to patch the DOWNLOADED SIDECAR scripts.')
        return
    for path, content in ((gate_file, gate), (install_file, install)):
        original = path.read_text()
        if original != content:
            backup = path.with_suffix(path.suffix + '.before-telemetry')
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text(content)
            print('Updated sidecar file:',path)
        else:
            print('Already patched:',path)


if __name__ == '__main__':
    main()
