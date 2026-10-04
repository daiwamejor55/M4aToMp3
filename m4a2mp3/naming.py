"""ファイル名末尾の連番 (_001 など) を扱う。"""

import re

# 末尾の "_" + 半角数字3桁。全角数字は対象外にするため \d ではなく [0-9] を使う。
SERIAL_RE = re.compile(r"_[0-9]{3}\Z")


def has_serial(stem: str) -> bool:
    """拡張子を除いたファイル名の末尾に連番が付いているか。"""
    return SERIAL_RE.search(stem) is not None


def strip_serial(stem: str) -> str:
    """末尾の連番を1つだけ取り除いた名前を返す。連番がなければそのまま返す。

    例: "アイドル_001" -> "アイドル", "lulu__001" -> "lulu_"
    """
    return SERIAL_RE.sub("", stem, count=1)
