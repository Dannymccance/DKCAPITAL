# PR65 MetaEditor compiler on VPS - isolated Wine/VNC

The compiler runs in a separate Ubuntu 24.04 Docker container using WineHQ **staging** (the newest version published for Ubuntu Noble at build time). Its Xvfb desktop and x11vnc service are separate from production MT5 Account 1 (:150) and Account 2 (:151). WineHQ staging is a compatibility experiment, not a confirmed fix for every MetaTrader build.

## Scope and safety

- NEVER restart/stop `dkcapital-mt5-1` or `dkcapital-mt5-2` to compile.
- The live `.wine` directories, broker profiles, cached tokens, existing EX5 and account settings are **never mounted into the container**.
- Only `MetaEditor64.exe`, standard MQL5 Include, and the verified PR65 `.mq5` are COPIED to `/opt/dkcapital-mt5-compiler/workspace/`.
- Port 5903 is published to **127.0.0.1 only**. Access via SSH tunnel.
- Docker image build requires access to official WineHQ package repositories. Verify the image build succeeds before assuming the bug is resolved.
- No replacement or chart attachment is performed by this script. `collect` copies the EX5 to a compiler artifacts directory only.

## Operate

Once this PR is merged and pulled onto the VPS, from `/opt/dkcapital`:

```bash
bash scripts/mt5_isolated_compiler_vnc.sh start
bash scripts/mt5_isolated_compiler_vnc.sh status
```

Run on Windows PowerShell in a new window:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 5903:127.0.0.1:5903 root@31.187.72.71
```

Open TigerVNC to `127.0.0.1::5903`, leaving the existing 5901 tunnel undisturbed.

MetaEditor should start with the staged file available at:

`Z:\\workspace\\MetaTrader 5\\MQL5\\Experts\\DKcapitalV205_SL_PR65_20261022T191833Z.mq5`

Open it using Ctrl+O, then F7. Require **zero compiler errors** and check for the output EX5 alongside the MQ5. If the anti-debugging message returns, preserve `metaeditor.log`, Wine version, screenshot, and container status; do not touch production to work around it.

After a verified successful compilation:

```bash
bash scripts/mt5_isolated_compiler_vnc.sh collect
```

Artifact location: `/opt/dkcapital-mt5-compiler/artifacts/DKcapitalV205_SL_PR65_20261022T191833Z.ex5`

The artifact is **NOT deployed**. Validate final EX5 signature, source parity, account exposures, EA configuration and broker-side TP1/SL behavior before planning a changeover. Existing GBP demo account 1 EA remains unchanged until that step.

To stop only the compiler:

```bash
bash scripts/mt5_isolated_compiler_vnc.sh stop
```

The existing VNC at 5901, active account terminals and open positions are not affected by compiler stop.

## Troubleshooting

```bash
bash scripts/mt5_isolated_compiler_vnc.sh status
docker logs dkcapital-mt5-compiler-vnc --tail 100
tail -80 /opt/dkcapital-mt5-compiler/workspace/metaeditor.log
tail -80 /opt/dkcapital-mt5-compiler/workspace/wineboot.log
```

Recent MT5 builds (October 2026) may still fail under new Wine versions. If they do, retain the live 9.0 Wine installation and obtain a compatible MetaEditor build or continue diagnosis within this isolated container rather than upgrading system packages in place.
