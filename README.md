# screensaver4wayland · 静景

A still-image screensaver for GNOME on Wayland.

为 **Ubuntu 22.04 / GNOME 42 / Wayland** 编写的本地图片屏保。闲置后，每台显示器各显示一张全屏图片；移动鼠标、按键、点击或触摸后退出。画面没有时钟、标题或文字。

当前实现使用 GNOME Mutter 的空闲检测接口，已在上述环境验证；尚未适配 KDE、Sway 等其他 Wayland 桌面。

默认 **5 分钟**启动，使用当前桌面壁纸。每块屏幕可分别选择单张图片或轮播文件夹，并独立设置缩放方式、轮播间隔。所有图片留在本机，程序不使用网络。

应用图标为透明背景的显示器 SVG：[查看源文件](assets/io.github.sunmaple.Screensaver4Wayland.svg)。安装时会刷新图标缓存，并统一 Wayland 窗口与桌面入口的应用标识。

## 安装与使用

### Debian 安装包（推荐）

从 GitHub Releases 下载 `.deb`，在下载目录运行：

```bash
sudo apt install ./screensaver4wayland_1.3.1-1_all.deb
screensaver4wayland --settings
```

依赖由 `apt` 安装，无需自行编译。应用列表搜索 **静景** 即可打开；以后登录 GNOME Wayland 时自动启动。已验证 Ubuntu 22.04 / GNOME 42 / amd64，其他桌面或版本尚未实测。

如果之前使用过下方的源码安装方式，先在源码目录执行 `python3 install.py --uninstall`，保留个人配置后再安装 `.deb`，避免旧的用户目录程序覆盖系统版本。

### 从源码安装

先安装运行依赖：

```bash
sudo apt install python3-gi python3-gi-cairo python3-pil gir1.2-gtk-3.0 librsvg2-common
```

在本项目目录执行：

```bash
python3 install.py
```

这一步无需 sudo，程序安装到当前用户目录并启动后台服务。随后在应用列表搜索 **静景**，或运行：

```bash
~/.local/bin/screensaver4wayland --settings
~/.local/bin/screensaver4wayland --preview
```

控制台提供屏幕选择、实时缩略图和等待时间设置：

1. 在“屏幕”中选择 **默认设置**，或选择具体的左屏、右屏。
2. 在“图片来源”中选当前壁纸、指定图片或文件夹轮播；选图后左侧更新预览。具体屏幕可选 **跟随默认** 来共用默认图片。
3. 可以先分别配置多块屏幕，切换屏幕不会丢失尚未应用的选择。缩放和轮播间隔随当前选中的屏幕保存。
4. **闲置时间** 为所有屏幕共用，支持秒和分钟。
5. 点击 **应用** 保存；点击 **应用并预览** 立即在各块屏幕显示各自的图片。右上角开关控制自动启动，**撤销** 返回已保存的配置。

底部只保留简短的应用状态和必要的错误提示。图片先经过读取检查，损坏的图片不会覆盖当前生效的设置。全屏预览时移动鼠标或按键会退出所有屏幕的屏保。控制台打开时自动屏保仍可按已应用的等待时间触发；关闭控制台后后台服务继续运行。

独立屏保面向扩展桌面。程序优先按显示器硬件身份匹配配置，缺少唯一序列号时按接口区分，不依赖显示器的临时编号。断开的屏幕配置会保留，新接入且未单独配置的屏幕使用默认设置。旧版本的单一图片配置自动作为所有屏幕的默认设置继续使用。

```bash
screensaver4wayland --pause       # 暂停自动屏保，保存到配置
screensaver4wayland --resume      # 恢复自动屏保
screensaver4wayland --dismiss     # 退出正在显示的屏保
screensaver4wayland --status      # 查看连接、空闲时间、窗口和错误信息
screensaver4wayland --preview --duration 5
```

源码安装后如果命令不在 `PATH` 中，可使用完整路径 `~/.local/bin/screensaver4wayland`。

图片支持 JPEG、PNG、WebP 和 BMP。系统缺少 WebP 加载插件时，自动使用 Pillow 解码，无需手工转换格式。支持透明 WebP；动态 WebP 仅显示第一帧。文件夹轮播按文件名排序，只读取所选文件夹，最多 2000 张，不递归子文件夹。无法解码的图片会跳过（每次最多尝试 100 张）；没有可用图片时会退出屏保并记录错误。大型图片会缩小到显示所需的尺寸。

## 与 GNOME 的关系

