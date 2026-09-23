import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_payload import PayloadError, find_tool, link_symbol

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


if __name__ == '__main__':
    unittest.main()
