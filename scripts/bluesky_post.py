#!/usr/bin/env python3
"""Posts new log entries to Bluesky as @jefflouden.com, kept plain: a
title (linked when it has a link) or a quote and who said it, Jeff's words,
and a photo with its description. No place, date or link back to the site.

    BLUESKY_APP_PASSWORD=... python3 scripts/bluesky_post.py --before REF --after REF

It compares src/log.json at two commits and posts the entries that are new
at the second (by their permalink id) and dated within the last week, oldest
first and at most five, so an edit or a rename never floods the account.
It skips any entry already among the account's latest 100 posts (same text
and photo description), so a repeat run never posts twice. With --recent in
place of --before it posts every entry from the last week, for catching up by
hand from the workflow's Run workflow button. Without BLUESKY_APP_PASSWORD it
posts nothing and says so. Standard library only, like build.py.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

PDS = 'https://bsky.social'
MAX_POSTS = 5
RECENT_DAYS = 7
LIMIT = 300  # Bluesky's limit, in characters as people count them


class PostError(Exception):
    pass


def log_at(ref):
    """src/log.json as it was at a commit, or None where it didn't exist yet."""
    if not ref or set(ref) == {'0'}:
        return None
    try:
        text = subprocess.run(['git', 'show', f'{ref}:src/log.json'], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        return None
    return json.loads(text)


def new_entries(before, after, today):
    """(entry, anchor) for each entry new in `after`, oldest first."""
    if before is None:
        return []  # no history to compare against: post nothing rather than everything
    old = set(build.entry_ids(before['entries']))
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    fresh = [(e, i) for e, i in zip(after['entries'], build.entry_ids(after['entries'])) if i not in old and e['date'] >= cutoff]
    return list(reversed(fresh))[:MAX_POSTS]


class Text:
    """Post text with link facets, which Bluesky places by UTF-8 byte offset."""

    def __init__(self):
        self.parts, self.facets = [], []

    def add(self, s, link=None):
        start = len(''.join(self.parts).encode('utf-8'))
        self.parts.append(s)
        if link:
            end = start + len(s.encode('utf-8'))
            self.facets.append({'index': {'byteStart': start, 'byteEnd': end},
                                'features': [{'$type': 'app.bsky.richtext.facet#link', 'uri': link}]})

    def __str__(self):
        return ''.join(self.parts)


def compose(e, anchor):
    """The post's text and link facets for one entry, kept plain: what it is
    (a title, linked when it has a link, or a quote and who said it) and Jeff's
    words. A photo carries its picture instead. His words give way first."""

    def build_text(words):
        t = Text()

        def block(s, link=None):
            if s:
                if str(t):
                    t.add('\n\n')
                t.add(s, link)

        if e['kind'] == 'quote':
            block(f'“{build.plain(e["quote"])}”\n{build.plain(e["who"])}')
        elif e.get('title'):
            block(build.plain(e['title']), e.get('url'))
        block(words)
        return t

    words = build.plain(e.get('text', ''))
    t = build_text(words)
    while len(str(t)) > LIMIT and words:
        keep = len(words) - (len(str(t)) - LIMIT) - 1
        words = (words[:keep].rstrip() + '…') if keep > 0 else ''
        t = build_text(words)
    if len(str(t)) > LIMIT:
        raise PostError(f'{anchor} is too long for a Bluesky post even without its words')
    return str(t), t.facets


def call(method, open_url, token=None, body=None, data=None, content_type='application/json', query=None):
    headers = {'Content-Type': content_type}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    url = f'{PDS}/xrpc/{method}'
    if query is not None:
        request = urllib.request.Request(f'{url}?{urllib.parse.urlencode(query)}', headers=headers, method='GET')
    else:
        payload = data if data is not None else json.dumps(body).encode('utf-8')
        request = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with open_url(request, timeout=60) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300] if e.fp else ''
        raise PostError(f'Bluesky refused {method} ({e.code}): {detail}') from None
    except urllib.error.URLError as e:
        raise PostError(f'Could not reach Bluesky: {e.reason}') from None


def fingerprint(text, alt):
    """What makes two posts the same here: their text and their photo's description."""
    return (text.strip(), alt or '')


def already_posted(did, token, open_url):
    """Fingerprints of the account's latest 100 posts, so nothing goes up twice."""
    found = call('com.atproto.repo.listRecords', open_url, token,
                 query={'repo': did, 'collection': 'app.bsky.feed.post', 'limit': 100})
    seen = set()
    for item in found.get('records', []):
        record = item['value']
        images = record.get('embed', {}).get('images', [])
        seen.add(fingerprint(record.get('text', ''), images[0].get('alt') if images else None))
    return seen


def post(entries, identifier, password, open_url=urllib.request.urlopen, now=None):
    """Posts each (entry, anchor) not already on Bluesky and returns their bsky.app URLs."""
    session = call('com.atproto.server.createSession', open_url, body={'identifier': identifier, 'password': password})
    token, did = session['accessJwt'], session['did']
    seen = already_posted(did, token, open_url)
    urls = []
    for e, anchor in entries:
        text, facets = compose(e, anchor)
        if fingerprint(text, e.get('image', {}).get('alt')) in seen:
            print(f'Already on Bluesky, skipped: {anchor}')
            continue
        record = {
            '$type': 'app.bsky.feed.post',
            'text': text,
            'createdAt': (now or datetime.datetime.now(datetime.timezone.utc)).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
            'langs': ['en'],
        }
        if facets:
            record['facets'] = facets
        if e.get('image'):
            data = (build.PUBLIC / e['image']['src']).read_bytes()
            width, height, _ = build.jpeg_info(data)
            blob = call('com.atproto.repo.uploadBlob', open_url, token, data=data, content_type='image/jpeg')['blob']
            record['embed'] = {'$type': 'app.bsky.embed.images', 'images': [
                {'image': blob, 'alt': e['image']['alt'], 'aspectRatio': {'width': width, 'height': height}}]}
        made = call('com.atproto.repo.createRecord', open_url, token, body={'repo': did, 'collection': 'app.bsky.feed.post', 'record': record})
        rkey = made['uri'].rsplit('/', 1)[-1]
        urls.append(f'https://bsky.app/profile/jefflouden.com/post/{rkey}')
    return urls


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--before', help='the commit to compare against')
    args.add_argument('--after', required=True)
    args.add_argument('--recent', action='store_true',
                      help='post every entry from the last week, as if none were posted yet (a manual catch-up, so mind repeats)')
    args = args.parse_args()
    today = datetime.datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    after = log_at(args.after)
    if not args.recent and not args.before:
        sys.exit('Give --before, or --recent to post the last week')
    before = {'entries': []} if args.recent else log_at(args.before)
    entries = new_entries(before, after, today) if after else []
    if not entries:
        print('No new log entries to post.')
        return
    password = os.environ.get('BLUESKY_APP_PASSWORD')
    if not password:
        print('BLUESKY_APP_PASSWORD is not set, so nothing was posted to Bluesky.')
        return
    site = json.loads((build.SRC / 'site.json').read_text(encoding='utf-8'))
    try:
        urls = post(entries, site['profile']['atprotoDid'], password)
    except PostError as e:
        sys.exit(str(e))
    for url in urls:
        print(url)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            f.write(f'posts={" ".join(urls)}\n')


if __name__ == '__main__':
    main()
