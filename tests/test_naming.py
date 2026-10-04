"""連番削除ルールのテスト。pytest でも python -m unittest でも実行できる。"""

import tempfile
import unittest
from pathlib import Path

from m4a2mp3.naming import has_serial, strip_serial
from m4a2mp3.processor import OUTPUT_DIR_NAME, find_target_dirs, plan_renames

# (拡張子を除いた入力ファイル名, 期待する結果)
STRIP_CASES = [
    ("アイドル_001", "アイドル"),
    ("海のまにまに_002", "海のまにまに"),
    ("手紙_003", "手紙"),
    ("邂逅_004", "邂逅"),
    ("Brand New_001", "Brand New"),
    ("lulu__001", "lulu_"),            # 元の名前末尾の "_" は残す
    ("I LOVE____001", "I LOVE___"),
    ("木星 (feat_稲葉浩志)", "木星 (feat_稲葉浩志)"),  # 途中の "_" は変更しない
    ("Interlude _Awakening_", "Interlude _Awakening_"),
    ("拍手喝采 [Piano ver_]", "拍手喝采 [Piano ver_]"),
    ("リズム64", "リズム64"),
    ("a_01", "a_01"),                  # 2桁は対象外
    ("a_0001", "a_0001"),              # 4桁は対象外
    ("a001", "a001"),                  # "_" がなければ対象外
    ("a_001_002", "a_001"),            # 取り除くのは末尾の1つだけ
    ("a_００１", "a_００１"),          # 全角数字は対象外
    ("a_001 ", "a_001 "),              # 末尾が数字でなければ対象外
]


class StripSerialTest(unittest.TestCase):
    def test_table(self):
        for stem, expected in STRIP_CASES:
            with self.subTest(stem=stem):
                self.assertEqual(strip_serial(stem), expected)
                self.assertEqual(has_serial(stem), stem != expected)


class PlanRenamesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, *names):
        for name in names:
            (self.dir / name).write_bytes(b"")

    def plans(self):
        return {(p.src.name, p.dst.name, p.collision) for p in plan_renames(self.dir)}

    def test_basic_and_other_extensions(self):
        self.touch("アイドル_001.m4a", "祝福.m4a", "cover_001.jpg", "x_001.M4A")
        self.assertEqual(self.plans(), {
            ("アイドル_001.m4a", "アイドル.m4a", False),
            ("x_001.M4A", "x.M4A", False),
        })

    def test_collision_with_existing_file(self):
        # 実例: SUPER BEAVER「東京」フォルダ
        self.touch("東京.m4a", "東京_001.m4a")
        self.assertEqual(self.plans(), {("東京_001.m4a", "東京.m4a", True)})

    def test_collision_is_case_insensitive(self):
        self.touch("Magic.m4a", "MAGIC_001.m4a")
        self.assertEqual(self.plans(), {("MAGIC_001.m4a", "MAGIC.m4a", True)})

    def test_collision_between_planned_renames(self):
        self.touch("hana_001.m4a", "hana_002.m4a")
        self.assertEqual(self.plans(), {
            ("hana_001.m4a", "hana.m4a", False),
            ("hana_002.m4a", "hana.m4a", True),
        })

    def test_name_that_becomes_empty_is_skipped(self):
        self.touch("_001.m4a")
        self.assertEqual(self.plans(), set())


class FindTargetDirsTest(unittest.TestCase):
    def test_skips_output_dir_and_dirs_without_m4a(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("a.m4a", "album1/b.m4a", f"album1/{OUTPUT_DIR_NAME}/c.m4a", "empty/readme.txt"):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
            found = {p.relative_to(root).as_posix() for p in find_target_dirs([root], recursive=True)}
            self.assertEqual(found, {".", "album1"})
            found = {p.relative_to(root).as_posix() for p in find_target_dirs([root], recursive=False)}
            self.assertEqual(found, {"."})


if __name__ == "__main__":
    unittest.main()