- 使用 `org.gnome.Mutter.IdleMonitor` 获取整个 GNOME 会话的空闲和恢复活动信号，对原生 Wayland 应用同样有效。
- 用 GTK 3 原生 Wayland 全屏窗口逐屏显示，无需切换 Xorg，也无需安装 Shell 扩展。
- 默认尊重 GNOME 会话的空闲抑制，例如支持该机制的视频播放器和演示软件。
- 用户切换、锁屏、显示器连接变化时退出图片屏保。
- 这是**图片屏保，不是密码锁屏程序**。GNOME 的自动锁屏、息屏和休眠继续生效；本程序不会禁用它们。若系统先息屏，屏幕仍会按系统设置变黑。
- 全屏和窗口焦点最终由桌面管理。在 GNOME 的活动概览、系统弹窗等特殊界面中，普通应用窗口不能保证覆盖系统界面。
- 专门针对 GNOME 的接口开发；不保证适用于 KDE、Sway 等其他 Wayland 桌面。

## 配置与维护

配置文件为 `~/.config/screensaver4wayland/config.json`（遵循 `XDG_CONFIG_HOME`）。通过设置界面保存立即生效；手工编辑后重启服务。

```bash
systemctl --user status screensaver4wayland.service
systemctl --user restart screensaver4wayland.service
journalctl --user -u screensaver4wayland.service -n 50
```

`.deb` 将程序安装到 `/usr/share/screensaver4wayland`，命令为 `/usr/bin/screensaver4wayland`，用户服务位于 `/usr/lib/systemd/user/screensaver4wayland.service`。源码安装使用 `~/.local/share/screensaver4wayland`、`~/.local/bin/screensaver4wayland` 和 `~/.config/systemd/user/screensaver4wayland.service`。两种方式使用相同的个人配置目录。

从原名 **QuietFrame** 升级时，安装脚本会将旧配置复制到新目录，保留已有的图片选择和等待时间。如果新配置已经存在，则以新配置为准。旧配置与旧程序数据保留作备份，旧服务和旧菜单入口会被停用、移除；原有 `quietframe` 命令会转到新命令，避免重复运行两个屏保。

卸载 `.deb`（保留个人图片与配置）：

```bash
screensaver4wayland --quit
sudo apt purge screensaver4wayland
```

卸载源码安装的程序与登录自启动（保留个人配置）：

```bash
python3 install.py --uninstall
```

## 开发

依赖 Ubuntu 系统 Python 3.10、PyGObject、GTK 3、GdkPixbuf、Cairo 和支持 WebP 的 Pillow。这台电脑已安装这些依赖；其他 Ubuntu 系统可安装 `python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-pil`。不需要 pip 包。

```bash
python3 -m unittest discover -s tests -v
python3 tests/integration.py
```

构建源码包和 Debian 安装包：

```bash
python3 scripts/build_release.py
python3 scripts/check_release.py
```

输出位于 `dist/v1.3.1/`。包括 `.deb`、源码 ZIP / tar.gz、发布说明和 SHA-256 校验文件。构建只需要系统 Python 和 `dpkg-deb`；不需要 sudo、pip 或第三方构建服务。发布到 GitHub 的步骤见 [RELEASING.md](RELEASING.md)。

集成测试启动独立 D-Bus、临时配置和 GNOME 42 headless Wayland 桌面，使用两台虚拟显示器。测试专用扩展只在临时目录启用，用于检查窗口、合成输入和截图；产品不包含该扩展。测试总线禁用桌面服务自动激活，使用独立 keyfile 配置，不连接用户桌面的配置服务。

测试涵盖配置读写及校验、图片发现、缩放比例、真正的 Wayland 双屏全屏显示、闲置自动激活、轮播、鼠标/键盘退出、模拟锁屏和播放抑制、限时预览及程序退出。另通过真实 GTK 控件验证选图实时预览、秒／分钟设置、立即应用、损坏图片处理、暂停／恢复、撤销修改、全屏预览、每屏不同图片及独立轮播、跟随默认和重新打开界面；验证显示器重新排序后身份保持稳定。截图和日志保存在 `.artifacts/`。

## 参考

- [Wayland 协议](https://wayland.freedesktop.org/docs/book/Introduction.html)
- [GTK 的 Wayland 后端](https://docs.gtk.org/gtk3/wayland.html)
- [GTK 按显示器全屏](https://docs.gtk.org/gtk3/method.Window.fullscreen_on_monitor.html)
- [GNOME Mutter](https://mutter.gnome.org/)

## 许可证

代码和 SVG 图标以 [MIT License](LICENSE) 发布。
