import json
import unittest

import tes3x.patches as registry
from tes3x.link import CLASS_EXTERNAL, CLASS_WEAK_EXTERNAL, link, parse_object
from tes3x.payload import (ABSOLUTE, DEFAULT_SOURCES, ENTRY, OBJECTS, SOURCE_DEPENDENCIES, WORD,
                           WORDS, expand_sources, production_sources, shipped_object,
                           shipped_objects)


class BundledObjectTests(unittest.TestCase):
    def test_every_production_source_has_a_current_object(self):
        index = shipped_objects()
        stale = [source for source in production_sources()
                 if shipped_object(source, index) is None]
        self.assertEqual(stale, [], "run tools/build_payload_objects.py with a retail XBE")
        self.assertEqual(sorted(index["objects"]), sorted(production_sources()))

    def test_index_layout_names_every_engine_value_the_objects_use(self):
        layout = json.loads((OBJECTS / "index.json").read_text(encoding="utf-8"))["layout"]
        for source in production_sources():
            obj = parse_object(source, (OBJECTS / source.replace(".c", ".obj")).read_bytes())
            for symbol in obj.symbols.values():
                name = symbol.name
                if symbol.section or not name.startswith("_" + ABSOLUTE) or "THUNK_" in name:
                    continue
                value = name[len(ABSOLUTE) + 1:]
                with self.subTest(source=source, value=value):
                    base, _, element = value.rpartition("_")
                    self.assertTrue(value in layout or value == "ORIG_ENTRY" or
                                    (element.isdigit() and isinstance(layout.get(base), int)))

    def test_each_patch_links_alone_from_bundled_objects(self):
        """Every engine value is a stand-in here; what is checked is that one patch's objects
        resolve against each other without the rest."""
        for entry in registry.PATCHES:
            source = entry.get("source")
            if not source:
                continue
            sources = list(DEFAULT_SOURCES)
            for name in (*SOURCE_DEPENDENCIES.get(source, ()), source):
                if name not in sources:
                    sources.append(name)
            with self.subTest(patch=entry["name"]):
                self.link(expand_sources(sources))

    def test_minimal_payload_links(self):
        self.link(["tes3xhook.c", "tes3xlog.c", "tes3xini.c"])

    def link(self, sources):
        objects = [(source, (OBJECTS / source.replace(".c", ".obj")).read_bytes())
                   for source in sources]
        wanted = set()
        for name, data in objects:
            for symbol in parse_object(name, data).symbols.values():
                if symbol.section == 0 and symbol.storage in (CLASS_EXTERNAL,
                                                              CLASS_WEAK_EXTERNAL) \
                        and symbol.name.startswith("_" + ABSOLUTE):
                    wanted.add(symbol.name)
        absolute = {name: 0x10000 + i * 0x10 for i, name in enumerate(sorted(wanted))}
        words = {f"_{WORD}{name}": 1 for name in WORDS}
        linked = link(objects, 0x405000, ENTRY, absolute, words)
        self.assertGreater(linked.vsize, 0)


if __name__ == "__main__":
    unittest.main()
