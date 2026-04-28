import argparse
import asyncio
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aranet4
from aranet4.client import Aranet4, Param

from .launchctl import install_launchctl, uninstall_launchctl
from .paths import default_db_path, ensure_db_dir

DEFAULT_ADDRESS = "35FE6334-9602-15B6-F44F-5EAFF1825B65"
INTERVAL = 300  # 5 minutes


def init_db(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            co2 INTEGER NOT NULL,
            temperature REAL NOT NULL,
            humidity REAL NOT NULL,
            pressure REAL NOT NULL,
            battery INTEGER
        )
    """)
    # battery was NOT NULL in the original schema; backfilled rows have no battery,
    # so rebuild the table to drop the constraint on existing DBs.
    cols = db.execute("PRAGMA table_info(readings)").fetchall()
    if any(c[1] == "battery" and c[3] == 1 for c in cols):
        db.executescript("""
            CREATE TABLE readings_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                co2 INTEGER NOT NULL,
                temperature REAL NOT NULL,
                humidity REAL NOT NULL,
                pressure REAL NOT NULL,
                battery INTEGER
            );
            INSERT INTO readings_new SELECT * FROM readings;
            DROP TABLE readings;
            ALTER TABLE readings_new RENAME TO readings;
        """)
    # Collapse duplicate device rows whose reconstructed timestamps drifted by 1-2s
    # between syncs before _fetch_history snapped to the interval boundary.
    # Device rows have no fractional seconds; legacy live readings do.
    needs_cleanup = db.execute(
        "SELECT 1 FROM readings "
        "WHERE instr(timestamp, '.') = 0 AND substr(timestamp, 18, 2) != '00' LIMIT 1"
    ).fetchone()
    if needs_cleanup:
        db.execute("DROP INDEX IF EXISTS idx_readings_timestamp")
        db.execute("""
            DELETE FROM readings
            WHERE instr(timestamp, '.') = 0
              AND id NOT IN (
                  SELECT MIN(id) FROM readings
                  WHERE instr(timestamp, '.') = 0
                  GROUP BY substr(timestamp, 1, 16)
              )
        """)
        db.execute("""
            UPDATE readings
            SET timestamp = substr(timestamp, 1, 16) || ':00+00:00'
            WHERE instr(timestamp, '.') = 0
        """)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp)")
    db.commit()


async def _fetch_history(address: str):
    # aranet4.client.get_all_records() returns empty value when the device buffer is full,
    # so we drive the per-param fetch ourselves.
    monitor = Aranet4(address=address)
    await monitor.connect()
    interval = await monitor.get_interval()
    last_log = await monitor.get_seconds_since_update()
    log_size = await monitor.get_total_readings()
    if log_size <= 0:
        return []
    # Snap the anchor (time of most recent sample) to an epoch-aligned interval
    # boundary so repeated syncs reconstruct identical timestamps and
    # INSERT OR IGNORE actually deduplicates.
    now = datetime.now(timezone.utc)
    anchor_s = int((now - timedelta(seconds=last_log)).timestamp())
    anchor_s -= anchor_s % interval
    anchor = datetime.fromtimestamp(anchor_s, tz=timezone.utc)
    times = [anchor - timedelta(seconds=(log_size - 1 - i) * interval) for i in range(log_size)]
    co2 = await monitor.get_records(Param.CO2, log_size=log_size, start=1, end=log_size)
    temp = await monitor.get_records(Param.TEMPERATURE, log_size=log_size, start=1, end=log_size)
    hum = await monitor.get_records(Param.HUMIDITY, log_size=log_size, start=1, end=log_size)
    pres = await monitor.get_records(Param.PRESSURE, log_size=log_size, start=1, end=log_size)
    return list(zip(times, co2, temp, hum, pres))


def sync_readings(db: sqlite3.Connection, address: str):
    rows = asyncio.run(_fetch_history(address))
    inserted = 0
    for ts_dt, co2, temp, hum, pres in rows:
        ts = ts_dt.astimezone(timezone.utc).isoformat()
        cur = db.execute(
            "INSERT OR IGNORE INTO readings (timestamp, co2, temperature, humidity, pressure, battery) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (ts, co2, temp, hum, pres),
        )
        inserted += cur.rowcount
    db.commit()
    now = datetime.now(timezone.utc).isoformat()
    if rows:
        _, co2, temp, hum, pres = rows[-1]
        print(
            f"{now}  synced {len(rows)} device rows  new={inserted}  "
            f"latest CO2={co2}ppm T={temp}°C H={hum}% P={pres}hPa"
        )
    else:
        print(f"{now}  no records on device")


def scan():
    print("Scanning for Aranet4 devices (8s)...")
    devices = aranet4.client.find_nearby(lambda adv: print(f"  {adv.device.address}  {adv.device.name}"))
    if not devices:
        print("No devices found. Make sure Bluetooth is on and the Aranet4 is nearby.")


def main():
    parser = argparse.ArgumentParser(prog="aranet-log", description="Log Aranet4 readings to SQLite")
    parser.add_argument("address", nargs="?", default=DEFAULT_ADDRESS, help="Bluetooth MAC/UUID of the Aranet4")
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: platform data dir or $ARANET_LOG_DB)")
    parser.add_argument("--scan", action="store_true", help="Scan for nearby Aranet4 devices")
    parser.add_argument("--once", action="store_true", help="Take a single reading and exit")
    parser.add_argument("--interval", type=int, default=INTERVAL, help="Sync interval in seconds (default 300)")
    parser.add_argument(
        "--install-launchctl",
        action="store_true",
        help="Install a launchd agent on macOS to run --once on a schedule",
    )
    parser.add_argument(
        "--uninstall-launchctl",
        action="store_true",
        help="Remove the launchd agent installed by --install-launchctl",
    )
    args = parser.parse_args()

    if args.install_launchctl:
        install_launchctl(address=args.address, interval=args.interval, db=args.db)
        return
    if args.uninstall_launchctl:
        uninstall_launchctl()
        return

    if args.scan:
        scan()
        return

    db_path = ensure_db_dir(args.db.expanduser() if args.db else default_db_path())
    db = sqlite3.connect(db_path)
    init_db(db)

    if args.once:
        sync_readings(db, args.address)
        return

    print(f"Syncing every {args.interval}s to {db_path}")
    while True:
        try:
            sync_readings(db, args.address)
        except Exception as e:
            print(f"{datetime.now(timezone.utc).isoformat()}  ERROR: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
