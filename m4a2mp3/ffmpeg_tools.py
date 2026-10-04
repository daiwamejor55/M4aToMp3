"""ffmpeg / ffprobe の検出と呼び出し。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

WINGET_INSTALL_COMMAND = "winget install --id Gyan.FFmpeg -e --source winget"

# exe 化 (--windowed) したときに ffmpeg のコンソール窓が出ないようにする
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path


@dataclass
class Probe:
    title: str | None = None
    track: str | None = None
    album: str | None = None
    duration: float | None = None
    has_cover: bool = False


@dataclass
class ConvertOutcome:
    ok: bool
    cancelled: bool = False
    error: str = ""


def tools_from_path(ffmpeg_path: str | os.PathLike) -> Tools | None:
    """ffmpeg.exe のパスから、同じフォルダの ffprobe.exe と組にして返す。"""
    ffmpeg = Path(ffmpeg_path)
    ffprobe = ffmpeg.with_name("ffprobe" + ffmpeg.suffix)
    if ffmpeg.is_file() and ffprobe.is_file():
        return Tools(ffmpeg, ffprobe)
    return None


def _candidates(saved_path: str | None):
    if saved_path:
        yield Path(saved_path)
    found = shutil.which("ffmpeg")
    if found:
        yield Path(found)
    # exe と同じフォルダに置いた ffmpeg
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    yield base / "ffmpeg.exe"
    yield base / "ffmpeg" / "bin" / "ffmpeg.exe"
    # winget でインストールした場合 (PATH が反映される前でも見つけられるように)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        yield winget / "Links" / "ffmpeg.exe"
        packages = winget / "Packages"
        if packages.is_dir():
            yield from sorted(packages.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"), reverse=True)


def find_tools(saved_path: str | None = None) -> Tools | None:
    """設定 → PATH → アプリと同じ場所 → winget の順に ffmpeg を探す。"""
    for candidate in _candidates(saved_path):
        tools = tools_from_path(candidate)
        if tools:
            return tools
    return None


def probe(tools: Tools, path: Path) -> Probe | None:
    """曲名・トラック番号・アルバム・再生時間・ジャケット有無を取得する。"""
    args = [
        str(tools.ffprobe), "-v", "error",
        "-show_entries", "format=duration:format_tags:stream=codec_type",
        "-of", "json", str(path),
    ]
    try:
        r = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace",
                           creationflags=_NO_WINDOW, timeout=60)
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
    try:
        duration = float(fmt["duration"])
    except (KeyError, ValueError):
        duration = None
    has_cover = any(s.get("codec_type") == "video" for s in data.get("streams", []))
    return Probe(tags.get("title"), tags.get("track"), tags.get("album"), duration, has_cover)


def _build_args(tools: Tools, src: Path, tmp: Path, bitrate: int, cover_codec: str) -> list[str]:
    return [
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(src),
        "-map", "0:a", "-map", "0:v?", "-map_metadata", "0",
        "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
        "-c:v", cover_codec, "-disposition:v", "attached_pic",
        "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)",
        "-id3v2_version", "3", "-write_id3v1", "1",
        "-f", "mp3", str(tmp),
    ]


def _run_ffmpeg(args: list[str], cancel: threading.Event) -> tuple[int | None, str]:
    """ffmpeg を実行する。中止されたら強制終了して (None, "") を返す。"""
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    while True:
        try:
            _, err = proc.communicate(timeout=0.2)
            return proc.returncode, (err or "").strip()
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                proc.kill()
                proc.communicate()
                return None, ""


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def convert(tools: Tools, src: Path, dst: Path, bitrate: int, cancel: threading.Event) -> ConvertOutcome:
    """src を MP3 に変換して dst に保存する。

    一時ファイルに書き出してから置き換えるので、失敗・中止時に作りかけの .mp3 は残らない。
    ジャケットのコピーに失敗したら JPEG に変換して再試行する。
    """
    tmp = dst.with_name(dst.name + ".part")
    errors = []
    for cover_codec in ("copy", "mjpeg"):
        code, err = _run_ffmpeg(_build_args(tools, src, tmp, bitrate, cover_codec), cancel)
        if code is None:
            _remove(tmp)
            return ConvertOutcome(ok=False, cancelled=True)
        if code == 0:
            try:
                os.replace(tmp, dst)
            except OSError as e:
                _remove(tmp)
                return ConvertOutcome(ok=False, error=f"保存に失敗しました: {e}")
            return ConvertOutcome(ok=True)
        errors.append(f"[-c:v {cover_codec}] {err or f'終了コード {code}'}")
        _remove(tmp)
    return ConvertOutcome(ok=False, error=" / ".join(errors))
