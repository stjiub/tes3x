import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tes3x.inject import load_pe
from tes3x.link import (REL_DIR32, REL_REL32, LinkError, link, parse_object, write_map,
                        write_pe)
from tes3x.payload import PayloadError, find_tool, link_symbol

TEXT = 0x60500020  # code, align 16, execute, read
DATA = 0xC0300040  # initialized data, align 4, read, write
RDATA_COMDAT = 0x40301040  # read-only data, COMDAT, align 4
DRECTVE = 0x00100A00  # info, remove, align 1
EXTERNAL, STATIC, WEAK = 2, 3, 105


def coff(sections, symbols):
    """An i386 COFF object. sections: (name, flags, data, [(offset, symbol, type)]);
    symbols: (name, value, section, storage, aux bytes)."""
    strings = bytearray(4)

    def name_field(name):
        raw = name.encode()
        if len(raw) <= 8:
            return raw.ljust(8, b"\0")
        offset = len(strings)
        strings.extend(raw + b"\0")
        return b"\0\0\0\0" + struct.pack("<I", offset)

    indices, count = {}, 0
    for symbol in symbols:
        indices[symbol[0]] = count
        count += 1 + len(symbol[4]) // 18
    header_size = 20 + 40 * len(sections)
    body = bytearray()
    table = bytearray()
    for name, flags, data, relocs in sections:
        raw_at = header_size + len(body)
        body.extend(data)
        rel_at = header_size + len(body)
        for offset, symbol, kind in relocs:
            body.extend(struct.pack("<IIH", offset, indices[symbol], kind))
        section_name = name.encode().ljust(8, b"\0") if len(name) <= 8 else \
            ("/%d" % len(strings)).encode().ljust(8, b"\0")
        if len(name) > 8:
            strings.extend(name.encode() + b"\0")
        table.extend(section_name + struct.pack("<IIIIIIHHI", 0, 0, len(data), raw_at,
                                                rel_at if relocs else 0, 0, len(relocs), 0,
                                                flags))
    symtab = bytearray()
    for name, value, section, storage, aux in symbols:
        symtab.extend(name_field(name) + struct.pack("<IhHBB", value, section, 0, storage,
                                                     len(aux) // 18) + aux)
    struct.pack_into("<I", strings, 0, len(strings))
    head = struct.pack("<HHIIIHH", 0x14C, len(sections), 0, header_size + len(body), count, 0, 0)
    return bytes(head + table + body + symtab + strings)


def section_aux(length, selection=0, number=0):
    return struct.pack("<IHHIHB3x", length, 0, 0, 0, number, selection)


def weak_aux(tag):
    return struct.pack("<II10x", tag, 3)


def caller():
    """_entry: call _callee; mov eax, [_data]; mov eax, ABS; mov eax, [_opt ref]; ret"""
    code = (b"\xE8\0\0\0\0" b"\xA1\0\0\0\0" b"\xB8\x06\0\0\0" b"\xB8\0\0\0\0" b"\xB8\0\0\0\0"
            b"\xC3")
    return coff([(".text", TEXT, code, [(1, "_callee", REL_REL32), (6, "_data", REL_DIR32),
                                         (11, "_tes3x_abs_X", REL_DIR32),
                                         (16, "_opt", REL_DIR32), (21, "_word", REL_DIR32)])],
                [(".text", 0, 1, STATIC, section_aux(len(code))),
                 ("_entry", 0, 1, EXTERNAL, b""),
                 ("_callee", 0, 0, EXTERNAL, b""), ("_data", 0, 0, EXTERNAL, b""),
                 ("_tes3x_abs_X", 0, 0, EXTERNAL, b""), ("_word", 0, 0, EXTERNAL, b""),
                 ("_opt", 0, 0, WEAK, weak_aux(9)),
                 (".weak._opt.default", 0, -1, EXTERNAL, b"")])


def callee():
    return coff([(".text", TEXT, b"\x90\xC3", []), (".data", DATA, b"\x11\x22\x33\x44", [])],
                [(".text", 0, 1, STATIC, section_aux(2)), ("_callee", 1, 1, EXTERNAL, b""),
                 (".data", 0, 2, STATIC, section_aux(4)), ("_data", 0, 2, EXTERNAL, b"")])


def comdat(name, string, keep=False):
    """An object with a string COMDAT and, when keep, code that references it."""
    sections = [(".rdata", RDATA_COMDAT, string, [])]
    symbols = [(".rdata", 0, 1, STATIC, section_aux(len(string), selection=2)),
               ("??_C@" + name, 0, 1, EXTERNAL, b"")]
    if keep:
        sections.append((".text", TEXT, b"\xB8\0\0\0\0\xC3", [(1, "??_C@" + name, REL_DIR32)]))
        symbols += [(".text", 0, 2, STATIC, section_aux(6)), ("_use_" + name, 0, 2, EXTERNAL,
                                                              b"")]
    return coff(sections, symbols)


class LinkTests(unittest.TestCase):
    base = 0x500000

    def linked(self, objects, **kw):
        kw.setdefault("absolute", {"_tes3x_abs_X": 0x123450})
        kw.setdefault("words", {"_word": 0xCAFE})
        return link([(f"o{i}.obj", data) for i, data in enumerate(objects)], self.base,
                    "_entry", **kw)

    def word(self, linked, va):
        return struct.unpack_from("<I", linked.image, va - self.base)[0]

    def test_relocations_absolute_values_weak_default_and_words(self):
        linked = self.linked([caller(), callee()])
        entry, symbols = linked.entry, linked.symbols
        self.assertEqual(entry, symbols["_entry"])
        self.assertEqual(self.word(linked, entry + 1), symbols["_callee"] - (entry + 5))
        self.assertEqual(self.word(linked, entry + 6), symbols["_data"])
        self.assertEqual(self.word(linked, symbols["_data"]), 0x44332211)
        self.assertEqual(self.word(linked, entry + 11), 0x123456)
        self.assertEqual(self.word(linked, entry + 16), 0)
        self.assertEqual(self.word(linked, self.word(linked, entry + 21)), 0xCAFE)

    def test_weak_reference_takes_a_definition_when_present(self):
        definition = coff([(".text", TEXT, b"\xC3", [])],
                          [(".text", 0, 1, STATIC, section_aux(1)), ("_opt", 0, 1, EXTERNAL, b"")])
        linked = self.linked([caller(), callee(), definition])
        self.assertEqual(self.word(linked, linked.entry + 16), linked.symbols["_opt"])

    def test_undefined_symbols_are_named(self):
        with self.assertRaisesRegex(LinkError, "_tes3x_abs_X"):
            self.linked([caller(), callee()], absolute={})
        with self.assertRaisesRegex(LinkError, "_callee"):
            self.linked([caller()])

    def test_duplicate_definitions_are_refused(self):
        with self.assertRaisesRegex(LinkError, "duplicate symbol _callee"):
            self.linked([caller(), callee(), callee()])

    def test_comdat_keeps_one_referenced_copy_and_drops_unreferenced(self):
        linked = self.linked([caller(), callee(), comdat("a", b"one\0", keep=True),
                              comdat("a", b"one\0"), comdat("b", b"unused\0")])
        strings = [s for s in linked.sections if s.name == ".rdata"]
        self.assertEqual(len(strings), 1)
        self.assertNotIn("??_C@b", linked.symbols)
        use = linked.symbols["_use_a"]
        self.assertEqual(self.word(linked, use + 1), linked.symbols["??_C@a"])

    def test_include_directive_keeps_a_comdat(self):
        directive = coff([(".drectve", DRECTVE, b" /INCLUDE:??_C@b", [])], [])
        linked = self.linked([caller(), callee(), comdat("b", b"kept\0"), directive])
        self.assertIn("??_C@b", linked.symbols)

    def test_unknown_directive_is_refused(self):
        directive = coff([(".drectve", DRECTVE, b" /DEFAULTLIB:libc", [])], [])
        with self.assertRaisesRegex(LinkError, "DEFAULTLIB"):
            parse_object("d.obj", directive)

    def test_pe_and_map_round_trip(self):
        linked = self.linked([caller(), callee()])
        with tempfile.TemporaryDirectory() as folder:
            pe, map_path = Path(folder, "p.pe"), Path(folder, "p.map")
            write_pe(linked, pe)
            write_map(linked, map_path)
            blob, vsize, entry_rva, base = load_pe(pe, base=self.base)
            self.assertEqual((base, vsize, entry_rva), (self.base, linked.vsize,
                                                        linked.entry - self.base))
            self.assertEqual(blob[:len(linked.image)], linked.image)
            with self.assertRaisesRegex(ValueError, "link it again"):
                load_pe(pe, base=self.base + 0x1000)
            lines = map_path.read_text().splitlines()
            self.assertEqual(int(link_symbol(lines, "_callee"), 16), linked.symbols["_callee"])


class ClangLinkTests(unittest.TestCase):
    """What clang emits for the payload's patterns links to the expected values."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.clang = find_tool("clang")
        except PayloadError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def test_weak_absolute_and_inline_asm_references(self):
        source = (
            '__attribute__((weak)) int optional(int);\n'
            'extern const char tes3x_abs_ENGINE[];\n'
            '#define ENGINE ((unsigned long)tes3x_abs_ENGINE)\n'
            'static const unsigned long sites[] = {ENGINE + 6};\n'
            'unsigned long probe(void) { return optional ? 1 : sites[0]; }\n'
            'int engine_call(int x) { return ((int (*)(int))ENGINE)(x); }\n'
            '__attribute__((naked)) void entry(void)\n'
            '{ __asm__ volatile("pushl $_tes3x_abs_ENGINE\\n\\tret\\n\\t"); }\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "t.c")
            path.write_text(source)
            obj = Path(folder, "t.obj")
            subprocess.run([self.clang, "-target", "i386-pc-win32", "-Os", "-ffreestanding",
                            "-c", str(path), "-o", str(obj)], check=True, capture_output=True)
            base, engine = 0x600000, 0x00012340
            linked = link([("t.obj", obj.read_bytes())], base, "_entry",
                          {"_tes3x_abs_ENGINE": engine})
        image = linked.image
        entry = linked.entry - base
        self.assertEqual(image[entry], 0x68)
        self.assertEqual(struct.unpack_from("<I", image, entry + 1)[0], engine)
        # a call or, at -Os, a tail jump
        start = linked.symbols["_engine_call"] - base
        call = min(i for i in (image.find(b"\xE8", start), image.find(b"\xE9", start)) if i >= 0)
        self.assertEqual(struct.unpack_from("<i", image, call + 1)[0] + base + call + 5, engine)


if __name__ == "__main__":
    unittest.main()
