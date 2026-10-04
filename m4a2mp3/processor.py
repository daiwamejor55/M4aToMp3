"""フォルダ単位の連番削除と MP3 変換。GUI から独立して動く。"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import ffmpeg_tools as ff
from .naming import strip_serial

OUTPUT_DIR_NAME = "変換"

# ログの種類: INFO / OK / RENAMED / SKIP / WARN / FAIL
LogFn = Callable[[str, str], None]
ProgressFn = Callable[[int, int], None]


@dataclass
class Options:
    recursive: bool = True
    rename: bool = True
    convert: bool = True
    bitrate: int = 320
    overwrite: bool = False


@dataclass
class RenamePlan:
    src: Path
    dst: Path
    collision: bool


@dataclass
class FolderResult:
    folder: Path
    renamed: int = 0
    converted: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: int = 0
    issues: list[str] = field(default_factory=list)


def is_m4a(path: Path) -> bool:
    return path.suffix.lower() == ".m4a" and path.is_file()


def list_m4a(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if is_m4a(p)), key=lambda p: p.name)


def find_target_dirs(roots: Iterable[Path], recursive: bool) -> list[Path]:
    """.m4a を含むフォルダを列挙する。出力先の「変換」フォルダは除外する。"""
    result: list[Path] = []
    seen: set[str] = set()

    def add(folder: Path) -> None:
        key = os.path.normcase(str(folder.resolve()))
        if key not in seen:
            seen.add(key)
            result.append(folder)

    for root in roots:
        root = Path(root)
        if not root.is_dir() or root.name == OUTPUT_DIR_NAME:
            continue
        if recursive:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d != OUTPUT_DIR_NAME)
                if any(f.lower().endswith(".m4a") for f in filenames):
                    add(Path(dirpath))
        elif any(is_m4a(p) for p in root.iterdir()):
            add(root)
    return result


def plan_renames(folder: Path) -> list[RenamePlan]:
    """連番を削除するリネームの計画を立てる。

    変更後の名前がすでに存在する (または先に計画したリネームで使われる) 場合は collision=True。
    Windows ではファイル名の大文字小文字を区別しないので、小文字にして比較する。
    """
    taken = {p.name.lower() for p in folder.iterdir()}
    plans = []
    for src in list_m4a(folder):
        new_stem = strip_serial(src.stem)
        if new_stem == src.stem or not new_stem.strip():
            continue
        dst = src.with_name(new_stem + src.suffix)
        collision = dst.name.lower() in taken
        if not collision:
            taken.discard(src.name.lower())
            taken.add(dst.name.lower())
        plans.append(RenamePlan(src, dst, collision))
    return plans


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def format_size(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


class Processor:
    def __init__(self, tools: ff.Tools | None, options: Options, log: LogFn,
                 progress: ProgressFn, cancel: threading.Event):
        self.tools = tools
        self.options = options
        self.log = log
        self.progress = progress
        self.cancel = cancel
        self._done = 0
        self._total = 0

    # ---- 公開 API ----

    def run(self, roots: Iterable[Path], dry_run: bool = False) -> list[FolderResult]:
        if self.options.convert and self.tools is None:
            raise RuntimeError("ffmpeg が見つからないため変換できません。")

        roots = [Path(r) for r in roots]
        for root in roots:
            if not root.is_dir():
                self.log("FAIL", f"フォルダが見つかりません: {root}")

        dirs = find_target_dirs(roots, self.options.recursive)
        if not dirs:
            self.log("INFO", ".m4a ファイルを含むフォルダが見つかりませんでした。")
            return []

        if self.options.convert:
            self._total = sum(len(list_m4a(d)) for d in dirs)
        elif self.options.rename:
            self._total = sum(len(plan_renames(d)) for d in dirs)
        self._done = 0
        self.progress(0, self._total)

        results = []
        for folder in dirs:
            if self.cancel.is_set():
                break
            result = FolderResult(folder)
            results.append(result)
            self.log("INFO", f"=== {folder}")
            renamed_to: dict[Path, Path] = {}
            if self.options.rename:
                renamed_to = self._rename_folder(folder, result, dry_run)
            if self.options.convert and not self.cancel.is_set():
                self._convert_folder(folder, result, dry_run, renamed_to)
        return results

    # ---- 連番削除 ----

    def _rename_folder(self, folder: Path, result: FolderResult, dry_run: bool) -> dict[Path, Path]:
        """リネームを行い、{元のパス: 新しいパス} を返す (ドライランでは予定)。"""
        renamed: dict[Path, Path] = {}
        for plan in plan_renames(folder):
            if self.cancel.is_set():
                break
            if plan.collision:
                result.skipped += 1
                self.log("SKIP", f"SKIP (exists): {plan.src.name} → {plan.dst.name}")
                detail = self._compare(plan.src, plan.dst)
                for line in detail:
                    self.log("SKIP", "    " + line)
                result.issues.append(
                    f"名前の衝突: {plan.src.name} → {plan.dst.name} は既に存在するためリネームしていません\n"
                    + "\n".join("    " + line for line in detail))
            elif dry_run:
                result.renamed += 1
                renamed[plan.src] = plan.dst
                self.log("RENAMED", f"[予定] {plan.src.name} → {plan.dst.name}")
            else:
                try:
                    plan.src.rename(plan.dst)
                except OSError as e:
                    result.failed += 1
                    self.log("FAIL", f"リネーム失敗: {plan.src.name} → {plan.dst.name} ({e})")
                    result.issues.append(f"リネーム失敗: {plan.src.name} ({e})")
                else:
                    result.renamed += 1
                    renamed[plan.src] = plan.dst
                    self.log("RENAMED", f"{plan.src.name} → {plan.dst.name}")
            if not self.options.convert:
                self._step()
        return renamed

    def _compare(self, a: Path, b: Path) -> list[str]:
        """名前が衝突した2つのファイルを比較する説明を作る。"""
        lines = []
        for label, path in (("リネーム対象", a), ("既存ファイル", b)):
            info = ff.probe(self.tools, path) if self.tools else None
            size = format_size(path.stat().st_size)
            if info:
                lines.append(f"{label}: {path.name} | 曲名={info.title or '?'} / トラック={info.track or '?'}"
                             f" / アルバム={info.album or '?'} / 時間={format_duration(info.duration)} / {size}")
            else:
                lines.append(f"{label}: {path.name} | {size}")
        return lines

    # ---- MP3 変換 ----

    def _convert_folder(self, folder: Path, result: FolderResult, dry_run: bool,
                        renamed_to: dict[Path, Path]) -> None:
        out_dir = folder / OUTPUT_DIR_NAME
        if dry_run:
            sources = [renamed_to.get(p, p) for p in list_m4a(folder)]
            sources.sort(key=lambda p: p.name)
        else:
            sources = list_m4a(folder)

        for src in sources:
            if self.cancel.is_set():
                return
            dst = out_dir / (src.stem + ".mp3")
            exists = dst.exists()
            if exists and not self.options.overwrite:
                result.skipped += 1
                self.log("SKIP", f"SKIP (exists): {OUTPUT_DIR_NAME}\\{dst.name}")
            elif dry_run:
                result.converted += 1
                self.log("OK", f"[予定] {src.name} → {OUTPUT_DIR_NAME}\\{dst.name}" + (" (上書き)" if exists else ""))
            else:
                if not self._convert_one(src, dst, out_dir, result):
                    return
            self._step()

    def _convert_one(self, src: Path, dst: Path, out_dir: Path, result: FolderResult) -> bool:
        """1ファイル変換する。中止されたら False。"""
        try:
            out_dir.mkdir(exist_ok=True)
        except OSError as e:
            result.failed += 1
            self.log("FAIL", f"{src.name}: 出力フォルダを作成できません ({e})")
            result.issues.append(f"変換失敗: {src.name} (出力フォルダを作成できません)")
            return True

        outcome = ff.convert(self.tools, src, dst, self.options.bitrate, self.cancel)
        if outcome.cancelled:
            self.log("SKIP", f"中止しました: {src.name} (作りかけのファイルは削除済み)")
            return False
        if not outcome.ok:
            result.failed += 1
            self.log("FAIL", f"{src.name}: {outcome.error}")
            result.issues.append(f"変換失敗: {src.name} ({outcome.error})")
            return True

        result.converted += 1
        self.log("OK", f"{src.name} → {OUTPUT_DIR_NAME}\\{dst.name}")
        info = ff.probe(self.tools, dst)
        missing = []
        if info is None or not info.has_cover:
            missing.append("NO COVER")
        if info is None or not info.title:
            missing.append("NO TITLE")
        if missing:
            result.warnings += 1
            self.log("WARN", f"{' / '.join(missing)}: {dst.name}")
            result.issues.append(f"{' / '.join(missing)}: {dst.name}")
        return True

    def _step(self) -> None:
        self._done += 1
        self.progress(self._done, self._total)


def format_summary(results: list[FolderResult], dry_run: bool, cancelled: bool) -> str:
    verb = "予定" if dry_run else ""
    lines = ["", "===== 集計" + (" (実行前の確認)" if dry_run else "") + (" ※中止しました" if cancelled else "") + " ====="]
    for r in results:
        lines.append(f"{r.folder}")
        lines.append(f"    リネーム{verb} {r.renamed} / 変換{verb} {r.converted} / スキップ {r.skipped}"
                     f" / 失敗 {r.failed} / 警告 {r.warnings}")
    total = FolderResult(Path())
    for r in results:
        total.renamed += r.renamed
        total.converted += r.converted
        total.skipped += r.skipped
        total.failed += r.failed
        total.warnings += r.warnings
    lines.append(f"合計: {len(results)} フォルダ / リネーム{verb} {total.renamed} / 変換{verb} {total.converted}"
                 f" / スキップ {total.skipped} / 失敗 {total.failed} / 警告 {total.warnings}")
    issues = [(r.folder, i) for r in results for i in r.issues]
    if issues:
        lines.append("")
        lines.append("----- 要確認 -----")
        for folder, issue in issues:
            lines.append(f"[{folder}]")
            lines.append("  " + issue)
    return "\n".join(lines)
