import json
import unittest

from tes3x.paths import resource
from tes3x.pipeline import resolve_patch_plan
from tes3x.prebuilt_payload import select_prebuilt


class PrebuiltPayloadTests(unittest.TestCase):
    def test_every_bundled_variant_has_checked_payload_and_binding_manifest(self):
        index = json.loads(resource("payloads", "index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(index["variants"]), 7)
        for variant in index["variants"]:
            with self.subTest(variant=variant["name"]):
                name, pe, manifest = select_prebuilt(variant["sources"])
                self.assertEqual(name, variant["name"])
                self.assertTrue(pe.is_file())
                bindings = json.loads(manifest.read_text(encoding="utf-8"))["bindings"]
                self.assertIn("orig_entry", bindings)
                self.assertIn("engine_names", bindings)
                self.assertIn("required_thunks", bindings)

    def test_compile_time_flags_and_other_sources_skip_prebuilts(self):
        sources = ["tes3xhook.c", "tes3xlog.c", "tes3xini.c", "tes3xdiag.c"]
        self.assertIsNone(select_prebuilt(sources, "-DTES3X_DIAG_TEST_FAULTS"))
        self.assertIsNone(select_prebuilt(sources + ["tes3xsaves.c"]))

    def test_default_modded_profile_selects_archive_payload(self):
        plan = resolve_patch_plan({"mods": [{"name": "example"}],
                                   "patches": {"preset": "recommended"}})
        self.assertIn("multi-bsa", plan["applied"])
        self.assertEqual(select_prebuilt(plan["sources"])[0], "archives")
