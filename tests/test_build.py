"""Tests for build.py. Run with `python3 -m unittest discover tests`.

They build from copies of the real src/site.json and src/log.json, changed
where a test needs it, so they never write to public/.
"""

import base64
import copy
import datetime
import hashlib
import json
import re
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
from fixtures import LOG  # noqa: E402

SITE = json.loads((build.SRC / 'site.json').read_text(encoding='utf-8'))
SITE_SCHEMA = json.loads((build.SRC / 'site.schema.json').read_text(encoding='utf-8'))
LOG_SCHEMA = json.loads((build.SRC / 'log.schema.json').read_text(encoding='utf-8'))


def site(change=None, log_change=None):
    s, l = copy.deepcopy(SITE), copy.deepcopy(LOG)
    if change:
        change(s)
    if log_change:
        log_change(l)
    # The sample log uses no photos, so the real ones in public/images/log/ are no concern here.
    with mock.patch.object(build, 'LOG_IMAGES', build.PUBLIC / 'images' / 'no-such-folder'):
        return build.Site(s, l)


class Helpers(unittest.TestCase):
    def test_years(self):
        self.assertEqual(build.years('2025-10', None), '2025 – Now')
        self.assertEqual(build.years('2020-05', '2020-11'), '2020')
        self.assertEqual(build.years('2021-03', '2022-07'), '2021 – 2022')

    def test_years_html(self):
        self.assertEqual(build.years_html('2025-10', None), '<time datetime="2025-10">2025</time> – Now')
        self.assertEqual(build.years_html('2020-05', '2020-11'), '<time datetime="2020-05">2020</time>')
        self.assertEqual(build.years_html('2021-03', '2022-07'), '<time datetime="2021-03">2021</time> – <time datetime="2022-07">2022</time>')

    def test_dates_long(self):
        self.assertEqual(build.dates_long('2025-10', None), 'October 2025 to present')
        self.assertEqual(build.dates_long('2013-02', '2013-12'), 'February 2013 to December 2013')

    def test_join_and(self):
        self.assertEqual(build.join_and(['A']), 'A')
        self.assertEqual(build.join_and(['A', 'B']), 'A and B')
        self.assertEqual(build.join_and(['A', 'B', 'C']), 'A, B and C')

    def test_links_in_text(self):
        s = 'Built [Aureo](https://aureo.audio) & more <3'
        self.assertEqual(build.html(s), 'Built <a href="https://aureo.audio">Aureo</a> &amp; more &lt;3')
        self.assertEqual(build.plain(s), 'Built Aureo & more <3')
        self.assertEqual(build.with_urls(s), 'Built Aureo (https://aureo.audio) & more <3')

    def test_attr_escapes_quotes(self):
        self.assertEqual(build.attr('a "b" & c'), 'a &quot;b&quot; &amp; c')


class Templates(unittest.TestCase):
    def test_value_alone_on_its_line_is_indented(self):
        self.assertEqual(build.render('<ul>\n  {{items}}\n</ul>\n', {'items': '<li>a</li>\n<li>b</li>'}),
                         '<ul>\n  <li>a</li>\n  <li>b</li>\n</ul>\n')

    def test_blank_lines_in_a_value_stay_blank(self):
        self.assertEqual(build.render('  {{x}}\n', {'x': 'a\n\nb'}), '  a\n\n  b\n')

    def test_inline_value_is_not_indented(self):
        self.assertEqual(build.render('<p>{{x}}</p>', {'x': 'a\nb'}), '<p>a\nb</p>')

    def test_comment_alone_on_its_line_takes_the_line(self):
        self.assertEqual(build.render('a\n  {{! gone }}\nb\n', {}), 'a\nb\n')

    def test_missing_value_fails(self):
        with self.assertRaises(build.BuildError):
            build.render('{{nope}}', {})

    def test_footer_partial(self):
        out = build.partial('footer', {'name': 'N', 'location': 'L', 'year': 2031})
        self.assertIn('<time datetime="2031">2031</time>', out)
        self.assertIn('<span class="p-locality">L</span>', out)


