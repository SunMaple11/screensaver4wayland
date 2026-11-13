"""GNOME's session D-Bus interfaces; no X11 hooks or injected Shell code."""
import logging
from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)
IDLE_NAME = "org.gnome.Mutter.IdleMonitor"
IDLE_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
LOCK_NAME = "org.gnome.ScreenSaver"
LOCK_PATH = "/org/gnome/ScreenSaver"
SESSION_NAME = "org.gnome.SessionManager"
SESSION_PATH = "/org/gnome/SessionManager"


def call(proxy, method, signature=None, args=()):
    params = GLib.Variant(signature, args) if signature else None
    return proxy.call_sync(method, params, Gio.DBusCallFlags.NONE, 1500, None).unpack()


class Desktop:
    def __init__(self, on_idle, on_active, on_state):
        self.on_idle, self.on_active, self.on_state = on_idle, on_active, on_state
        self.idle_watch = self.active_watch = 0
        self.idle_seconds = 300
        self.locked = True  # Until the locker has answered, fail closed.
        self.closed = False
        self.idle = self._proxy(IDLE_NAME, IDLE_PATH)
        self.lock = self._proxy(LOCK_NAME, LOCK_PATH)
        self.session = self._proxy(SESSION_NAME, SESSION_PATH)
        self.idle.connect("g-signal", self._idle_signal)
        self.idle.connect("notify::g-name-owner", self._idle_owner)
        self.lock.connect("g-signal", self._lock_signal)
        self.lock.connect("notify::g-name-owner", self._lock_owner)
        self.session.connect("g-properties-changed", lambda *_: self.on_state())
        self.session.connect("notify::g-name-owner", lambda *_: self.on_state())
        self._refresh_lock()

    @staticmethod
    def _proxy(name, path):
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None, name, path, name, None)

    def available(self):
        return all(p.get_name_owner() for p in (self.idle, self.lock, self.session))

    def blocked_reason(self, respect_inhibitors=True):
        if not self.available():
            return "等待 GNOME 桌面连接"
        if self.locked:
            return "系统已锁屏"
        active = self.session.get_cached_property("SessionIsActive")
        if active is None or not active.unpack():
            return "当前会话未激活"
        actions = self.session.get_cached_property("InhibitedActions")
        if respect_inhibitors and (actions is None or actions.unpack() & 8):
            return "播放或演示正在阻止空闲"
        return ""

    def idletime(self):
        return call(self.idle, "GetIdletime")[0]

    def configure(self, seconds):
        self.idle_seconds = seconds
        self._remove("idle_watch")
        if not self.closed and self.idle.get_name_owner():
            self.idle_watch = call(self.idle, "AddIdleWatch", "(t)", (seconds * 1000,))[0]

    def watch_activity(self):
        self._remove("active_watch")
        self.active_watch = call(self.idle, "AddUserActiveWatch")[0]

    def stop_activity_watch(self):
        self._remove("active_watch")

    def _remove(self, key):
        watch = getattr(self, key)
        setattr(self, key, 0)
        if watch and self.idle.get_name_owner():
            try:
                call(self.idle, "RemoveWatch", "(u)", (watch,))
            except GLib.Error as e:
                LOG.warning("移除空闲监听失败: %s", e.message)

    def _idle_signal(self, _proxy, _sender, signal, args):
        if signal != "WatchFired" or self.closed:
            return
        watch = args.unpack()[0]
        if watch == self.idle_watch:
            self.on_idle()
        elif watch == self.active_watch:
            self.active_watch = 0  # GNOME removes this one-shot watch itself.
            self.on_active()

    def _idle_owner(self, *_):
        self.idle_watch = self.active_watch = 0
        try:
            self.configure(self.idle_seconds)
        except GLib.Error:
            LOG.exception("等待空闲检测重新连接")
        self.on_state()

    def _refresh_lock(self):
        try:
            self.locked = call(self.lock, "GetActive")[0] if self.lock.get_name_owner() else True
        except GLib.Error:
            self.locked = True

    def _lock_owner(self, *_):
        self._refresh_lock()
        self.on_state()

    def _lock_signal(self, _proxy, _sender, signal, args):
        if signal == "ActiveChanged":
            self.locked = args.unpack()[0]
            self.on_state()

    def close(self):
        self.closed = True
        self._remove("idle_watch")
        self._remove("active_watch")
