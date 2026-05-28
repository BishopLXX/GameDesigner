from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


APP_NAME = "GameDesigner"
APP_EXE_NAME = "GameDesigner.exe"
NODE_DIST_INDEX_URL = "https://nodejs.org/dist/index.json"
AI_CLI_PACKAGES = ("@openai/codex", "@anthropic-ai/claude-code")


class InstallError(RuntimeError):
    pass


def main() -> int:
    if os.name != "nt":
        _message_box("GameDesigner 安装失败", "当前安装器只支持 Windows。", error=True)
        return 1
    try:
        installer = GameDesignerInstaller(default_install_dir())
        installer.install()
    except Exception as exc:
        text = f"安装失败：{exc}"
        print(text, flush=True)
        _message_box("GameDesigner 安装失败", text, error=True)
        return 1
    return 0


class GameDesignerInstaller:
    def __init__(self, install_dir: Path) -> None:
        self.install_dir = install_dir
        self.runtime_dir = install_dir / "runtime"
        self.node_dir = self.runtime_dir / "node"
        self.ai_cli_dir = self.runtime_dir / "ai-cli"

    def install(self) -> None:
        _log(f"安装目录：{self.install_dir}")
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._install_app_exe()
        npm_cmd = self._ensure_node_runtime()
        self._install_ai_cli_runtime(npm_cmd)
        self._write_launcher()
        self._write_runtime_marker()
        _log("安装完成，正在启动 GameDesigner。")
        subprocess.Popen([str(self.install_dir / APP_EXE_NAME)], cwd=str(self.install_dir))

    def _install_app_exe(self) -> None:
        source = bundled_app_exe_path()
        target = self.install_dir / APP_EXE_NAME
        _log(f"复制主程序：{source.name}")
        try:
            shutil.copy2(source, target)
        except PermissionError as exc:
            raise InstallError("请先关闭正在运行的 GameDesigner.exe，再重新安装。") from exc

    def _ensure_node_runtime(self) -> Path:
        npm_cmd = self.node_dir / "npm.cmd"
        node_exe = self.node_dir / "node.exe"
        if npm_cmd.is_file() and node_exe.is_file():
            _log("已存在本地 Node/npm runtime。")
            return npm_cmd
        version, file_name = latest_node_windows_zip()
        url = f"https://nodejs.org/dist/{version}/{file_name}"
        _log(f"下载 Node.js {version}：{url}")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / file_name
            download_file(url, zip_path)
            extract_root = temp_path / "node"
            extract_root.mkdir()
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(extract_root)
            extracted_dirs = [path for path in extract_root.iterdir() if path.is_dir()]
            if not extracted_dirs:
                raise InstallError("Node.js 压缩包结构异常。")
            if self.node_dir.exists():
                shutil.rmtree(self.node_dir)
            shutil.move(str(extracted_dirs[0]), str(self.node_dir))
        if not npm_cmd.is_file() or not node_exe.is_file():
            raise InstallError("Node.js runtime 安装不完整。")
        return npm_cmd

    def _install_ai_cli_runtime(self, npm_cmd: Path) -> None:
        self.ai_cli_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = self.runtime_dir / "npm-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _log("安装 Codex CLI 和 Claude Code CLI。")
        env = dict(os.environ)
        env["npm_config_cache"] = str(cache_dir)
        command = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            str(npm_cmd),
            "install",
            "--prefix",
            str(self.ai_cli_dir),
            "--no-audit",
            "--no-fund",
            *AI_CLI_PACKAGES,
        ]
        subprocess.run(command, check=True, env=env)
        missing = [name for name in ("codex", "claude") if not self._cli_command_exists(name)]
        if missing:
            raise InstallError(f"AI CLI 安装后未找到命令：{', '.join(missing)}")

    def _cli_command_exists(self, name: str) -> bool:
        bin_dir = self.ai_cli_dir / "node_modules" / ".bin"
        return any((bin_dir / f"{name}{suffix}").is_file() for suffix in ("", ".exe", ".cmd", ".bat", ".ps1"))

    def _write_launcher(self) -> None:
        launcher = self.install_dir / "GameDesigner.cmd"
        launcher.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            "set \"GAMEDESIGNER_RUNTIME_DIR=%~dp0runtime\"\r\n"
            "set \"PATH=%~dp0runtime\\node;%~dp0runtime\\ai-cli\\node_modules\\.bin;%PATH%\"\r\n"
            "start \"\" \"%~dp0GameDesigner.exe\" %*\r\n",
            encoding="ascii",
        )

    def _write_runtime_marker(self) -> None:
        marker = self.runtime_dir / "runtime.json"
        marker.write_text(
            json.dumps(
                {
                    "app": APP_NAME,
                    "node_dir": str(self.node_dir),
                    "ai_cli_dir": str(self.ai_cli_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def default_install_dir() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def bundled_app_exe_path() -> Path:
    candidates: list[Path] = []
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(Path(bundle_dir) / APP_EXE_NAME)
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / APP_EXE_NAME)
    candidates.append(Path(__file__).resolve().parents[1] / "release" / APP_EXE_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise InstallError("安装器内没有找到 GameDesigner.exe。请先重新执行 build_release.bat。")


def latest_node_windows_zip() -> tuple[str, str]:
    arch = windows_arch()
    required_file = f"win-{arch}-zip"
    with urllib.request.urlopen(NODE_DIST_INDEX_URL, timeout=60) as response:
        releases = json.loads(response.read().decode("utf-8"))
    for release in releases:
        files = release.get("files", [])
        version = str(release.get("version") or "")
        if release.get("lts") and required_file in files and version:
            return version, f"node-{version}-win-{arch}.zip"
    raise InstallError(f"没有找到适合 Windows {arch} 的 Node.js LTS 版本。")


def windows_arch() -> str:
    machine = platform.machine().lower()
    if "arm" in machine and "64" in machine:
        return "arm64"
    return "x64"


def download_file(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response:
        target.write_bytes(response.read())


def _log(message: str) -> None:
    print(f"[{APP_NAME} Setup] {message}", flush=True)


def _message_box(title: str, message: str, *, error: bool = False) -> None:
    if os.name != "nt":
        return
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, title, flags)


if __name__ == "__main__":
    raise SystemExit(main())
