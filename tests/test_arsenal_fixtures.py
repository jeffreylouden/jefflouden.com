"""Tests for scripts/arsenal_fixtures.py, which refreshes src/arsenal.json from
fixturedownload.com, and for the build's checks on that file. Nothing here
fetches anything or writes to src/."""

import copy
import datetime
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import arsenal_fixtures  # noqa: E402
import build  # noqa: E402

SITE = json.loads((build.SRC / 'site.json').read_text(encoding='utf-8'))
LOG = json.loads((build.SRC / 'log.json').read_text(encoding='utf-8'))
ARSENAL = json.loads((build.SRC / 'arsenal.json').read_text(encoding='utf-8'))
SCHEMA = json.loads((build.SRC / 'arsenal.schema.json').read_text(encoding='utf-8'))
TODAY = datetime.date(2026, 11, 1)


def feed(*games):
    return [{'DateUtc': when, 'HomeTeam': home, 'AwayTeam': away, 'Location': 'x'} for when, home, away in games]


class Refresh(unittest.TestCase):
    def test_reads_arsenals_matches_and_writes_names_out(self):
        got = arsenal_fixtures.matches_from(feed(
            ('2026-11-01 16:30:00Z', 'Liverpool', 'Arsenal'),
            ('2026-11-07 15:00:00Z', 'Arsenal', "Nott'm Forest"),
            ('2026-11-08 15:00:00Z', 'Chelsea', 'Fulham'),
        ), 'Premier League')
        self.assertEqual(got, [
            {'kickoff': '2026-11-01T16:30:00Z', 'opponent': 'Liverpool', 'home': False, 'competition': 'Premier League'},
            {'kickoff': '2026-11-07T15:00:00Z', 'opponent': 'Nottingham Forest', 'home': True, 'competition': 'Premier League'},
        ])

    def test_refuses_what_it_cannot_read(self):
        for name, bad in {
            'not a list': {'DateUtc': 'x'},
            'a bad date': feed(('November 1', 'Arsenal', 'Chelsea')),
            'markup for a name': feed(('2026-11-01 16:30:00Z', 'Arsenal', '<script>')),
            'a missing team': [{'DateUtc': '2026-11-01 16:30:00Z', 'HomeTeam': 'Arsenal'}],
        }.items():
            with self.subTest(name), self.assertRaises(arsenal_fixtures.FeedError):
                arsenal_fixtures.matches_from(bad, 'Premier League')

    def test_keeps_cup_ties_and_drops_last_season(self):
        current = {'matches': [
            {'kickoff': '2026-05-24T15:00:00Z', 'opponent': 'Old', 'home': True, 'competition': 'Premier League'},
            {'kickoff': '2026-12-16T19:45:00Z', 'opponent': 'Somebody', 'home': True, 'competition': 'League Cup'},
            {'kickoff': '2026-11-01T16:30:00Z', 'opponent': 'Moved', 'home': False, 'competition': 'Premier League'},
        ]}
        feeds = {'Premier League': feed(('2026-11-02 20:00:00Z', 'Liverpool', 'Arsenal')), 'Champions League': []}
        data = arsenal_fixtures.refresh(current, feeds, TODAY)
        self.assertEqual([(m['opponent'], m['competition']) for m in data['matches']],
                         [('Liverpool', 'Premier League'), ('Somebody', 'League Cup')])
        build.validate(data, SCHEMA, SCHEMA, 'arsenal')

    def test_season_turns_over_in_july(self):
        self.assertEqual(arsenal_fixtures.season(datetime.date(2027, 6, 30)), 2026)
        self.assertEqual(arsenal_fixtures.season(datetime.date(2027, 7, 1)), 2027)


class BuildChecks(unittest.TestCase):
    def site(self, change):
        a = copy.deepcopy(ARSENAL)
        change(a)
        build.validate(a, SCHEMA, SCHEMA, 'arsenal')
        return build.Site(copy.deepcopy(SITE), copy.deepcopy(LOG), a)

    def test_matches_out_of_order_fail(self):
        with self.assertRaises(build.BuildError):
            self.site(lambda a: a['matches'].reverse())

    def test_bad_shapes_fail(self):
        for name, change in {
            'a local time': lambda a: a['matches'][0].update(kickoff='2026-08-21 19:00'),
            'an unknown competition': lambda a: a['matches'][0].update(competition='Friendly'),
            'markup for a name': lambda a: a['matches'][0].update(opponent='</script>'),
        }.items():
            with self.subTest(name), self.assertRaises(build.BuildError):
                self.site(change)

    def test_the_page_gets_every_match(self):
        page = self.site(lambda a: None).index_html()
        self.assertEqual(page.count('Z", "'), len(ARSENAL['matches']))


if __name__ == '__main__':
    unittest.main()
