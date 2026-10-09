import re
import tempfile
import unittest
from pathlib import Path

import tes3x.cli as cli
import tes3x.docs as docs


class DocsTests(unittest.TestCase):
    def test_documentation_is_consistent(self):
        self.assertEqual(docs.problems(), [])

    def test_every_command_is_listed(self):
        page = (docs.DOCS / 'commands.md').read_text(encoding='utf-8')
        package = docs.ROOT / 'src' / 'tes3x'
        missing = [name for name in cli.COMMANDS  # those with options of their own
                   if 'ArgumentParser' in (package / f'{name}.py').read_text(encoding='utf-8')
                   and f"`tes3x {name.replace('_', '-')}`" not in page]
        self.assertEqual(missing, [])

    def test_the_docs_name_commands_not_tool_scripts(self):
        # The tools/ shims go in step 7; nothing a reader follows may point at them.
        pages = [docs.ROOT / 'README.md', *sorted(docs.DOCS.glob('*.md')),
                 *sorted((docs.ROOT / 'patches').glob('*.md'))]
        found = [f'{page.name}:{number}' for page in pages
                 for number, line in enumerate(page.read_text(encoding='utf-8').splitlines(), 1)
                 if re.search(r'tools[/\\]tes3x_|tes3x_\w+\.py', line)]
        self.assertEqual(found, [])

    def test_links_from_the_code_reach_a_page_and_heading(self):
        package = docs.ROOT / 'src' / 'tes3x'
        links = {match.group(1) for path in package.glob('*.py')
                 for match in re.finditer(r'docs_url\([\'"]([^\'"]+)[\'"]\)',
                                          path.read_text(encoding='utf-8'))}
        self.assertTrue(links)
        for link in sorted(links):
            page, _, anchor = link.partition('#')
            text = (docs.DOCS / page).read_text(encoding='utf-8')
            headings = {docs.slug(line.lstrip('#').strip()) for line in text.splitlines()
                        if line.startswith('#')}
            self.assertTrue(not anchor or anchor in headings, link)

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
