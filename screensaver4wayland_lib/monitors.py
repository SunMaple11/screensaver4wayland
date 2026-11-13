"""Match GTK monitor indices to persistent Mutter display identities."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import logging

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Monitor:
    key: str
    index: int
    name: str
    x: int
    y: int
    width: int
    height: int
    scale: int
    primary: bool

    @property
    def image_size(self):
        return min(7680, self.width * self.scale), min(4320, self.height * self.scale)


def match_monitors(snapshots, logical_monitors):
    """Position maps GTK outputs; EDID identities keep settings across reordering."""
    all_specs = [tuple(spec) for row in logical_monitors for spec in row[5]]
    counts = Counter(spec[1:] for spec in all_specs)
    used = set()
    result = []
    for snapshot in snapshots:
        matching = [row for row in logical_monitors if (row[0], row[1]) == (snapshot["x"], snapshot["y"])]
        specs = [tuple(spec) for row in matching for spec in row[5] if tuple(spec) not in used]
        spec = next((s for s in specs if s[2] == snapshot["model"]), specs[0] if specs else None)
        if spec:
            used.add(spec)
            connector, vendor, product, serial = spec
            valid_serial = serial and serial.lower() not in ("unknown", "none", "0", "0x00000000")
            identity = ("edid", vendor, product, serial) if valid_serial and counts[spec[1:]] == 1 else ("connector", *spec)
            primary = any(row[4] for row in matching)
            model = product or snapshot["model"] or connector
        else:
            model = snapshot["model"] or "显示器"
            same_model = [s for s in snapshots if (s["vendor"], s["model"]) == (snapshot["vendor"], snapshot["model"])]
            identity = ("gdk", snapshot["vendor"], model)
            if len(same_model) > 1:
                identity += (snapshot["x"], snapshot["y"])
            primary = snapshot["primary"]
        key = "monitor-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
        result.append(Monitor(key, snapshot["index"], model, snapshot["x"], snapshot["y"],
                              snapshot["width"], snapshot["height"], snapshot["scale"], primary))
    result.sort(key=lambda m: (m.x, m.y, m.index))
    named = []
    for i, monitor in enumerate(result):
        if len(result) == 2 and result[0].x != result[1].x:
            position = "左屏" if i == 0 else "右屏"
        elif len(result) == 2 and result[0].y != result[1].y:
            position = "上屏" if i == 0 else "下屏"
        else:
            position = f"屏幕 {i + 1}"
        named.append(Monitor(monitor.key, monitor.index, f"{position} · {monitor.name}", monitor.x, monitor.y,
                             monitor.width, monitor.height, monitor.scale, monitor.primary))
    return named


def read_monitors():
    display = Gdk.Display.get_default()
    snapshots = []
    for index in range(display.get_n_monitors()):
        monitor = display.get_monitor(index)
        geometry = monitor.get_geometry()
        snapshots.append({"index": index, "vendor": monitor.get_manufacturer() or "", "model": monitor.get_model() or "",
                          "x": geometry.x, "y": geometry.y, "width": geometry.width, "height": geometry.height,
                          "scale": monitor.get_scale_factor(), "primary": monitor.is_primary()})
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        state = bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                              "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
                              Gio.DBusCallFlags.NONE, 1500, None).unpack()
        logical_monitors = state[2]
    except GLib.Error as e:
        LOG.warning("无法读取显示器身份，使用桌面提供的显示器信息：%s", e.message)
        logical_monitors = []
    return match_monitors(snapshots, logical_monitors)
