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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402

SITE = json.loads((build.SRC / 'site.json').read_text(encoding='utf-8'))
SITE_SCHEMA = json.loads((build.SRC / 'site.schema.json').read_text(encoding='utf-8'))
LOG = json.loads((build.SRC / 'log.json').read_text(encoding='utf-8'))
LOG_SCHEMA = json.loads((build.SRC / 'log.schema.json').read_text(encoding='utf-8'))


def site(change=None, log_change=None):
    s, l = copy.deepcopy(SITE), copy.deepcopy(LOG)
    if change:
        change(s)
    if log_change:
        log_change(l)
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
    def check(self, change=None):
        s = copy.deepcopy(SITE)
        if change:
            change(s)
        build.validate(s, SITE_SCHEMA, SITE_SCHEMA, 'site')

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

    def check_log(self, change):
        l = copy.deepcopy(LOG)
        change(l)
        build.validate(l, LOG_SCHEMA, LOG_SCHEMA, 'log')

    def test_log_shapes(self):
        add = lambda e: lambda l: l['entries'].insert(0, e)
        self.check_log(lambda l: None)
        for good in [{'kind': 'shipped', 'date': '2026-11-01', 'title': 'T', 'text': 'W'},
                     {'kind': 'quote', 'date': '2026-11-01', 'quote': 'Q', 'who': 'W'},
                     {'kind': 'note', 'date': '2026-11-01', 'text': 'T'},
                     {'kind': 'photo', 'date': '2026-11-01', 'shows': 'S'}]:
            with self.subTest(good['kind']):
                self.check_log(add(good))
        for name, bad in {'an unknown kind': {'kind': 'tweet', 'date': '2026-11-01', 'title': 'T', 'text': 'W'},
                          'a thing with no words': {'kind': 'book', 'date': '2026-11-01', 'title': 'T'},
                          'a photo with no description': {'kind': 'photo', 'date': '2026-11-01'},
                          'a photo file': {'kind': 'photo', 'date': '2026-11-01', 'shows': 'S', 'image': {'src': 'a.jpg', 'alt': 'a'}},
                          'a bad date': {'kind': 'note', 'date': '2026-11', 'text': 'T'}}.items():
            with self.subTest(name), self.assertRaises(build.BuildError):
                self.check_log(add(bad))



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

    def test_log_rejects(self):
        cases = {
            'entries out of order': lambda l: l['entries'].append({'kind': 'note', 'date': '2099-01-01', 'text': 'x'}),
            'a semicolon in his words': lambda l: l['entries'][0].update(text='Good; great.'),
            'a middle dot in a place': lambda l: l['entries'][0].update(place='A · B'),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(build.BuildError):
                site(log_change=change)



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

    def test_removing_linkedin_removes_it_everywhere(self):
        s = site(lambda s: s.update(links=[l for l in s['links'] if not l['removeAtExit']]))
        for name, text in [('index.html', s.index_html()), ('index.md', s.index_md()), ('index.txt', s.index_txt()),
                           ('llms.txt', s.llms_txt()), ('llms-full.txt', s.llms_full_txt()), ('humans.txt', s.humans_txt())]:
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

    def test_llms_full_has_every_role_and_no_semicolons(self):
        s = site()
        text = s.llms_full_txt()
        for role in SITE['roles']:
            with self.subTest(role['title']):
                self.assertIn(f'### {s.names(role)}: {build.plain(role["title"])}', text)
                if role.get('resume'):
                    self.assertIn(build.with_urls(role['resume']), text)
        self.assertIn('### What is Aureo?\n\nAureo (https://aureo.audio) is a studio gear', text)
        self.assertNotIn(';', text)
        self.assertIn('Full profile: https://jefflouden.com/llms-full.txt', s.llms_txt())

    def test_log_md(self):
        def add(l):
            l['entries'][:0] = [
                {'kind': 'quote', 'date': '2026-11-01', 'quote': 'Q', 'who': 'W', 'text': 'Why'},
                {'kind': 'shipped', 'date': '2026-11-01', 'title': 'A thing', 'url': 'https://example.com', 'text': 'It [works](https://example.com/w).'},
            ]
        text = site(log_change=add).log_md()
        self.assertIn('## Quote from W\n\n- Kind: quote\n- Logged: November 1, 2026\n\n> “Q”\n\nIn his words: Why\n', text)
        self.assertIn('## A thing\n\n- Kind: shipped\n- Logged: November 1, 2026\n- Link: https://example.com\n\nIn his words: It works (https://example.com/w).', text)
        self.assertIn('## Photo: Gerhard Richter', text)
        self.assertIn('Nothing logged yet.', site(log_change=lambda l: l.update(entries=[])).log_md())
        self.assertIn('Log: https://jefflouden.com/log.md', site().llms_txt())

    def test_nothing_for_people_links_to_the_agent_files(self):
        s = site()
        for name, text in [('index.html', s.index_html()), ('404.html', s.not_found_html()), ('index.md', s.index_md()),
                           ('index.txt', s.index_txt()), ('humans.txt', s.humans_txt()), ('sitemap.xml', s.sitemap_xml())]:
            with self.subTest(name):
                self.assertNotIn('llms-full', text)
                self.assertNotIn('log.md', text)

    def test_semicolon_in_a_product_description_fails(self):
        with self.assertRaises(build.BuildError):
            site(lambda s: s['products']['aureo'].update(description='a catalog; and more.'))

    def test_vcard(self):
        card = site().vcard()
        self.assertTrue(card.endswith('END:VCARD\r\n'))
        self.assertNotIn('\n', card.replace('\r\n', ''))
        self.assertTrue(all(len(line) <= 75 for line in card.split('\r\n')))
        self.assertIn('TITLE:SVP\\, Engineering\r\n', card)
        photo = re.search(r'PHOTO;ENCODING=b;TYPE=JPEG:(.*?)\r\nEND', card, re.S).group(1).replace('\r\n ', '')
        self.assertEqual(base64.b64decode(photo), (build.SRC / 'vcard-photo.jpg').read_bytes())


class Haunting(unittest.TestCase):
    def test_404_without_javascript_shows_the_first_line(self):
        page = site().not_found_html()
        static = re.search(r'<p id="haunt">(.*?)</p>', page).group(1)
        lines = re.findall(r"'([^']+)'", re.search(r'var lines = \[(.*?)\]', page).group(1))
        self.assertGreater(len(lines), 1)
        self.assertEqual(lines[0], static)
        self.assertFalse(any(';' in line or '·' in line for line in lines))

    def test_flashlight_stays_hidden_without_javascript(self):
        self.assertIn('<div class="flashlight" aria-hidden="true" hidden></div>', site().index_html())


class Headers(unittest.TestCase):
    def test_csp_covers_every_inline_script_on_the_static_pages(self):
        s = site()
        pages = [s.not_found_html()]
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
