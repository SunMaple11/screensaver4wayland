# 发布到 GitHub

当前版本为 `v1.3.1`，Debian 包版本为 `1.3.1-1`。许可证为 MIT。

## 1. 推送源码

在 GitHub 新建一个空仓库 `screensaver4wayland`。如果使用源码 ZIP / tar.gz，先解压并进入其中的项目目录；源码压缩包不包含 `.git`。

```bash
git init -b main
git add .
git commit -m "Release screensaver4wayland v1.3.1"
git remote add origin https://github.com/YOUR_USERNAME/screensaver4wayland.git
git push -u origin main
```

将 `YOUR_USERNAME` 换成你的 GitHub 用户名。如果项目已经有提交，则从 `git remote add` 开始；如果已经有 `origin`，使用已有远程地址。

## 2. 自动发布 Release

仓库中已包含 `.github/workflows/release.yml`。推送版本标签即可触发构建和发布：

```bash
git tag -a v1.3.1 -m "screensaver4wayland v1.3.1"
git push origin v1.3.1
```

工作流在 Ubuntu 22.04 上执行测试、构建并检查包，然后创建 GitHub Release，上传安装包、源码包、校验文件和构建信息。发布正文来自 `releases/v1.3.1.md`。使用 GitHub 自动提供的 token，无需另填个人访问令牌。标签必须与 `screensaver4wayland_lib/__init__.py` 中的版本一致；已有的 Release 不会被自动覆盖。

在 GitHub 的 **Actions → Release** 查看运行结果，完成后其他用户即可从 **Releases** 下载 `.deb`。尚未推送标签时，不会创建线上 Release。

## 3. 手动发布现成附件

如果不使用自动工作流，在 GitHub 的 **Releases → Draft a new release** 选择或创建 `v1.3.1`，将 `dist/v1.3.1/RELEASE_NOTES.md` 复制为正文，并上传这个目录内的所有文件：

- `screensaver4wayland_1.3.1-1_all.deb`
- `screensaver4wayland-1.3.1-source.zip`
- `screensaver4wayland-1.3.1-source.tar.gz`
- `SHA256SUMS`
- `BUILD_INFO.json`
- `RELEASE_NOTES.md`

自动方式与手动方式选一种即可；如果已经手动创建同名 Release，自动工作流会停止，避免覆盖已发布的附件。

## 4. 在本机重建

```bash
python3 scripts/build_release.py
python3 scripts/check_release.py
```

可指定发布仓库地址：

```bash
python3 scripts/build_release.py --homepage https://github.com/YOUR_USERNAME/screensaver4wayland
```

如需验证安装和卸载生命周期，安装 `bubblewrap` 后执行：

```bash
python3 scripts/check_release.py --install-smoke
```

此测试使用只读宿主根目录、独立用户/进程/网络命名空间和独立 dpkg 数据库；仅测试根目录可写，不安装到你的实际系统。测试依赖宿主已有的运行库，检查安装、配置、重装、移除、清除以及个人配置保留。它不等同于在完全干净的虚拟机上验证依赖下载。

归档采用固定时间、排序和文件权限；在相同工具版本、源码和构建选项下可重复构建。`SOURCE_DATE_EPOCH` 可覆盖默认的 changelog 时间。构建脚本只收集明确列出的源码目录，排除 `.artifacts`、`dist`、`.git`、字节码、本机图片和个人配置。

## 5. 发布下一版

同步修改应用版本、`packaging/debian/changelog`、相应的 `releases/v版本.md` 和文档中的示例，再提交并推送新的版本标签。包维护者信息取自 Debian changelog，也可以通过 `--maintainer '姓名 <邮箱>'` 显式指定。
