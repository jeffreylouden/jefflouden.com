#!/usr/bin/env python3
"""Posts new log entries to Bluesky as @jefflouden.com. The site stays the
original: each post carries the entry's words, its photo and place, and a
link back to the entry on jefflouden.com/log.

    BLUESKY_APP_PASSWORD=... python3 scripts/bluesky_post.py --before REF --after REF

It compares src/log.json at two commits and posts the entries that are new
at the second (by their permalink id) and dated within the last week, oldest
first and at most five, so an edit or a rename never floods the account.
Without BLUESKY_APP_PASSWORD it posts nothing and says so. With --recent in
place of --before it posts every entry from the last week, for catching up by
hand from the workflow's Run workflow button. Standard library
only, like build.py.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
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


def compose(e, anchor, root='https://jefflouden.com'):
    """The post's text and link facets for one entry: what it is, Jeff's words,
    the place, then a link back to the entry. His words give way first."""
    permalink = f'{root}/log#{anchor}'

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
        block(e.get('place', ''))
        block('jefflouden.com/log', permalink)
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


def call(method, open_url, token=None, body=None, data=None, content_type='application/json'):
    headers = {'Content-Type': content_type}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    payload = data if data is not None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(f'{PDS}/xrpc/{method}', data=payload, headers=headers, method='POST')
    try:
        with open_url(request, timeout=60) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300] if e.fp else ''
        raise PostError(f'Bluesky refused {method} ({e.code}): {detail}') from None
    except urllib.error.URLError as e:
        raise PostError(f'Could not reach Bluesky: {e.reason}') from None


def post(entries, identifier, password, open_url=urllib.request.urlopen, now=None):
    """Posts each (entry, anchor) and returns their bsky.app URLs."""
    session = call('com.atproto.server.createSession', open_url, body={'identifier': identifier, 'password': password})
    token, did = session['accessJwt'], session['did']
    urls = []
    for e, anchor in entries:
        text, facets = compose(e, anchor)
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
