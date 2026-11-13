"""Session/lock signals for the disposable integration bus only."""
import os
if os.environ.get("SCREENSAVER4WAYLAND_INTEGRATION_BUS") != "1":
    raise SystemExit("Run via tests/integration.py")
from gi.repository import Gio, GLib

XML = """<node>
<interface name="org.gnome.SessionManager">
 <method name="GetLocale"><arg type="i" direction="in"/><arg type="s" direction="out"/></method>
 <method name="RegisterClient"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="o" direction="out"/></method>
 <method name="UnregisterClient"><arg type="o" direction="in"/></method>
 <method name="IsInhibited"><arg type="u" direction="in"/><arg type="b" direction="out"/></method>
 <method name="CanShutdown"><arg type="b" direction="out"/></method>
 <method name="IsSessionRunning"><arg type="b" direction="out"/></method>
 <property name="SessionIsActive" type="b" access="read"/>
 <property name="InhibitedActions" type="u" access="read"/>
 <property name="SessionName" type="s" access="read"/>
</interface>
<interface name="org.gnome.ScreenSaver">
 <method name="GetActive"><arg type="b" direction="out"/></method>
 <method name="Lock"/>
 <signal name="ActiveChanged"><arg type="b"/></signal>
</interface>
<interface name="io.github.sunmaple.Screensaver4Wayland.TestSession">
 <method name="SetState"><arg type="b" direction="in"/><arg type="u" direction="in"/><arg type="b" direction="in"/></method>
</interface>
<interface name="org.gnome.SessionManager.Presence">
 <property name="status" type="u" access="read"/>
 <signal name="StatusChanged"><arg type="u"/></signal>
</interface>
</node>"""
state = {"locked": False, "inhibitors": 0, "active": True}
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def method(connection, sender, path, iface, name, args, invocation):
    if name == "SetState":
        state["locked"], state["inhibitors"], state["active"] = args.unpack()
        connection.emit_signal(None, "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "ActiveChanged", GLib.Variant("(b)", (state["locked"],)))
        props = {"SessionIsActive": GLib.Variant("b", state["active"]), "InhibitedActions": GLib.Variant("u", state["inhibitors"])}
        connection.emit_signal(None, "/org/gnome/SessionManager", "org.freedesktop.DBus.Properties", "PropertiesChanged", GLib.Variant("(sa{sv}as)", ("org.gnome.SessionManager", props, [])))
        answer = GLib.Variant("()", ())
    elif name == "GetActive":
        answer = GLib.Variant("(b)", (state["locked"],))
    elif name == "GetLocale":
        answer = GLib.Variant("(s)", ("C.UTF-8",))
    elif name == "RegisterClient":
        answer = GLib.Variant("(o)", ("/org/gnome/SessionManager/Client1",))
    elif name == "IsInhibited":
        answer = GLib.Variant("(b)", (bool(state["inhibitors"] & args.unpack()[0]),))
    elif name in ("CanShutdown", "IsSessionRunning"):
        answer = GLib.Variant("(b)", (name == "IsSessionRunning",))
    else:
        answer = GLib.Variant("()", ())
    invocation.return_value(answer)


def get_property(connection, sender, path, iface, name):
    return {"SessionIsActive": GLib.Variant("b", state["active"]), "InhibitedActions": GLib.Variant("u", state["inhibitors"]),
            "SessionName": GLib.Variant("s", "screensaver4wayland-test"), "status": GLib.Variant("u", 0)}[name]


for info in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
    path = "/org/gnome/ScreenSaver" if info.name == "org.gnome.ScreenSaver" else "/org/gnome/SessionManager"
    if info.name.endswith(".Presence"):
        path += "/Presence"
    bus.register_object(path, info, method, get_property, None)
for name in ("org.gnome.SessionManager", "org.gnome.ScreenSaver"):
    Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
