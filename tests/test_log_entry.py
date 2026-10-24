"""Tests for the log's photos and the issue form that adds entries:
scripts/log_entry.py, scripts/log_photo.py and the photo checks in build.py.

The photo tests need Pillow (CI installs it) and skip without it. Everything
runs in a temporary copy of public/ and src/log.json, never the real ones.
"""

import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import build  # noqa: E402
import log_entry  # noqa: E402

try:
    from PIL import Image
except ImportError:
    Image = None

SITE = json.loads((build.SRC / 'site.json').read_text(encoding='utf-8'))
LOG = json.loads((build.SRC / 'log.json').read_text(encoding='utf-8'))


def form(**fields):
    """An issue body as GitHub writes one from the form."""
    labels = ['Kind', 'Title', 'Link', 'Quote', 'Who said it', 'Your words', 'Photo', 'What the photo shows', 'Place', 'Date']
    return '\n\n'.join(f'### {label}\n\n{fields.get(label, "_No response_")}' for label in labels)


def phone_photo(size=(4032, 3024)):
    """A JPEG like a phone's: large, rotated by EXIF, and carrying GPS."""
    image = Image.new('RGB', size, (120, 160, 200))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees to view
    exif[0x010F] = 'Phone maker'
    exif.get_ifd(0x8825).update({1: 'N', 2: (38.0, 53.0, 0.0), 3: 'W', 4: (77.0, 2.0, 0.0)})
    out = io.BytesIO()
    image.save(out, 'JPEG', exif=exif.tobytes(), quality=90)
    return out.getvalue()


