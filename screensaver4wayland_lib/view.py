"""Pure-image GTK surfaces for each monitor, plus the settings window."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango
import cairo
from pathlib import Path
from copy import deepcopy
from .config import Config, image_geometry


class ImageCanvas(Gtk.DrawingArea):
    def __init__(self, pixbuf=None, fit="cover"):
        super().__init__()
        self.pixbuf, self.fit = pixbuf, fit
        self.connect("draw", self._draw)

    def set_image(self, pixbuf, fit=None):
        self.pixbuf = pixbuf
        if fit:
            self.fit = fit
        self.queue_draw()

    def _draw(self, _widget, context):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        context.set_source_rgb(0, 0, 0)
        context.paint()
        if self.pixbuf and w > 0 and h > 0:
            scale, x, y = image_geometry(self.pixbuf.get_width(), self.pixbuf.get_height(), w, h, self.fit)
            context.save()
            context.rectangle(0, 0, w, h)
            context.clip()
            context.translate(x, y)
            context.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(context, self.pixbuf, 0, 0)
            context.get_source().set_filter(cairo.FILTER_BILINEAR)
            context.paint()
            context.restore()
        return False


class SaverWindow(Gtk.ApplicationWindow):
    def __init__(self, app, monitor_index, pixbuf, fit=None, monitor_key=""):
        super().__init__(application=app, title="静景 · Screensaver4Wayland")
        self.monitor_index = monitor_index
        self.monitor_key = monitor_key
        self.image_path = ""
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.canvas = ImageCanvas(pixbuf, fit or app.config.fit)
        self.add(self.canvas)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.TOUCH_MASK)
        for event in ("key-press-event", "button-press-event", "scroll-event", "touch-event"):
            self.connect(event, lambda *_: (app.dismiss("input"), True)[1])
        self.connect("delete-event", lambda *_: (app.dismiss("window-closed"), True)[1])
        self.connect("realize", self._realize)
        self.connect("window-state-event", self._state)
        self.fullscreen_on_monitor(Gdk.Screen.get_default(), monitor_index)

    def _realize(self, *_):
        self.get_window().set_cursor(Gdk.Cursor.new_for_display(self.get_display(), Gdk.CursorType.BLANK_CURSOR))

    def _state(self, _window, event):
        # If the desktop leaves fullscreen (e.g. via its window menu), dismiss.
        if event.changed_mask & Gdk.WindowState.FULLSCREEN and not event.new_window_state & Gdk.WindowState.FULLSCREEN:
            self.get_application().dismiss("left-fullscreen")
        return False


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="静景 · 屏保控制台")
        self.app = app
        self.busy = False
        self.dirty = False
        self.closed = False
        self._syncing = True
        self._thumbnail_timer = 0
        self._thumbnail_key = None
        self.preview_generation = 0
        self.preview_path = ""
        self.preview_error = ""
        self._idle_unit = "minutes"
        self._target_id = "default"
        self._default_settings = app.config.image_settings()
        self._profiles = deepcopy(app.config.monitor_profiles)
        self._runtime_timer = 0
        self.set_default_size(940, 640)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("screensaver4wayland-settings")
        self._style()
        header = Gtk.HeaderBar(title="静景", show_close_button=True)
        self.set_titlebar(header)
        toggle = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER, margin_end=6)
        toggle.add(Gtk.Label(label="自动屏保"))
        self.enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.enabled.set_tooltip_text("立即暂停或恢复自动屏保")
        toggle.add(self.enabled)
        header.pack_end(toggle)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(scroll, True, True, 0)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=20)
        scroll.add(root)
        monitor_row = Gtk.Box(spacing=14)
        monitor_row.add(self._label("屏幕", "qf-section"))
        self.monitor_selector = Gtk.ComboBoxText()
        monitor_row.pack_start(self.monitor_selector, True, True, 0)
        root.pack_start(monitor_row, False, False, 0)

        columns = Gtk.Box(spacing=24, homogeneous=True, valign=Gtk.Align.START)
        root.pack_start(columns, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        columns.pack_start(left, True, True, 0)
        left.add(self._label("预览", "qf-section"))
        self.preview_frame = Gtk.AspectFrame(ratio=16 / 9, obey_child=False)
        self.preview_frame.set_shadow_type(Gtk.ShadowType.NONE)
        overlay = Gtk.Overlay()
        self.thumbnail = ImageCanvas(fit=app.config.fit)
        self.thumbnail.set_size_request(350, 250)
        overlay.add(self.thumbnail)
        self.placeholder = Gtk.Label(label="正在读取图片…", wrap=True, margin=18)
        self.placeholder.get_style_context().add_class("qf-placeholder")
        self.placeholder.set_no_show_all(True)
        overlay.add_overlay(self.placeholder)
        self.preview_frame.add(overlay)
        left.add(self.preview_frame)
        self.image_name = self._label("正在读取图片…")
        self.image_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.image_name.set_max_width_chars(42)
        left.add(self.image_name)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        columns.pack_start(self.controls, True, True, 0)
        self.controls.add(self._label("图片来源", "qf-section"))
        self.source = Gtk.ComboBoxText()
        self.controls.add(self.source)
        self.source_stack = Gtk.Stack()
        self.source_stack.set_hhomogeneous(False)
        self.source_stack.set_vhomogeneous(False)
        self.source_stack.add_named(Gtk.Box(), "wallpaper")
        self.source_stack.add_named(Gtk.Box(), "inherit")
        self.image = Gtk.FileChooserButton(title="选择屏保图片", action=Gtk.FileChooserAction.OPEN)
        self.image.set_local_only(True)
        self.image.set_width_chars(24)
        filter_ = Gtk.FileFilter()
        filter_.set_name("图片（JPEG / PNG / WebP / BMP）")
        for mime in ("image/jpeg", "image/png", "image/webp", "image/bmp"):
            filter_.add_mime_type(mime)
        for pattern in ("*.webp", "*.WEBP"):
            filter_.add_pattern(pattern)
        self.image.add_filter(filter_)
        image_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        image_panel.add(self.image)
        self.source_stack.add_named(image_panel, "image")
        self.folder = Gtk.FileChooserButton(title="选择图片文件夹", action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.folder.set_local_only(True)
        self.folder.set_width_chars(24)
        folder_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        folder_panel.add(self.folder)
        self.interval = Gtk.SpinButton.new_with_range(5, 86400, 5)
        interval_row = Gtk.Box(spacing=10)
        interval_row.pack_start(self._label("轮播间隔"), True, True, 0)
        interval_row.add(self.interval)
        interval_row.add(Gtk.Label(label="秒"))
        folder_panel.add(interval_row)
        self.source_stack.add_named(folder_panel, "folder")
        self.source_stack.show_all()
        self.controls.add(self.source_stack)

        self.controls.add(self._label("缩放", "qf-section"))
        self.fit = Gtk.ComboBoxText()
        self.fit.append("cover", "铺满屏幕")
        self.fit.append("contain", "完整显示")
        self.controls.add(self.fit)

        self.controls.add(Gtk.Separator())
        idle_label = self._label("闲置时间", "qf-section")
        idle_label.set_tooltip_text("所有屏幕共用此等待时间")
        self.controls.add(idle_label)
        idle_row = Gtk.Box(spacing=10)
        self.idle_value = Gtk.SpinButton.new_with_range(1, 1440, 1)
        self.idle_value.set_numeric(True)
        self.idle_unit = Gtk.ComboBoxText()
        self.idle_unit.append("seconds", "秒")
        self.idle_unit.append("minutes", "分钟")
        idle_row.pack_start(self.idle_value, True, True, 0)
        idle_row.add(self.idle_unit)
        self.controls.add(idle_row)
        self.inhibitors = Gtk.CheckButton(label="播放或演示时暂缓启动")
        self.controls.add(self.inhibitors)

        outer.pack_start(Gtk.Separator(), False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=18)
        outer.pack_end(footer, False, False, 0)
        self.message = Gtk.Label(xalign=0, wrap=True)
        self.message.set_no_show_all(True)
        self.message.set_max_width_chars(90)
        self.message.set_ellipsize(Pango.EllipsizeMode.END)
        footer.add(self.message)
        action_row = Gtk.Box(spacing=10)
        self.changes_label = self._label("", "dim-label")
        action_row.pack_start(self.changes_label, True, True, 0)
        self.revert_button = Gtk.Button(label="撤销")
        self.apply_button = Gtk.Button(label="应用")
        self.preview_button = Gtk.Button(label="应用并预览")
        self.preview_button.get_style_context().add_class("suggested-action")
        self.revert_button.connect("clicked", lambda *_: self.load_config(self.app.config))
        self.apply_button.connect("clicked", lambda *_: self.apply())
        self.preview_button.connect("clicked", lambda *_: self.apply(preview=True))
        for button in (self.revert_button, self.apply_button, self.preview_button):
            action_row.add(button)
        footer.add(action_row)

        for widget, event in ((self.source, "changed"), (self.image, "file-set"), (self.folder, "file-set"),
                              (self.idle_value, "value-changed"), (self.interval, "value-changed"),
                              (self.fit, "changed"), (self.inhibitors, "toggled")):
            widget.connect(event, self._changed)
        self.idle_unit.connect("changed", self._unit_changed)
        self.monitor_selector.connect("changed", self._target_changed)
        self.enabled.connect("notify::active", self._toggle_enabled)
        self.connect("destroy", self._destroyed)
        self.load_config(app.config)
        self._runtime_timer = GLib.timeout_add_seconds(2, self.refresh_runtime)

    @staticmethod
    def _label(text, style=None):
        label = Gtk.Label(label=text, xalign=0)
        if style:
            label.get_style_context().add_class(style)
        return label

    def _style(self):
        self._css = Gtk.CssProvider()
        self._css.load_from_data(b"""
            .screensaver4wayland-settings .qf-heading { font-size: 20px; font-weight: 600; }
            .screensaver4wayland-settings .qf-section { font-weight: 600; }
            .screensaver4wayland-settings .qf-placeholder { color: #dddddd; }
            .screensaver4wayland-settings .qf-error { color: #bf616a; }
            .screensaver4wayland-settings button { padding: 7px 12px; }
        """)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    @staticmethod
    def duration(seconds):
        return f"{seconds // 60} 分钟" if seconds % 60 == 0 else f"{seconds} 秒"

    def load_config(self, config):
        self._syncing = True
        self._default_settings = config.image_settings()
        self._profiles = deepcopy(config.monitor_profiles)
        self._fill_monitors()
        self._load_editor()
        self.inhibitors.set_active(config.respect_inhibitors)
        self.set_idle_seconds(config.idle_seconds)
        self._syncing = False
        self._thumbnail_key = None
        self.show_message("")
        self._changed()
        self.refresh_runtime()

    def _fill_monitors(self):
        self.monitor_selector.remove_all()
        self.monitor_selector.append("default", "默认设置")
        keys = {"default"}
        for monitor in self.app.monitors:
            self.monitor_selector.append(monitor.key, monitor.name)
            keys.add(monitor.key)
        if self._target_id not in keys:
            self._target_id = "default"
        self.monitor_selector.set_active_id(self._target_id)

    def _load_editor(self):
        picture = dict(self._default_settings)
        picture.update(self._profiles.get(self._target_id, {}))
        self.source.remove_all()
        if self._target_id != "default":
            self.source.append("inherit", "跟随默认")
        for key, title in (("wallpaper", "当前壁纸"), ("image", "指定图片"), ("folder", "文件夹轮播")):
            self.source.append(key, title)
        source = "inherit" if self._target_id != "default" and self._target_id not in self._profiles else picture["source"]
        self.source.set_active_id(source)
        for widget, path in ((self.image, picture["image_path"]), (self.folder, picture["folder_path"])):
            widget.unselect_all()
            if path:
                widget.set_filename(path)
        self.interval.set_value(picture["slideshow_seconds"])
        self.fit.set_active_id(picture["fit"])
        self.source_stack.set_visible_child_name(source)
        monitor = next((m for m in self.app.monitors if m.key == self._target_id), None)
        self.preview_frame.set_property("ratio", monitor.width / monitor.height if monitor else 16 / 9)

    def _store_editor(self):
        if self._syncing or not self.source.get_active_id():
            return
        if self.source.get_active_id() == "inherit":
            self._profiles.pop(self._target_id, None)
            return
        picture = {"source": self.source.get_active_id(), "image_path": self.image.get_filename() or "",
                   "folder_path": self.folder.get_filename() or "", "slideshow_seconds": self.interval.get_value_as_int(),
                   "fit": self.fit.get_active_id()}
        if self._target_id == "default":
            self._default_settings = picture
        else:
            self._profiles[self._target_id] = picture

    def _target_changed(self, *_):
        if self._syncing:
            return
        self._store_editor()
        self._target_id = self.monitor_selector.get_active_id() or "default"
        self._syncing = True
        self._load_editor()
        self._syncing = False
        self.show_message("")
        self._changed()

    def refresh_monitors(self):
        self._store_editor()
        self._syncing = True
        self._fill_monitors()
        self._load_editor()
        self._syncing = False
        self._changed()

    def set_idle_seconds(self, seconds):
        was_syncing = self._syncing
        self._syncing = True
        self._idle_unit = "minutes" if seconds % 60 == 0 else "seconds"
        self.idle_unit.set_active_id(self._idle_unit)
        self.idle_value.set_range(1 if self._idle_unit == "minutes" else 5,
                                  1440 if self._idle_unit == "minutes" else 86400)
        self.idle_value.set_value(seconds / 60 if self._idle_unit == "minutes" else seconds)
        self._syncing = was_syncing
        self._changed()

    def _unit_changed(self, *_):
        if self._syncing:
            return
        seconds = self.idle_value.get_value_as_int() * (60 if self._idle_unit == "minutes" else 1)
        self._idle_unit = self.idle_unit.get_active_id()
        self._syncing = True
        self.idle_value.set_range(1 if self._idle_unit == "minutes" else 5,
                                  1440 if self._idle_unit == "minutes" else 86400)
        self.idle_value.set_value((seconds + 59) // 60 if self._idle_unit == "minutes" else seconds)
        self._syncing = False
        self._changed()

    def draft(self):
        self._store_editor()
        return Config(enabled=self.app.config.enabled,
                      idle_seconds=self.idle_value.get_value_as_int() * (60 if self.idle_unit.get_active_id() == "minutes" else 1),
                      respect_inhibitors=self.inhibitors.get_active(), **self._default_settings,
                      monitor_profiles=deepcopy(self._profiles))

    def _changed(self, widget=None, *_):
        if self._syncing or self.closed:
            return
        config = self.draft()
        if widget is self.source and self.source.get_active_id() == "inherit":
            self._syncing = True
            self._load_editor()
            self._syncing = False
        self.source_stack.set_visible_child_name(self.source.get_active_id())
        self.fit.set_sensitive(self.source.get_active_id() != "inherit")
        self.dirty = config != self.app.config
        self._update_buttons()
        picture = config.for_monitor(self._target_id if self._target_id != "default" else None)
        key = (self._target_id, tuple(picture.image_settings().items()))
        if key != self._thumbnail_key:
            self._thumbnail_key = key
            self.preview_generation += 1
            self.preview_path = ""
            if self._thumbnail_timer:
                GLib.source_remove(self._thumbnail_timer)
            def load():
                self._thumbnail_timer = 0
                try:
                    picture.validate()
                    self.placeholder.set_text("正在读取图片…")
                    self.placeholder.show()
                    self.app.load_thumbnail(self, picture)
                except ValueError as e:
                    self.thumbnail_failed(str(e))
                return GLib.SOURCE_REMOVE
            self._thumbnail_timer = GLib.timeout_add(150, load)

    def thumbnail_ready(self, pixbuf, path, count, fit):
        self.preview_error = ""
        self.preview_path = str(path)
        self.thumbnail.set_image(pixbuf, fit)
        self.placeholder.hide()
        self.image_name.set_text(Path(path).name)
        self.image_name.set_tooltip_text(str(path))

    def thumbnail_failed(self, message):
        self.preview_error = message
        self.preview_path = ""
        self.thumbnail.set_image(None)
        self.placeholder.set_text("请选择可用的图片")
        self.placeholder.show()
        self.image_name.set_text("暂时无法预览")
        self.image_name.set_tooltip_text(message)
        self.show_message(message, error=True)

    def _update_buttons(self):
        self.controls.set_sensitive(not self.busy)
        self.monitor_selector.set_sensitive(not self.busy)
        self.apply_button.set_sensitive(not self.busy and self.dirty)
        self.preview_button.set_sensitive(not self.busy)
        self.revert_button.set_sensitive(not self.busy and self.dirty)
        self.changes_label.set_text("检查中…" if self.busy else ("未应用" if self.dirty else "已应用"))

    def show_message(self, message, error=False):
        self.message.set_text(message)
        self.message.set_visible(bool(message))
        context = self.message.get_style_context()
        (context.add_class if error else context.remove_class)("qf-error")

    def refresh_runtime(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        config = self.app.config
        self._syncing = True
        self.enabled.set_active(config.enabled)
        self._syncing = False
        self.dirty = self.draft() != config
        self._update_buttons()
        return GLib.SOURCE_CONTINUE

    def _toggle_enabled(self, *_):
        if self._syncing:
            return
        try:
            self.app.set_enabled(self.enabled.get_active())
            self.show_message("")
        except (ValueError, TypeError, OSError) as e:
            self.show_message(str(e), error=True)
        self.refresh_runtime()

    def apply(self, preview=False):
        if self.busy:
            return
        try:
            config = self.draft().validate()
        except (ValueError, TypeError, OSError) as e:
            self.show_message(str(e), error=True)
            return
        self.busy = True
        self.show_message("")
        self._update_buttons()
        def finished(error):
            if self.closed:
                return
            self.busy = False
            if error:
                self.show_message(error, error=True)
            else:
                self.show_message("")
                if preview:
                    # Let the initiating button release before arming the input watch.
                    self.app._timer("settings-preview", 250, lambda: self.app.show_saver(preview=True))
            self.refresh_runtime()
        self.app.apply_config_async(config, finished)

    def _destroyed(self, *_):
        self.closed = True
        self.preview_generation += 1
        for timer in (self._thumbnail_timer, self._runtime_timer):
            if timer:
                GLib.source_remove(timer)
        Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), self._css)
