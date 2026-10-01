import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_docs as docs


class DocsTests(unittest.TestCase):
    def test_documentation_is_consistent(self):
        self.assertEqual(docs.problems(), [])

    def test_slug_matches_rendered_anchors(self):
        self.assertEqual(docs.slug('2. Collect the winning files'), '2-collect-the-winning-files')
        self.assertEqual(docs.slug('`[paths]`'), 'paths')
        self.assertEqual(docs.slug('`console`: Play on Xbox'), 'console-play-on-xbox')

    def test_problems_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'docs').mkdir()
            (root / 'patches').mkdir()
            (root / 'README.md').write_text('# R\n\n[a](docs/a.md#nowhere) [b](docs/b.md)\n'
                                            '`[not a link](x.md)`\n', encoding='utf-8')
            (root / 'docs' / 'a.md').write_text('# A\n\nFirst. Second.\n\n## Here\n',
                                                encoding='utf-8')
            (root / 'docs' / 'c.md').write_text('# C\n# Again\n\n```\n# not a title\n```\n',
                                                encoding='utf-8')
            (root / 'docs' / 'nav.toml').write_text(
                '[[section]]\ntitle = "S"\npages = ["a.md", "a.md", "gone.md"]\n',
                encoding='utf-8')
            found = docs.problems(root)
            self.assertIn('docs/nav.toml: a.md is listed more than once', found)
            self.assertIn('docs/nav.toml: gone.md does not exist', found)
            self.assertIn('docs/c.md: not listed in docs/nav.toml', found)
            self.assertIn('docs/c.md: 2 titles, wants one', found)
            self.assertIn('README.md: no heading for #nowhere in docs/a.md', found)
            self.assertIn('README.md: link to missing docs/b.md', found)
            self.assertFalse(any('missing x.md' in problem for problem in found))
            self.assertTrue(any('index.md' in problem for problem in found))

    def test_index_uses_the_first_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.md').write_text('# A\n\nSee [b](b.md) first. Then more.\n',
                                       encoding='utf-8')
            (root / 'nav.toml').write_text('[[section]]\ntitle = "S"\npages = ["a.md"]\n',
                                           encoding='utf-8')
            index = docs.render_index(root / 'nav.toml', root)
            self.assertIn('- [A](a.md): See b first.', index)
