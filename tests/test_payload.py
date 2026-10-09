import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_payload import PayloadError, build_id, find_tool, link_symbol, source_text

LINK_MAP = [
    ' Address         Publics by Value              Rva+Base               Lib:Object',
    ' 0001:00000000   _tes3x_entry               0000000000406053     tes3xhook.obj',
    ' 0001:00000100   @tes3x_console_hook@12     0000000000406839     tes3xconsole.obj',
]


class PayloadTests(unittest.TestCase):
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
        before = build_id([str(main)], '')
        section.write_text('int b2;\n', encoding='utf-8')
        self.assertNotEqual(build_id([str(main)], ''), before)

    def test_generated_headers_are_part_of_the_build_id(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        main, thunks = folder / 'main.c', folder / 'tes3x_thunks.h'
        main.write_text('int a;\n', encoding='utf-8')
        thunks.write_text('#define THUNK_A 0x1\n', encoding='utf-8')
        before = build_id([str(main)], '', [thunks])
        thunks.write_text('#define THUNK_A 0x2\n', encoding='utf-8')
        self.assertNotEqual(build_id([str(main)], '', [thunks]), before)


if __name__ == '__main__':
    unittest.main()
