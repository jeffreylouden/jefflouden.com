"""Tests for scripts/bluesky_post.py, against a stand-in for Bluesky's API.
Nothing here reaches the network."""

import copy
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bluesky_post  # noqa: E402
import build  # noqa: E402
from fixtures import LOG  # noqa: E402


def with_entries(*new):
    log = copy.deepcopy(LOG)
    log['entries'][:0] = list(new)
    return log


class FakeBluesky:
    """Answers createSession, listRecords, uploadBlob and createRecord, and keeps what it was sent."""

    def __init__(self, fail=None, existing=()):
        self.calls, self.fail, self.existing = [], fail, list(existing)

    def __call__(self, request, timeout):
        method = request.full_url.rsplit('/', 1)[-1].split('?')[0]
        self.calls.append((method, request))
        if method == self.fail:
            raise urllib.error.HTTPError(request.full_url, 400, 'Bad Request', {}, io.BytesIO(b'{"error":"InvalidRequest"}'))
        answers = {
            'com.atproto.server.createSession': {'accessJwt': 'jwt', 'did': 'did:plc:me'},
            'com.atproto.repo.listRecords': {'records': [{'uri': 'at://x', 'value': v} for v in self.existing]},
            'com.atproto.repo.uploadBlob': {'blob': {'$type': 'blob', 'ref': {'$link': 'cid'}, 'mimeType': 'image/jpeg', 'size': 1}},
            'com.atproto.repo.createRecord': {'uri': f'at://did:plc:me/app.bsky.feed.post/rkey{len(self.calls)}'},
        }
        return io.BytesIO(json.dumps(answers[method]).encode())

    def records(self):
        return [json.loads(r.data)['record'] for m, r in self.calls if m == 'com.atproto.repo.createRecord']


class NewEntries(unittest.TestCase):
    def test_only_new_recent_entries_oldest_first(self):
        after = with_entries(
            {'kind': 'note', 'date': '2026-10-25', 'text': 'Second.'},
            {'kind': 'note', 'date': '2026-10-24', 'text': 'First.'},
            {'kind': 'note', 'date': '2026-09-01', 'text': 'Backdated a long way.'},
        )
        found = bluesky_post.new_entries(LOG, after, '2026-10-25')
        self.assertEqual([a for _, a in found], ['2026-10-24-first', '2026-10-25-second'])

    def test_an_edit_posts_nothing(self):
        after = copy.deepcopy(LOG)
        after['entries'][1]['place'] = 'Brooklyn'
        after['entries'][0]['text'] = 'Changed words.'
        self.assertEqual(bluesky_post.new_entries(LOG, after, '2026-10-25'), [])

    def test_no_history_posts_nothing(self):
        self.assertEqual(bluesky_post.new_entries(None, with_entries({'kind': 'note', 'date': '2026-10-25', 'text': 'x'}), '2026-10-25'), [])

    def test_catching_up_posts_the_last_week(self):
        found = bluesky_post.new_entries({'entries': []}, LOG, '2026-10-25')
        self.assertEqual([a for _, a in found], ['2026-10-23-hundred-rabbits', '2026-10-23-you-can-see-everything'])
        self.assertEqual(bluesky_post.new_entries({'entries': []}, LOG, '2026-11-15'), [])

    def test_at_most_five(self):
        after = with_entries(*[{'kind': 'note', 'date': '2026-10-25', 'text': f'n{i}'} for i in range(8)])
        self.assertEqual(len(bluesky_post.new_entries(LOG, after, '2026-10-25')), bluesky_post.MAX_POSTS)


