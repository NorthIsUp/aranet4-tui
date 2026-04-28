# aranet-log

Log [Aranet4](https://aranet.com/products/aranet4/) CO₂ / temperature / humidity / pressure
readings to a local SQLite file, and browse them in a Textual TUI.

## Install

```sh
# one-shot via uvx (no install)
uvx --from aranet-log aranet-log --scan
uvx --from aranet-log aranet4-tui

# persistent install
uv tool install aranet-log
# or
pipx install aranet-log
```

## Use

```sh
aranet-log --scan                       # find your device's address
aranet-log <ADDRESS> --once             # sync history once
aranet-log <ADDRESS>                    # sync forever (every 5 min)
aranet4-tui                             # open the TUI
```

The DB lives at `~/Library/Application Support/aranet-log/readings.db` on macOS, or
`$XDG_DATA_HOME/aranet-log/readings.db` (default `~/.local/share/...`) on Linux.
Override with `--db PATH` or `$ARANET_LOG_DB`.

## macOS launchd

Install a launchd agent that runs `aranet-log --once` every 5 minutes:

```sh
aranet-log <ADDRESS> --install-launchctl
# logs: ~/Library/Logs/aranet-log.log
aranet-log --uninstall-launchctl
```
