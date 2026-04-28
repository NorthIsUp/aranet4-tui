import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Resize
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, Static

from .paths import default_db_path

REFRESH_INTERVAL = 10
CHART_WINDOW_HOURS = 24
TABLE_LIMIT = 200
WIDE_LAYOUT_COLS = 140
BLOCKS = " ▁▂▃▄▅▆▇█"


def query_readings(db_path: Path, limit: int = 200) -> list[dict]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM readings ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def query_readings_since(db_path: Path, hours: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM readings WHERE timestamp >= ? ORDER BY timestamp", (cutoff,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def co2_color(val: int) -> str:
    if val < 600:
        return "green"
    if val < 1000:
        return "yellow"
    if val < 1400:
        return "orange"
    return "red"


def parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone()


def format_ts_full(iso: str) -> str:
    return parse_ts(iso).strftime("%Y-%m-%d %H:%M:%S")


class Chart(Static):
    data: reactive[list[float]] = reactive(list, always_update=True)
    timestamps: reactive[list[datetime]] = reactive(list, always_update=True)
    highlight: reactive[int | None] = reactive(None)

    def __init__(
        self,
        color_min: tuple[int, int, int] = (0, 180, 0),
        color_max: tuple[int, int, int] = (255, 200, 0),
        highlight_color: str = "bold white on blue",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._color_min = color_min
        self._color_max = color_max
        self._highlight_color = highlight_color

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height - 1  # 1 row for hour markers
        if height < 1 or not self.data:
            return Text("")

        data = list(self.data)
        timestamps = list(self.timestamps)
        n = len(data)
        highlight = self.highlight

        # resample to fit width
        if n > width:
            resampled_data = []
            resampled_ts = []
            for i in range(width):
                lo = i * n // width
                hi = (i + 1) * n // width
                resampled_data.append(sum(data[lo:hi]) / (hi - lo))
                resampled_ts.append(timestamps[lo])
            data = resampled_data
            timestamps = resampled_ts
            highlight = highlight * width // n if highlight is not None else None
        elif n < width:
            # pad left with blanks so chart is right-aligned
            pad = width - n
            data = [None] * pad + data
            timestamps = [None] * pad + timestamps
            highlight = highlight + pad if highlight is not None else None

        cols = len(data)
        real_vals = [v for v in data if v is not None]
        if not real_vals:
            return Text("")
        min_val = min(real_vals)
        max_val = max(real_vals)
        val_range = max_val - min_val or 1

        max_units = height * 8
        normalized = [
            int((v - min_val) / val_range * (max_units - 1)) if v is not None else -1
            for v in data
        ]

        # find hour boundaries
        hour_marks = set()
        for i in range(1, cols):
            if timestamps[i] is not None and timestamps[i - 1] is not None:
                if timestamps[i].hour != timestamps[i - 1].hour:
                    hour_marks.add(i)

        # precompute per-column gradient color based on normalized value
        def _lerp_color(t: float) -> str:
            r = int(self._color_min[0] + (self._color_max[0] - self._color_min[0]) * t)
            g = int(self._color_min[1] + (self._color_max[1] - self._color_min[1]) * t)
            b = int(self._color_min[2] + (self._color_max[2] - self._color_min[2]) * t)
            return f"rgb({r},{g},{b})"

        col_colors = [
            _lerp_color(v / max(max_units - 1, 1)) if v >= 0 else ""
            for v in normalized
        ]

        lines: list[Text] = []
        for row in range(height):
            row_bottom = (height - 1 - row) * 8
            line = Text()
            for col in range(cols):
                val = normalized[col]
                is_hl = highlight is not None and col == highlight
                is_hour = col in hour_marks

                if val < 0:
                    if is_hour:
                        line.append("┊", style="dim")
                    else:
                        line.append(" ")
                    continue

                fill = val - row_bottom
                if fill >= 8:
                    char = BLOCKS[8]
                elif fill > 0:
                    char = BLOCKS[fill]
                elif is_hour:
                    char = "┊"
                else:
                    char = " "

                if is_hl:
                    line.append(char if char.strip() else "│", style=self._highlight_color)
                elif is_hour and char == "┊":
                    line.append(char, style="dim")
                elif fill > 0:
                    line.append(char, style=col_colors[col])
                else:
                    line.append(char)
            lines.append(line)

        # hour marker labels: place greedily left-to-right, skipping any that
        # would overlap the previous label (keeps at least 1 space between them).
        marker = [" "] * cols
        last_end = -2
        for i in sorted(hour_marks):
            if timestamps[i] is None:
                continue
            label = timestamps[i].strftime("%H")
            start = max(0, i - len(label) // 2)
            if start <= last_end + 1:
                continue
            for j, ch in enumerate(label):
                pos = start + j
                if 0 <= pos < cols:
                    marker[pos] = ch
            last_end = start + len(label) - 1
        lines.append(Text("".join(marker), style="dim"))

        return Text("\n").join(lines)


class BigStat(Static):
    value = reactive("")

    def __init__(self, title: str, unit: str, color: str = "", **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._unit = unit
        self._color = color

    def render(self) -> str:
        c = self._color or "white"
        return f"[dim]{self._title}[/]\n[bold {c}]{self.value}[/] [dim]{self._unit}[/]"


class AranetTUI(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #stats {
        height: 5;
        padding: 1 2;
    }
    #stats BigStat {
        width: 1fr;
        content-align: center middle;
        text-align: center;
    }
    #main-area {
        layout: vertical;
        height: 1fr;
    }
    #main-area.wide {
        layout: horizontal;
    }
    #sparklines {
        layout: horizontal;
        height: 12;
        padding: 0 2;
    }
    #main-area.wide #sparklines {
        layout: vertical;
        width: 2fr;
        height: 1fr;
    }
    .spark-box {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    #main-area.wide .spark-box {
        width: 100%;
        height: 1fr;
    }
    .spark-box Label {
        text-align: center;
        width: 100%;
        text-style: dim;
    }
    .spark-box Chart {
        height: 1fr;
    }
    #table-box {
        padding: 0 2;
        height: 1fr;
    }
    #main-area.wide #table-box {
        width: 1fr;
        height: 1fr;
    }
    DataTable {
        height: 1fr;
    }
    #status-bar {
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "clear_selection", "Clear selection"),
    ]

    selected_index: reactive[int | None] = reactive(None)

    def __init__(self, db_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="stats"):
            yield BigStat("CO₂", "ppm", id="co2-stat")
            yield BigStat("Temp", "°C", color="cyan", id="temp-stat")
            yield BigStat("Humidity", "%", color="blue", id="hum-stat")
            yield BigStat("Pressure", "hPa", color="magenta", id="pres-stat")
            yield BigStat("Battery", "%", color="green", id="bat-stat")
        with Container(id="main-area"):
            with Container(id="sparklines"):
                with Vertical(classes="spark-box"):
                    yield Label("CO₂ ppm")
                    yield Chart(color_min=(0, 180, 0), color_max=(220, 50, 50), id="co2-chart")
                with Vertical(classes="spark-box"):
                    yield Label("Temp °C")
                    yield Chart(color_min=(40, 120, 160), color_max=(0, 255, 255), id="temp-chart")
                with Vertical(classes="spark-box"):
                    yield Label("Humidity %")
                    yield Chart(color_min=(50, 70, 160), color_max=(80, 140, 255), id="hum-chart")
            with Vertical(id="table-box"):
                yield DataTable(id="table")
        yield Static("", id="status-bar")
        yield Footer()

    def on_resize(self, event: Resize) -> None:
        self.query_one("#main-area").set_class(event.size.width >= WIDE_LAYOUT_COLS, "wide")

    def on_mount(self) -> None:
        self.title = "Aranet4"
        table = self.query_one("#table", DataTable)
        table.add_columns("Time", "CO₂ ppm", "Temp °C", "Humidity %", "Pressure hPa", "Battery %")
        table.cursor_type = "row"
        self._table_rows: list[dict] = []
        self._chart_rows: list[dict] = []
        self.load_data()
        self.set_interval(REFRESH_INTERVAL, self.load_data)

    def load_data(self) -> None:
        table_rows = query_readings(self.db_path, TABLE_LIMIT)
        chart_rows = query_readings_since(self.db_path, CHART_WINDOW_HOURS)
        if not table_rows:
            return
        self._table_rows = table_rows
        self._chart_rows = chart_rows

        timestamps = [parse_ts(r["timestamp"]) for r in chart_rows]

        co2_chart = self.query_one("#co2-chart", Chart)
        co2_chart.data = [r["co2"] for r in chart_rows]
        co2_chart.timestamps = timestamps

        temp_chart = self.query_one("#temp-chart", Chart)
        temp_chart.data = [r["temperature"] for r in chart_rows]
        temp_chart.timestamps = timestamps

        hum_chart = self.query_one("#hum-chart", Chart)
        hum_chart.data = [r["humidity"] for r in chart_rows]
        hum_chart.timestamps = timestamps

        table = self.query_one("#table", DataTable)
        table.clear()
        for r in table_rows:
            table.add_row(
                format_ts_full(r["timestamp"]),
                str(r["co2"]),
                f"{r['temperature']:.1f}",
                f"{r['humidity']:.0f}",
                f"{r['pressure']:.1f}",
                "—" if r["battery"] is None else str(r["battery"]),
            )

        self._update_stats(table_rows[0])
        self._update_status_bar(table_rows)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row is None or not self._table_rows:
            return
        row_idx = event.cursor_row
        if row_idx >= len(self._table_rows):
            return
        selected = self._table_rows[row_idx]
        ts = selected["timestamp"]
        chart_idx = next(
            (i for i, r in enumerate(self._chart_rows) if r["timestamp"] == ts), None
        )
        self.query_one("#co2-chart", Chart).highlight = chart_idx
        self.query_one("#temp-chart", Chart).highlight = chart_idx
        self.query_one("#hum-chart", Chart).highlight = chart_idx
        self._update_stats(selected)

    def _update_stats(self, reading: dict) -> None:
        co2_stat = self.query_one("#co2-stat", BigStat)
        co2_stat.value = str(reading["co2"])
        co2_stat._color = co2_color(reading["co2"])
        co2_stat.refresh()
        self.query_one("#temp-stat", BigStat).value = f"{reading['temperature']:.1f}"
        self.query_one("#hum-stat", BigStat).value = f"{reading['humidity']:.0f}"
        self.query_one("#pres-stat", BigStat).value = f"{reading['pressure']:.1f}"
        self.query_one("#bat-stat", BigStat).value = str(reading["battery"])

    def _update_status_bar(self, rows: list[dict]) -> None:
        latest = rows[0]
        ago = datetime.now(timezone.utc) - datetime.fromisoformat(latest["timestamp"])
        mins = int(ago.total_seconds() // 60)
        self.query_one("#status-bar", Static).update(
            f" {len(self._chart_rows)} pts  ·  {CHART_WINDOW_HOURS}h window  ·  latest {mins}m ago  ·  refresh {REFRESH_INTERVAL}s"
        )

    def action_clear_selection(self) -> None:
        self.query_one("#co2-chart", Chart).highlight = None
        self.query_one("#temp-chart", Chart).highlight = None
        self.query_one("#hum-chart", Chart).highlight = None
        if self._table_rows:
            self._update_stats(self._table_rows[0])

    def action_refresh(self) -> None:
        self.load_data()


def main():
    parser = argparse.ArgumentParser(prog="aranet4-tui", description="Aranet4 readings TUI")
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: platform data dir or $ARANET_LOG_DB)")
    args = parser.parse_args()
    db_path = args.db.expanduser() if args.db else default_db_path()
    if not db_path.exists():
        raise SystemExit(
            f"No readings DB at {db_path}. Run `aranet-log --once` first, or pass --db."
        )
    AranetTUI(db_path=db_path).run()


if __name__ == "__main__":
    main()