class Sandbox(unittest.TestCase):
    """Points build.py and log_entry.py at a temporary public/ and log."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.public = self.tmp / 'public'
        (self.public / 'images' / 'log').mkdir(parents=True)
        self.log = self.tmp / 'log.json'
        self.log.write_text(json.dumps(LOG, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for target, name, value in [(build, 'PUBLIC', self.public), (build, 'LOG_IMAGES', self.public / 'images' / 'log'), (log_entry, 'LOG', self.log)]:
            mock.patch.object(target, name, value).start()

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.tmp)

    def site(self):
        return build.Site(copy.deepcopy(SITE), json.loads(self.log.read_text(encoding='utf-8')))


class Form(unittest.TestCase):
    def test_parse_form(self):
        fields = log_entry.parse_form(form(**{'Kind': 'movie', 'Title': 'Alien', 'Your words': 'In space.\r\n'}))
        self.assertEqual(fields['Kind'], 'movie')
        self.assertEqual(fields['Title'], 'Alien')
        self.assertEqual(fields['Your words'], 'In space.')
        self.assertIsNone(fields['Link'])

    def test_thing(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'album', 'Title': 'Blue', 'Link': 'https://example.com/blue', 'Your words': 'Still\nperfect.', 'Place': 'Brooklyn',
        })), '2026-10-24')
        self.assertEqual(entry, {'kind': 'album', 'date': '2026-10-24', 'title': 'Blue', 'url': 'https://example.com/blue', 'text': 'Still perfect.', 'place': 'Brooklyn'})

    def test_quote_drops_its_quotation_marks(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{'Kind': 'quote', 'Quote': '“Less, but better.”', 'Who said it': 'Dieter Rams'})), '2026-10-24')
        self.assertEqual(entry, {'kind': 'quote', 'date': '2026-10-24', 'quote': 'Less, but better.', 'who': 'Dieter Rams'})

    def test_note_with_photo(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'note', 'Your words': 'First frost.',
            'Photo': '![IMG_0001](https://github.com/user-attachments/assets/0a1b2c3d-4e5f-6789-abcd-ef0123456789)',
            'What the photo shows': 'Frost on a car window',
        })), '2026-10-24')
        self.assertEqual(entry['image'], {'src': 'images/log/2026-10-24-frost-on-a-car-window.jpg', 'alt': 'Frost on a car window'})
        self.assertEqual(entry['_photo_url'], 'https://github.com/user-attachments/assets/0a1b2c3d-4e5f-6789-abcd-ef0123456789')

    def test_a_backdated_photo_is_named_for_its_day(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'photo', 'Date': '10/20/2026', 'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'Frost',
        })), '2026-10-24')
        self.assertEqual((entry['date'], entry['image']['src']), ('2026-10-20', 'images/log/2026-10-20-frost.jpg'))

    def test_photo(self):
        # Issue #28 as Jeff wrote it, with the kind he meant.
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'photo',
            'Photo': '<img width="1251" height="1668" alt="Gerhard Richter Zwei Kerzen (Two Candles), 1982" src="https://github.com/user-attachments/assets/4c1e210b-b47b-4cfd-8843-6b652449a212" />',
            'What the photo shows': 'Gerhard Richter Zwei Kerzen (Two Candles), 1982',
            'Place': 'San Francisco',
        })), '2026-10-24')
        self.assertEqual(entry['kind'], 'photo')
        self.assertEqual(entry['image'], {'src': 'images/log/2026-10-24-gerhard-richter-zwei-kerzen-two-candles.jpg', 'alt': 'Gerhard Richter Zwei Kerzen (Two Candles), 1982'})
        self.assertEqual(entry['place'], 'San Francisco')
        self.assertNotIn('title', entry)
        self.assertNotIn('text', entry)

    def test_photo_with_a_title_and_words(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'photo', 'Title': 'Zwei Kerzen', 'Link': 'https://example.com/richter', 'Your words': 'Stood here a while.',
            'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'Two candles',
        })), '2026-10-24')
        self.assertEqual((entry['title'], entry['url'], entry['text']), ('Zwei Kerzen', 'https://example.com/richter', 'Stood here a while.'))

    def test_mistakes(self):
        cases = {
            'no kind': {},
            'an unknown kind': {'Kind': 'podcast', 'Title': 'x', 'Your words': 'y'},
            'a thing with no title': {'Kind': 'book', 'Your words': 'y'},
            'a thing with no words': {'Kind': 'book', 'Title': 'x'},
            'a quote with no one to say it': {'Kind': 'quote', 'Quote': 'q'},
            'a quote with a photo': {'Kind': 'quote', 'Quote': 'q', 'Who said it': 'w', 'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'a'},
            'a note with a title': {'Kind': 'note', 'Title': 'x', 'Your words': 'y'},
            'a photo with no description': {'Kind': 'note', 'Your words': 'y', 'Photo': 'https://github.com/user-attachments/assets/abc'},
            'a photo from anywhere else': {'Kind': 'note', 'Your words': 'y', 'Photo': '![x](https://evil.example/x.jpg)', 'What the photo shows': 'a'},
            'a two-line place': {'Kind': 'note', 'Your words': 'y', 'Place': 'a\nb'},
            'a photo with no picture': {'Kind': 'photo', 'Title': 'x'},
            'a photo with a link but no title': {'Kind': 'photo', 'Link': 'https://example.com', 'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'a'},
        }
        for name, fields in cases.items():
            with self.subTest(name), self.assertRaises(log_entry.EntryError):
                log_entry.entry_from(log_entry.parse_form(form(**fields)), '2026-10-24')

    def test_download_tries_without_the_token_first(self):
        calls = []

        class Res(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def open_url(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, None)
            return Res(b'photo')

        url = 'https://github.com/user-attachments/assets/abc'
        self.assertEqual(log_entry.download(url, 'tok', open_url), b'photo')
        self.assertIsNone(calls[0].get_header('Authorization'))
        # Unredirected, so a redirect to GitHub's storage host never carries it.
        self.assertEqual(calls[1].unredirected_hdrs.get('Authorization'), 'Bearer tok')
        self.assertNotIn('Authorization', calls[1].headers)

    def test_download_refused_says_so(self):
        def open_url(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, None)

        with self.assertRaisesRegex(log_entry.EntryError, '403'):
            log_entry.download('https://github.com/user-attachments/assets/abc', 'tok', open_url)

    def test_dates(self):
        for value in ['', None, '2026-10-24']:
            self.assertEqual(log_entry.parse_date(value, '2026-10-24'), '2026-10-24')
        for value in ['2026-10-20', '10/20/2026', '10/20/26', 'Oct 20 2026', 'October 20, 2026', 'Oct. 20, 2026', '20 Oct 2026']:
            with self.subTest(value):
                self.assertEqual(log_entry.parse_date(value, '2026-10-24'), '2026-10-20')
        for value in ['2026-10-25', 'last Tuesday', '2026-13-01']:
            with self.subTest(value), self.assertRaises(log_entry.EntryError):
                log_entry.parse_date(value, '2026-10-24')

    def test_commit_messages(self):
        self.assertEqual(log_entry.commit_message({'kind': 'movie', 'title': 'Alien'}), "Add movie entry for 'Alien'")
        self.assertEqual(log_entry.commit_message({'kind': 'quote', 'who': 'Dieter Rams'}), 'Add a quote from Dieter Rams to the log')
        self.assertEqual(log_entry.commit_message({'kind': 'note'}), 'Add a note to the log')

    def test_slug(self):
        self.assertEqual(log_entry.slug('You Can See Everything!'), 'you-can-see-everything')
        self.assertEqual(log_entry.slug('¿¡'), 'entry')


class Add(Sandbox):
    def test_adds_at_the_top_and_builds(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{'Kind': 'book', 'Title': 'Piranesi', 'Your words': 'A house of tides.', 'Place': 'Washington, D.C.'})), '2026-10-24')
        log_entry.add(entry)
        log = json.loads(self.log.read_text(encoding='utf-8'))
        self.assertEqual(log['entries'][0]['title'], 'Piranesi')
        self.assertEqual(len(log['entries']), len(LOG['entries']) + 1)
        page = self.site().log_html()
        self.assertIn('<p class="what">Piranesi</p>\n        <p>A house of tides.</p>\n        <p class="place">Washington, D.C.</p>', page)

    def test_a_backdated_entry_goes_above_the_others_from_its_day(self):
        def add(date, title):
            log_entry.add(log_entry.entry_from(log_entry.parse_form(form(**{'Kind': 'book', 'Title': title, 'Your words': 'x', 'Date': date})), '2026-10-30'))
        add('2026-10-30', 'Today')
        add('2026-10-23', 'Same day as the first two')
        add('2026-01-01', 'Oldest')
        dates = [(e['date'], e.get('title')) for e in json.loads(self.log.read_text(encoding='utf-8'))['entries']]
        self.assertEqual(dates[0], ('2026-10-30', 'Today'))
        self.assertEqual(dates[1], ('2026-10-23', 'Same day as the first two'))
        self.assertEqual(dates[-1], ('2026-01-01', 'Oldest'))
        self.site()  # still newest first, so the build takes it

    def test_an_entry_that_breaks_the_schema_writes_nothing(self):
        before = self.log.read_text(encoding='utf-8')
        with self.assertRaises(log_entry.EntryError):
            log_entry.add({'kind': 'note', 'date': 'today', 'text': 'x'})
        self.assertEqual(self.log.read_text(encoding='utf-8'), before)

    @unittest.skipUnless(Image, 'needs Pillow')
    def test_photo_is_prepared_and_shown(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'note', 'Your words': 'First frost.', 'Place': 'Brooklyn',
            'Photo': '<img width="4032" alt="IMG" src="https://github.com/user-attachments/assets/0a1b2c3d-4e5f-6789-abcd-ef0123456789" />',
            'What the photo shows': 'Frost on a car window',
        })), '2026-10-24')
        fetched = []
        log_entry.add(entry, fetch=lambda url: fetched.append(url) or phone_photo())
        self.assertEqual(fetched, ['https://github.com/user-attachments/assets/0a1b2c3d-4e5f-6789-abcd-ef0123456789'])
        data = (self.public / 'images/log/2026-10-24-frost-on-a-car-window.jpg').read_bytes()
        width, height, metadata = build.jpeg_info(data)
        self.assertEqual((width, height), (1200, 1600))  # turned upright, then fit in 1600
        self.assertFalse(metadata)
        self.assertNotIn(b'Exif', data)
        self.assertNotIn(b'Phone maker', data)
        page = self.site().log_html()
        self.assertIn('<img src="/images/log/2026-10-24-frost-on-a-car-window.jpg" alt="Frost on a car window" width="1200" height="1600" loading="lazy" decoding="async">', page)

    @unittest.skipUnless(Image, 'needs Pillow')
    def test_a_photo_on_its_own(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'photo', 'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'Two candles', 'Place': 'San Francisco',
        })), '2026-10-24')
        log_entry.add(entry, fetch=lambda url: phone_photo())
        page = self.site().log_html()
        self.assertIn('<li data-kind="photo" data-date="2026-10-24">\n        <img src="/images/log/2026-10-24-two-candles.jpg" alt="Two candles" width="1200" height="1600" loading="lazy" decoding="async">\n        <p class="place">San Francisco</p>\n      </li>', page)

    @unittest.skipUnless(Image, 'needs Pillow')
    def test_photo_names_never_collide(self):
        (self.public / 'images/log/2026-10-24-frost.jpg').write_bytes(b'')
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'note', 'Your words': 'Again.', 'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'Frost',
        })), '2026-10-24')
        self.assertEqual(entry['image']['src'], 'images/log/2026-10-24-frost-2.jpg')

    @unittest.skipUnless(Image, 'needs Pillow')
    def test_not_a_photo(self):
        entry = log_entry.entry_from(log_entry.parse_form(form(**{
            'Kind': 'note', 'Your words': 'x', 'Photo': 'https://github.com/user-attachments/assets/abc', 'What the photo shows': 'x',
        })), '2026-10-24')
        with self.assertRaises(log_entry.EntryError):
            log_entry.add(entry, fetch=lambda url: b'<html>sign in</html>')


@unittest.skipUnless(Image, 'needs Pillow')
class BuildChecks(Sandbox):
    def add_entry(self, name, data):
        (self.public / 'images/log' / name).write_bytes(data)
        log = json.loads(self.log.read_text(encoding='utf-8'))
        log['entries'].insert(0, {'kind': 'note', 'date': '2026-10-24', 'text': 'x', 'image': {'src': f'images/log/{name}', 'alt': 'a'}})
        self.log.write_text(json.dumps(log), encoding='utf-8')

    def jpeg(self, size=(800, 600), **save):
        out = io.BytesIO()
        Image.new('RGB', size).save(out, 'JPEG', **save)
        return out.getvalue()

    def test_jpeg_info_reads_baseline_and_progressive(self):
        self.assertEqual(build.jpeg_info(self.jpeg((640, 480))), (640, 480, False))
        self.assertEqual(build.jpeg_info(self.jpeg((640, 480), progressive=True)), (640, 480, False))
        with self.assertRaises(build.BuildError):
            build.jpeg_info(b'\x89PNG')

    def test_a_prepared_photo_passes(self):
        self.add_entry('ok.jpg', self.jpeg())
        self.assertEqual(self.site().images, {'images/log/ok.jpg': (800, 600)})

    def test_refuses(self):
        cases = {
            'a photo with metadata': ('meta.jpg', phone_photo((800, 600))),
            'a photo too large': ('big.jpg', self.jpeg((2400, 1600))),
            'a file that is not a JPEG': ('fake.jpg', b'not a jpeg'),
        }
        for name, (file, data) in cases.items():
            with self.subTest(name):
                self.log.write_text(json.dumps(LOG), encoding='utf-8')
                for old in (self.public / 'images/log').iterdir():
                    old.unlink()
                self.add_entry(file, data)
                with self.assertRaises(build.BuildError):
                    self.site()

    def test_refuses_a_missing_photo(self):
        self.add_entry('gone.jpg', self.jpeg())
        (self.public / 'images/log/gone.jpg').unlink()
        with self.assertRaises(build.BuildError):
            self.site()

    def test_refuses_a_photo_no_entry_uses(self):
        (self.public / 'images/log/stray.jpg').write_bytes(self.jpeg())
        with self.assertRaises(build.BuildError):
            self.site()


if __name__ == '__main__':
    unittest.main()