class Schema(unittest.TestCase):
    def check(self, change=None, log_change=None):
        s, l = copy.deepcopy(SITE), copy.deepcopy(LOG)
        if change:
            change(s)
        if log_change:
            log_change(l)
        build.validate(s, SITE_SCHEMA, SITE_SCHEMA, 'site')
        build.validate(l, LOG_SCHEMA, LOG_SCHEMA, 'log')

    def test_real_data_passes(self):
        self.check()

    def test_rejects(self):
        cases = {
            'an unknown key': lambda s: s['roles'][0].update(titel='x'),
            'a missing key': lambda s: s['roles'][0].pop('title'),
            'a bad month': lambda s: s['roles'][0].update(start='2025-13'),
            'a bare date for updated': lambda s: s.update(updated='2026-10-15'),
            'a string year': lambda s: s['education'][0].update(start='2005'),
            'a boolean as a year': lambda s: s['education'][0].update(start=True),
            'an http org url': lambda s: s['organizations']['time'].update(url='http://time.com'),
            'a guessed QID': lambda s: s['organizations']['time'].update(wikidata='43297'),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(build.BuildError):
                self.check(change)

    def test_log_shapes(self):
        add = lambda entry: lambda l: l['entries'].insert(0, entry)
        self.check(log_change=add({'kind': 'quote', 'date': '2026-10-24', 'quote': 'Q', 'who': 'W'}))
        self.check(log_change=add({'kind': 'note', 'date': '2026-10-24', 'text': 'T'}))
        for bad in [
            {'kind': 'quote', 'date': '2026-10-24', 'quote': 'Q'},
            {'kind': 'note', 'date': '2026-10-24', 'text': 'T', 'title': 'x'},
            {'kind': 'podcast', 'date': '2026-10-24', 'title': 'x', 'text': 'T'},
            {'kind': 'site', 'date': '24 October', 'title': 'x', 'text': 'T'},
        ]:
            with self.subTest(bad), self.assertRaises(build.BuildError):
                self.check(log_change=add(bad))


class CrossChecks(unittest.TestCase):
    def test_rejects(self):
        cases = {
            'an unknown organization': lambda s: s['roles'][0].update(orgs=['nope']),
            'an unknown product': lambda s: s['roles'][1].update(built='nope'),
            'roles out of order': lambda s: s['roles'].reverse(),
            'an end before the start': lambda s: s['roles'][2].update(end='2020-01'),
            'no current role': lambda s: [r.update(end='2026-01') for r in s['roles'][:2]],
            'a brand with no CSS rule': lambda s: s['organizations']['time'].update(brand='vermilion'),
            'a semicolon in the copy': lambda s: s['profile'].update(bio='Teams; things.'),
            'a middle dot in the copy': lambda s: s['roles'][0].update(resume='A · B'),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(build.BuildError):
                site(change)

    def test_log_out_of_order(self):
        with self.assertRaises(build.BuildError):
            site(log_change=lambda l: l['entries'].append({'kind': 'note', 'date': '2099-01-01', 'text': 'x'}))


class Pages(unittest.TestCase):
    def test_headline_is_the_newest_current_role(self):
        s = site()
        self.assertEqual(s.headline, 'SVP, Engineering at The Atlantic')
        self.assertEqual(s.title, 'Jeffrey Louden, SVP, Engineering at The Atlantic')

    def test_titles_at_one_org_stack_in_one_row(self):
        def promote(s):
            s['roles'][0]['end'] = '2026-09'
            s['roles'].insert(0, {'orgs': ['atlantic'], 'title': 'CTO', 'start': '2026-09', 'end': None})
        s = site(promote)
        page = s.index_html()
        self.assertEqual(page.count('data-brand="atlantic">The Atlantic</a>'), 1)
        self.assertRegex(page, r'<p class="title">CTO</p>\n\s+<p class="title">SVP, Engineering</p>')
        self.assertIn('<span class="years"><time datetime="2025-10">2025</time> – Now</span>', page)
        self.assertEqual(s.headline, 'CTO at The Atlantic')
        txt = s.index_txt()
        self.assertIn('  2025 – Now    The Atlantic                      CTO\n' + ' ' * 50 + 'SVP, Engineering\n', txt)

    def test_roles_before_2020_share_the_earlier_row(self):
        page = site().index_html()
        earlier = page[page.index('<span class="years">Earlier</span>'):]
        earlier = earlier[:earlier.index('</ul>')]
        for name in ['The New York Times', 'Nest', 'Google', 'The New Yorker', 'Obama for America']:
            self.assertIn(f'>{name}</a>', earlier)
        self.assertNotIn('Lyft', earlier)

    def test_terminal_page_stays_under_80_columns(self):
        long_title = lambda s: s['roles'][3].update(title='Engineering Manager II, Memberships, Growth, Platform')
        txt = site(long_title).index_txt()
        self.assertTrue(all(len(line) <= 80 for line in txt.splitlines()))
        self.assertIn('Lyft                              Engineering Manager II\n', txt)

    def test_earlier_wraps_between_names(self):
        txt = site().index_txt()
        self.assertIn('  Earlier       The New York Times, Nest and Google, The New Yorker,\n                Obama for America\n', txt)

    def test_log_entries(self):
        def add(l):
            l['entries'][:0] = [
                {'kind': 'quote', 'date': '2026-10-25', 'quote': 'Make it work.', 'who': 'Kent Beck', 'text': 'Still true.'},
                {'kind': 'note', 'date': '2026-10-24', 'text': 'A [link](https://example.com) & more.'},
                {'kind': 'book', 'date': '2026-10-24', 'title': 'No link', 'text': 'Fine.'},
            ]
        s = site(log_change=add)
        page = s.log_html()
        self.assertIn('<li data-kind="quote" data-date="2026-10-25">\n        <blockquote><p>“Make it work.”</p></blockquote>\n        <p class="who">Kent Beck</p>\n        <p>Still true.</p>\n      </li>', page)
        self.assertIn('<p>A <a href="https://example.com">link</a> &amp; more.</p>', page)
        self.assertIn('<p class="what">No link</p>', page)
        self.assertIn('<lastmod>2026-10-25</lastmod>', s.sitemap_xml())
        self.assertIn('Last update:    2026-10-25', s.humans_txt())

    def test_removing_linkedin_removes_it_everywhere(self):
        s = site(lambda s: s.update(links=[l for l in s['links'] if not l['removeAtExit']]))
        for name, text in [('index.html', s.index_html()), ('index.md', s.index_md()), ('index.txt', s.index_txt()),
                           ('llms.txt', s.llms_txt()), ('humans.txt', s.humans_txt())]:
            with self.subTest(name):
                self.assertNotIn('linkedin.com', text)

    def test_jsonld(self):
        graph = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', site().index_html(), re.S).group(1))['@graph']
        person = next(n for n in graph if n['@type'] == 'Person')
        self.assertEqual(person['worksFor'], [{'@id': 'https://jefflouden.com/#org-atlantic'}, {'@id': 'https://existential.engineering/#organization'}])
        self.assertEqual([o['name'] for o in person['alumniOf']], ['TIME', 'Lyft', 'The New York Times', 'Nest', 'Google', 'The New Yorker', 'Obama for America'])
        self.assertIn('https://www.wikidata.org/wiki/Q1542536', next(n for n in graph if n.get('name') == 'The Atlantic')['sameAs'])
        profile = next(n for n in graph if n['@type'] == 'ProfilePage')
        datetime.datetime.fromisoformat(profile['dateModified'])
        self.assertEqual(profile['name'], site().title)

    def test_vcard(self):
        card = site().vcard()
        self.assertTrue(card.endswith('END:VCARD\r\n'))
        self.assertNotIn('\n', card.replace('\r\n', ''))
        self.assertTrue(all(len(line) <= 75 for line in card.split('\r\n')))
        self.assertIn('TITLE:SVP\\, Engineering\r\n', card)
        photo = re.search(r'PHOTO;ENCODING=b;TYPE=JPEG:(.*?)\r\nEND', card, re.S).group(1).replace('\r\n ', '')
        self.assertEqual(base64.b64decode(photo), (build.SRC / 'vcard-photo.jpg').read_bytes())


class Headers(unittest.TestCase):
    def test_csp_covers_every_inline_script_on_the_static_pages(self):
        s = site()
        pages = [s.not_found_html(), s.log_html()]
        headers = s.headers(pages)
        for page in pages:
            for script in re.findall(r'<script>(.*?)</script>', page, re.S):
                digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                self.assertIn(f"'sha256-{digest}'", headers)
        self.assertNotIn('{scripts}', headers)

    def test_middleware_matches_headers(self):
        s = site()
        js = s.middleware((build.ROOT / 'functions/_middleware.js').read_text(encoding='utf-8'))
        for k, v in SITE['security']['headers'].items():
            self.assertIn(f"'{k}': '{v}',", js)
        self.assertIn("`script-src 'self' 'nonce-${nonce}' https://static.cloudflareinsights.com`", js)
        self.assertEqual(js.count('// BEGIN GENERATED'), 1)

    def test_middleware_without_markers_fails(self):
        with self.assertRaises(build.BuildError):
            site().middleware('const SECURITY = {};\n')

    def test_security_txt_expiry(self):
        text = 'Expires: 2027-10-22T00:00:00Z\n'
        utc = datetime.timezone.utc
        build.security_txt_expiry(text, datetime.datetime(2027, 7, 1, tzinfo=utc))
        with self.assertRaises(build.BuildError):
            build.security_txt_expiry(text, datetime.datetime(2027, 8, 1, tzinfo=utc))


class Committed(unittest.TestCase):
    def test_built_files_are_current(self):
        for path, text in build.build().items():
            with self.subTest(path):
                self.assertEqual((build.ROOT / path).read_bytes(), text.encode('utf-8'), 'run python3 build.py')


if __name__ == '__main__':
    unittest.main()
