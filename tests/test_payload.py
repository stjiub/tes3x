import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tes3x.payload import (PayloadError, build_id, find_tool, link_symbol, resolve_address,
                           source_digest, source_text)

LINK_MAP = [
    ' Address         Publics by Value              Rva+Base               Lib:Object',
    ' 0001:00000000   _tes3x_entry               0000000000406053     tes3xhook.obj',
    ' 0001:00000100   @tes3x_console_hook@12     0000000000406839     tes3xconsole.obj',
]


class PayloadTests(unittest.TestCase):
    def test_unknown_image_requires_explicit_fixed_binding(self):
        name = "TES3X_TEST_BINDING"
        with patch.dict("os.environ", {name: "0x00123456"}):
            self.assertEqual(resolve_address(name, 0x1000, False), "0x00123456")
        with self.assertRaisesRegex(PayloadError, "no binding for TES3X_TEST_BINDING"):
            resolve_address(name, 0x1000, False)
        self.assertEqual(resolve_address(name, 0x1000), "0x00001000")

    def test_link_map_symbol_is_the_second_to_last_field(self):
        self.assertEqual(link_symbol(LINK_MAP, '_tes3x_entry'), '0x0000000000406053')

    def test_link_map_tries_each_spelling(self):
        self.assertEqual(link_symbol(LINK_MAP, '_tes3x_console_hook', '@tes3x_console_hook@12'),
                         '0x0000000000406839')
        with self.assertRaises(PayloadError):
            link_symbol(LINK_MAP, '_missing')

    def test_an_explicit_llvm_folder_must_hold_the_tool(self):
        with self.assertRaises(PayloadError):
            find_tool('clang', Path(__file__).parent)

    def test_section_files_are_part_of_their_source(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / 'multi').mkdir()
        main, section = folder / 'main.c', folder / 'multi' / 'part.c'
        main.write_text('int a;\n#include "multi/part.c"\nint c;\n', encoding='utf-8')
        section.write_text('int b;\n', encoding='utf-8')
        self.assertEqual(source_text(main).split(), ['int', 'a;', 'int', 'b;', 'int', 'c;'])
        before = source_digest(str(main))
        section.write_text('int b2;\n', encoding='utf-8')
        self.assertNotEqual(source_digest(str(main)), before)

    def test_source_digest_ignores_line_endings(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        main = folder / 'main.c'
        main.write_bytes(b'int a;\nint b;\n')
        before = source_digest(str(main))
        main.write_bytes(b'int a;\r\nint b;\r\n')
        self.assertEqual(source_digest(str(main)), before)

    def test_build_id_follows_the_linked_objects_and_flags(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        obj = folder / 'main.obj'
        obj.write_bytes(b'one')
        before = build_id([obj], '')
        self.assertNotEqual(build_id([obj], '-DTES3X_DIAG_TEST_FAULTS'), before)
        obj.write_bytes(b'two')
        self.assertNotEqual(build_id([obj], ''), before)


if __name__ == '__main__':
    unittest.main()
