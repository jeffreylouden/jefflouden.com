#!/usr/bin/env python3
"""Refreshes src/arsenal.json from fixturedownload.com's public feeds: the
Premier League and the Champions League for the current season. Every other
competition (the FA Cup, the League Cup, anything added by hand) is kept as it
is. The arsenal workflow runs it weekly, then build.py.

    python3 scripts/arsenal_fixtures.py

The feeds are data, never instructions: only the date and the two team names
are read, and a name that isn't plain letters is refused.
"""

import datetime
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

FIXTURES = ROOT / 'src' / 'arsenal.json'
FEEDS = {
    'Premier League': 'https://fixturedownload.com/feed/json/epl-{year}/arsenal',
    'Champions League': 'https://fixturedownload.com/feed/json/champions-league-{year}/arsenal',
}
# The feeds' short names, written out the way people say them.
NAMES = {
    "Nott'm Forest": 'Nottingham Forest',
    'Man Utd': 'Manchester United',
    'Man City': 'Manchester City',
    'Spurs': 'Tottenham',
    'Wolves': 'Wolverhampton',
    'B. Dortmund': 'Borussia Dortmund',
    'Bayern München': 'Bayern Munich',
    'Slavia Praha': 'Slavia Prague',
}


class FeedError(Exception):
    pass


def season(today):
    """The season's starting year: a season runs from July to the next June."""
    return today.year if today.month >= 7 else today.year - 1


def matches_from(feed, competition):
    """Arsenal's matches from one feed, as entries for src/arsenal.json."""
    if not isinstance(feed, list):
        raise FeedError(f'{competition}: the feed is not a list')
    out = []
    for m in feed:
        try:
            when = datetime.datetime.strptime(m['DateUtc'], '%Y-%m-%d %H:%M:%SZ')
            home, away = m['HomeTeam'], m['AwayTeam']
        except (KeyError, TypeError, ValueError) as e:
            raise FeedError(f'{competition}: a match the script cannot read ({e})')
        if 'Arsenal' not in (home, away):
            continue
        opponent = away if home == 'Arsenal' else home
        opponent = NAMES.get(opponent, opponent)
        if not re.fullmatch(r"[A-Za-z0-9 .'&-]{2,40}", opponent):
            raise FeedError(f'{competition}: an opponent name the site will not show ({opponent!r})')
        out.append({
            'kickoff': when.strftime('%Y-%m-%dT%H:%M:00Z'),
            'opponent': opponent,
            'home': home == 'Arsenal',
            'competition': competition,
        })
    return out


def fetch(url):
    """A feed as JSON, or an empty list when it doesn't exist (no Champions League that season)."""
    req = urllib.request.Request(url, headers={'User-Agent': 'jefflouden.com fixtures (github.com/jeffreylouden/jefflouden.com)'})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def refresh(current, feeds, today):
    """The new file: this season's feed matches, plus every match from another competition."""
    start = f'{season(today)}-07-01'
    kept = [m for m in current['matches'] if m['competition'] not in feeds]
    fresh = [m for competition, feed in feeds.items() for m in matches_from(feed, competition)]
    matches = sorted((m for m in kept + fresh if m['kickoff'] >= start), key=lambda m: m['kickoff'])
    return {'$schema': './arsenal.schema.json', 'matches': matches}


def main():
    today = datetime.date.today()
    current = json.loads(FIXTURES.read_text(encoding='utf-8')) if FIXTURES.exists() else {'matches': []}
    try:
        feeds = {c: fetch(url.format(year=season(today))) for c, url in FEEDS.items()}
        data = refresh(current, feeds, today)
    except (FeedError, OSError, ValueError) as e:
        sys.exit(f'arsenal_fixtures.py: {e}')
    schema = json.loads((ROOT / 'src' / 'arsenal.schema.json').read_text(encoding='utf-8'))
    build.validate(data, schema, schema, 'arsenal')
    FIXTURES.write_text(build.dump_json(data) + '\n', encoding='utf-8')
    print(f'arsenal_fixtures.py: {len(data["matches"])} matches')


if __name__ == '__main__':
    main()