class Compose(unittest.TestCase):
    def links(self, text, facets):
        raw = text.encode('utf-8')
        return [(raw[f['index']['byteStart']:f['index']['byteEnd']].decode(), f['features'][0]['uri']) for f in facets]

    def test_a_thing_is_its_title_and_words_only(self):
        text, facets = bluesky_post.compose(LOG['entries'][0], '2026-10-23-you-can-see-everything')
        self.assertEqual(text, 'You Can See Everything\n\nSee. It. Immediately.')
        self.assertEqual(self.links(text, facets), [('You Can See Everything', 'https://www.imdb.com/title/tt45368240')])

    def test_a_quote_and_a_bare_photo(self):
        text, _ = bluesky_post.compose({'kind': 'quote', 'date': 'd', 'quote': 'Less, but better.', 'who': 'Dieter Rams', 'place': 'Berlin'}, 'a')
        self.assertEqual(text, '“Less, but better.”\nDieter Rams')
        text, facets = bluesky_post.compose({'kind': 'photo', 'date': 'd', 'url': 'https://u', 'image': {'src': 's', 'alt': 'a'}, 'place': 'San Francisco'}, 'a')
        self.assertEqual((text, facets), ('', []))

    def test_offsets_count_bytes(self):
        text, facets = bluesky_post.compose({'kind': 'book', 'date': 'd', 'title': 'Ünïcode ✨', 'url': 'https://u', 'text': 'é'}, 'a')
        self.assertEqual(self.links(text, facets)[0], ('Ünïcode ✨', 'https://u'))

    def test_long_words_give_way(self):
        text, facets = bluesky_post.compose({'kind': 'book', 'date': 'd', 'title': 'T', 'url': 'https://u', 'text': 'word ' * 100}, 'a')
        self.assertEqual(len(text), bluesky_post.LIMIT)
        self.assertTrue(text.startswith('T\n\nword') and text.endswith('…'))
        self.assertEqual(self.links(text, facets), [('T', 'https://u')])


class Post(unittest.TestCase):
    def test_posts_text_and_signs_in_once(self):
        bsky = FakeBluesky()
        urls = bluesky_post.post([(LOG['entries'][0], 'a'), (LOG['entries'][1], 'b')], 'did:plc:me', 'pw', bsky)
        self.assertEqual([m for m, _ in bsky.calls], ['com.atproto.server.createSession', 'com.atproto.repo.listRecords', 'com.atproto.repo.createRecord', 'com.atproto.repo.createRecord'])
        self.assertEqual(urls, ['https://bsky.app/profile/jefflouden.com/post/rkey3', 'https://bsky.app/profile/jefflouden.com/post/rkey4'])
        self.assertEqual(bsky.calls[1][1].get_header('Authorization'), 'Bearer jwt')
        record = bsky.records()[0]
        self.assertEqual(record['$type'], 'app.bsky.feed.post')
        self.assertEqual(record['langs'], ['en'])
        self.assertNotIn('embed', record)

    def test_a_photo_goes_up_with_its_description(self):
        real = json.loads((build.SRC / 'log.json').read_text(encoding='utf-8'))
        photo = next((e for e in real['entries'] if e.get('image')), None)
        if photo is None:
            self.skipTest('the log has no photo to post')
        bsky = FakeBluesky()
        bluesky_post.post([(photo, 'p')], 'did:plc:me', 'pw', bsky)
        upload = next(r for m, r in bsky.calls if m == 'com.atproto.repo.uploadBlob')
        self.assertEqual(upload.get_header('Content-type'), 'image/jpeg')
        self.assertEqual(upload.data, (build.PUBLIC / photo['image']['src']).read_bytes())
        image = bsky.records()[0]['embed']['images'][0]
        self.assertEqual(image['alt'], photo['image']['alt'])
        self.assertEqual(set(image['aspectRatio']), {'width', 'height'})

    def test_whats_already_posted_is_skipped(self):
        text, _ = bluesky_post.compose(LOG['entries'][0], 'a')
        bsky = FakeBluesky(existing=[{'text': text}])
        urls = bluesky_post.post([(LOG['entries'][0], 'a'), (LOG['entries'][1], 'b')], 'did:plc:me', 'pw', bsky)
        self.assertEqual(len(urls), 1)
        self.assertEqual([r['text'].split('\n')[0] for r in bsky.records()], ['Hundred Rabbits'])

    def test_a_photo_already_posted_matches_on_its_description(self):
        photo = {'kind': 'photo', 'date': 'd', 'image': {'src': 'images/log/x.jpg', 'alt': 'Two candles'}}
        bsky = FakeBluesky(existing=[{'text': '', 'embed': {'$type': 'app.bsky.embed.images', 'images': [{'alt': 'Two candles'}]}}])
        self.assertEqual(bluesky_post.post([(photo, 'p')], 'did:plc:me', 'pw', bsky), [])
        self.assertEqual(bsky.records(), [])

    def test_a_refusal_says_what_happened(self):
        with self.assertRaisesRegex(bluesky_post.PostError, 'createRecord.*400'):
            bluesky_post.post([(LOG['entries'][0], 'a')], 'did:plc:me', 'pw', FakeBluesky(fail='com.atproto.repo.createRecord'))


if __name__ == '__main__':
    unittest.main()
