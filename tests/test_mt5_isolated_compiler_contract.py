"""Guardrails for isolated MetaEditor/VNC diagnostics (no live trading changes)."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mt5_isolated_compiler_vnc.sh"
ENTRYPOINT = ROOT / "ops/mt5/isolated-compiler/entrypoint.sh"
DOCKERFILE = ROOT / "ops/mt5/isolated-compiler/Dockerfile"


def test_shell_syntax():
    for script in (SCRIPT, ENTRYPOINT):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_compiler_never_mutates_live_accounts():
    script = SCRIPT.read_text()
    dockerfile = DOCKERFILE.read_text()
    assert "systemctl restart" not in script
    assert "systemctl stop" not in script
    assert "systemctl is-active dkcapital-mt5-1" in script
    assert "systemctl is-active dkcapital-mt5-2" in script
    assert "--mount \"type=bind,src=$WORK,dst=/workspace\"" in script
    assert '--mount "type=bind,src=$LIVE' not in script
    assert "DKcapitalV2.ex5" not in script
    assert "127.0.0.1:5903:5903" in script
    assert "USER compiler" in dockerfile


def test_compiled_artifact_never_autodeployed():
    script = SCRIPT.read_text()
    assert '/opt/dkcapital-mt5-compiler/artifacts' in script
    assert "LIVE EA IS UNCHANGED" in script
    assert "cmp -s" in script
    assert "slAlreadyAtOrBeyondBE" in script
