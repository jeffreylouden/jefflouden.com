#!/usr/bin/env python3
"""Builds the site from src/ into public/.

    python3 build.py           write every page that changed
    python3 build.py --check   change nothing, and fail if any page is stale

The facts live in src/site.json and src/log.json, checked against the schemas
beside them. The page shells live in src/templates, the pieces they share in
src/partials. The built files are committed, so the site keeps working, and
stays editable by hand, without this script. It uses only the Python standard
library so it should still run in 20 years.

Templates know four tags: {{name}} for a value, {{> name}} for a partial,
{{! a comment }} that never reaches the page, and nothing else. A tag alone on
its line indents every line of what it inserts to match.
"""

import base64
import datetime
import hashlib
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'src'
PUBLIC = ROOT / 'public'

# Roles that started before this year share the home page's "Earlier" row.
EARLIER_BEFORE = 2020
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
          'August', 'September', 'October', 'November', 'December']


class BuildError(Exception):
    pass


# Schema checks: the subset of JSON Schema the two schemas use.

def validate(value, schema, root, path='$'):
    if '$ref' in schema:
        ref = root
        for part in schema['$ref'].lstrip('#/').split('/'):
            ref = ref[part]
        return validate(value, ref, root, path)
    if 'oneOf' in schema:
        errors = []
        for option in schema['oneOf']:
            try:
                validate(value, option, root, path)
                return
            except BuildError as e:
                errors.append(str(e))
        raise BuildError(f'{path} matches none of its shapes:\n  ' + '\n  '.join(errors))
    if 'const' in schema and value != schema['const']:
        raise BuildError(f'{path} must be {schema["const"]!r}')
    if 'enum' in schema and value not in schema['enum']:
        raise BuildError(f'{path} must be one of {schema["enum"]}')
    kind = schema.get('type')
    types = {'string': str, 'integer': int, 'boolean': bool, 'array': list, 'object': dict, 'null': type(None)}
    if kind and not (isinstance(value, types[kind]) and not (kind == 'integer' and isinstance(value, bool))):
        raise BuildError(f'{path} must be a {kind}')
    if 'minLength' in schema and len(value) < schema['minLength']:
        raise BuildError(f'{path} must not be empty')
    if 'pattern' in schema and not re.search(schema['pattern'], value):
        raise BuildError(f'{path} ({value!r}) does not match {schema["pattern"]}')
    if kind == 'array':
        if len(value) < schema.get('minItems', 0):
            raise BuildError(f'{path} needs at least {schema["minItems"]} item(s)')
        for i, item in enumerate(value):
            validate(item, schema.get('items', {}), root, f'{path}[{i}]')
    if kind == 'object':
        for key in schema.get('required', []):
            if key not in value:
                raise BuildError(f'{path} is missing {key!r}')
        props = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for key, item in value.items():
            if key in props:
                validate(item, props[key], root, f'{path}.{key}')
            elif extra is False:
                raise BuildError(f'{path} has an unknown key {key!r}')
            elif isinstance(extra, dict):
                validate(item, extra, root, f'{path}.{key}')


def load(name):
    data = json.loads((SRC / f'{name}.json').read_text(encoding='utf-8'))
    schema = json.loads((SRC / f'{name}.schema.json').read_text(encoding='utf-8'))
    validate(data, schema, schema, name)
    return data


# Photos. The log's JPEGs are checked here with no image library: the build
# reads their size from the frame header and refuses any that still carry
# metadata (EXIF, XMP or IPTC, where a phone keeps the place a photo was taken).

LOG_IMAGES = PUBLIC / 'images' / 'log'
IMAGE_MAX_SIDE = 1600
IMAGE_MAX_BYTES = 600_000


def jpeg_info(data):
    """Returns (width, height, has_metadata) for a baseline or progressive JPEG."""
    if data[:2] != b'\xff\xd8':
        raise BuildError('not a JPEG')
    pos, size, metadata = 2, None, False
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise BuildError('a broken JPEG')
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], 'big')
        if marker in (0xE1, 0xED):
            metadata = True
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[pos + 5:pos + 7], 'big')
            width = int.from_bytes(data[pos + 7:pos + 9], 'big')
            size = (width, height)
        if marker == 0xDA:
            break
        pos += 2 + length
    if not size:
        raise BuildError('a JPEG with no frame size')
    return size[0], size[1], metadata


# Templates.

TAG = re.compile(r'\{\{(!.*?|>\s*[\w-]+|\s*\w+\s*)\}\}', re.S)


def render(template, context):
    out, pos = [], 0
    for m in TAG.finditer(template):
        before = template[pos:m.start()]
        line_start = before.rfind('\n') + 1
        indent = before[line_start:]
        tag = m.group(1).strip()
        end = m.end()
        if tag.startswith('!'):
            # A comment alone on its line takes the line with it.
            if not indent.strip() and template[end:end + 1] == '\n':
                before, end = before[:line_start], end + 1
            out.append(before)
            pos = end
            continue
        if tag.startswith('>'):
            value = partial(tag[1:].strip(), context)
        elif tag in context:
            value = str(context[tag])
        else:
            raise BuildError(f'no value for {{{{{tag}}}}}')
        if not indent.strip():
            value = value.replace('\n', '\n' + indent)
            value = re.sub(r'\n[ \t]+(?=\n|$)', '\n', value)
        out.append(before + value)
        pos = end
    out.append(template[pos:])
    return ''.join(out)


def partial(name, context):
    return render((SRC / 'partials' / f'{name}.html').read_text(encoding='utf-8'), context).rstrip('\n')


def template(name, context):
    return render((SRC / 'templates' / name).read_text(encoding='utf-8'), context)


# Text helpers. Text fields may carry [text](url) links.

LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def attr(s):
    return esc(s).replace('"', '&quot;')


def html(s):
    parts, pos = [], 0
    for m in LINK.finditer(s):
        parts.append(esc(s[pos:m.start()]))
        parts.append(f'<a href="{attr(m.group(2))}">{esc(m.group(1))}</a>')
        pos = m.end()
    parts.append(esc(s[pos:]))
    return ''.join(parts)


def plain(s):
    return LINK.sub(r'\1', s)


def with_urls(s):
    return LINK.sub(r'\1 (\2)', s)


def join_and(items):
    items = list(items)
    if len(items) < 3:
        return ' and '.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def years(start, end):
    first = start[:4]
    if end is None:
        return f'{first} – Now'
    return first if end[:4] == first else f'{first} – {end[:4]}'


def years_html(start, end):
    first = f'<time datetime="{start}">{start[:4]}</time>'
    if end is None:
        return f'{first} – Now'
    if end[:4] == start[:4]:
        return first
    return f'{first} – <time datetime="{end}">{end[:4]}</time>'


def month_long(ym):
    return f'{MONTHS[int(ym[5:]) - 1]} {ym[:4]}'


def dates_long(start, end):
    return f'{month_long(start)} to {"present" if end is None else month_long(end)}'


def entry_ids(entries):
    """A stable anchor per log entry: its date and a slug of what it is. Numbered
    from the oldest, so a new entry never renames an old one. The page, the feed
    and the Bluesky posts all use these."""
    ids, seen = {}, {}
    for i in reversed(range(len(entries))):
        e = entries[i]
        what = e.get('title') or e.get('quote') or e.get('image', {}).get('alt') or e.get('text', '')
        words = re.sub(r'[^a-z0-9]+', '-', plain(what).lower()).strip('-')[:40].rstrip('-') or e['kind']
        base = f'{e["date"]}-{words}'
        seen[base] = seen.get(base, 0) + 1
        ids[i] = base if seen[base] == 1 else f'{base}-{seen[base]}'
    return [ids[i] for i in range(len(entries))]


# The site.

class Site:
    def __init__(self, site, log):
        self.site, self.log = site, log
        self.p = site['profile']
        self.orgs = site['organizations']
        self.products = site['products']
        self.roles = site['roles']
        self.links = site['links']
        self.check()
        lead = next(r for r in self.roles if r['end'] is None)
        self.job_title = plain(lead['title'])
        self.job_org = join_and(self.orgs[k]['name'] for k in lead['orgs'])
        self.headline = f'{self.job_title} at {self.job_org}'
        self.title = f'{self.p["name"]}, {self.headline}'
        self.year = datetime.date.today().year
        entries = log['entries']
        self.log_lastmod = entries[0]['date'] if entries else site['updated'][:10]
        self.home_lastmod = site['updated'][:10]
        self.base = {
            'name': self.p['name'],
            'headline': self.headline,
            'email': self.p['email'],
            'url': self.p['url'],
            'location': self.p['location']['display'],
            'year': self.year,
            'bluesky': next(l['url'] for l in self.links if l['url'].startswith('https://bsky.app/')),
        }

    def check(self):
        for i, role in enumerate(self.roles):
            for key in role['orgs']:
                if key not in self.orgs:
                    raise BuildError(f'roles[{i}] names an unknown organization {key!r}')
            if role.get('built') and role['built'] not in self.products:
                raise BuildError(f'roles[{i}] built an unknown product {role["built"]!r}')
            if role['end'] and role['end'] < role['start']:
                raise BuildError(f'roles[{i}] ends before it starts')
        if [r['start'] for r in self.roles] != sorted((r['start'] for r in self.roles), reverse=True):
            raise BuildError('roles must be newest first')
        if not any(r['end'] is None for r in self.roles):
            raise BuildError('no current role to take the headline from')
        for key, product in self.products.items():
            if product['publisher'] not in self.orgs:
                raise BuildError(f'products.{key} has an unknown publisher')
        dates = [e['date'] for e in self.log['entries']]
        if dates != sorted(dates, reverse=True):
            raise BuildError('log entries must be newest first')
        self.check_images()
        styles = (SRC / 'templates' / 'index.html').read_text(encoding='utf-8')
        for brand in {o['brand'] for o in self.orgs.values()} | {l['brand'] for l in self.links if 'brand' in l}:
            if f'[data-brand="{brand}"]' not in styles:
                raise BuildError(f'no data-brand="{brand}" rule in src/templates/index.html')
        # Jeff doesn't use semicolons in his writing, and titles never use a separator dot.
        copy = [self.p[k] for k in ('bio', 'resumeBio', 'summary', 'description', 'personDescription', 'about')]
        copy += [r.get(k, '') for r in self.roles for k in ('title', 'summary', 'resume')]
        copy += [e.get(k, '') for e in self.log['entries'] for k in ('title', 'text', 'quote', 'who', 'place')]
        copy += [e['image']['alt'] for e in self.log['entries'] if 'image' in e]
        for s in copy:
            if ';' in s or '·' in s:
                raise BuildError(f'no semicolons or middle dots in the copy: {s!r}')

    def check_images(self):
        self.images = {}
        for e in self.log['entries']:
            if 'image' not in e:
                continue
            src = e['image']['src']
            path = PUBLIC / src
            if not path.is_file():
                raise BuildError(f'log photo public/{src} is missing')
            data = path.read_bytes()
            if len(data) > IMAGE_MAX_BYTES:
                raise BuildError(f'public/{src} is {len(data) // 1000} KB, over {IMAGE_MAX_BYTES // 1000}. Prepare it with scripts/log_photo.py')
            try:
                width, height, metadata = jpeg_info(data)
            except BuildError as err:
                raise BuildError(f'public/{src} is {err}') from None
            if metadata:
                raise BuildError(f'public/{src} still carries metadata, which can hold where it was taken. Prepare it with scripts/log_photo.py')
            if max(width, height) > IMAGE_MAX_SIDE:
                raise BuildError(f'public/{src} is {width}x{height}, over {IMAGE_MAX_SIDE}px. Prepare it with scripts/log_photo.py')
            self.images[src] = (width, height)
        if LOG_IMAGES.is_dir():
            for path in sorted(LOG_IMAGES.iterdir()):
                src = path.relative_to(PUBLIC).as_posix()
                if src not in self.images:
                    raise BuildError(f'public/{src} is in no log entry. Delete it or add its entry')

    def names(self, role):
        return join_and(self.orgs[k]['name'] for k in role['orgs'])

    def org_id(self, key):
        return self.orgs[key].get('id', f'https://jefflouden.com/#org-{key}')

    def org_links(self, role, brand=True):
        out = []
        for key in role['orgs']:
            o = self.orgs[key]
            extra = f' data-brand="{o["brand"]}"' if brand else ''
            out.append(f'<a href="{attr(o["url"])}"{extra}>{esc(o["name"])}</a>')
        return join_and(out)

    def groups(self):
        """Consecutive roles at the same organization, which share a row."""
        out = []
        for role in self.roles:
            if out and out[-1][0]['orgs'] == role['orgs']:
                out[-1].append(role)
            else:
                out.append([role])
        return out

    def built(self, role):
        return self.products[role['built']] if role.get('built') else None

    # The home page.

    def work_html(self):
        rows, earlier = [], []
        for group in self.groups():
            first, last = group[-1], group[0]
            if int(first['start'][:4]) < EARLIER_BEFORE:
                earlier.append(group[0])
                continue
            titles = []
            for role in group:
                product = self.built(role)
                if product:
                    lockup = SRC / 'partials' / f'{role["built"]}.html'
                    if lockup.exists():
                        mark = partial(role['built'], self.base)
                        link = f'<a class="{role["built"]}" href="{attr(product["url"])}" aria-label="{attr(product["name"])}">{mark}</a>'
                        titles.append(f'<p class="title built">{html(role["title"])}, built {link}</p>')
                    else:
                        titles.append(f'<p class="title">{html(role["title"])}, built <a href="{attr(product["url"])}">{esc(product["name"])}</a></p>')
                else:
                    titles.append(f'<p class="title">{html(role["title"])}</p>')
            body = '\n'.join(['<div>', f'  <p class="org">{self.org_links(last)}</p>'] + [f'  {t}' for t in titles] + ['</div>'])
            rows.append('\n'.join([
                '<li class="row">',
                f'  <span class="years">{years_html(first["start"], last["end"])}</span>',
                textwrap.indent(body, '  '),
                '</li>',
            ]))
        if earlier:
            items = '\n'.join(f'    <li>{self.org_links(r)}</li>' for r in earlier)
            rows.append(f'<li class="row">\n  <span class="years">Earlier</span>\n  <ul>\n{items}\n  </ul>\n</li>')
        return '\n'.join(rows)

    def jsonld(self):
        person = 'https://jefflouden.com/#person'
        current, past = [], []
        for role in self.roles:
            for key in role['orgs']:
                (current if role['end'] is None else past).append(key)
        current = list(dict.fromkeys(current))
        past = [k for k in dict.fromkeys(past) if k not in current]

        def same_as(o):
            out = []
            if o.get('wikidata'):
                out.append(f'https://www.wikidata.org/wiki/{o["wikidata"]}')
            if o.get('wikipedia'):
                out.append(f'https://en.wikipedia.org/wiki/{o["wikipedia"]}')
            return out

        def org(key, node):
            o = self.orgs[key]
            d = {'@type': 'Organization'}
            if node:
                d['@id'] = self.org_id(key)
            d['name'] = o['name']
            if o.get('legalName'):
                d['legalName'] = o['legalName']
            d['url'] = o['url']
            if same_as(o):
                d['sameAs'] = same_as(o)
            if node and o.get('founder'):
                d['founder'] = {'@id': person}
            if node and o.get('foundingDate'):
                d['foundingDate'] = o['foundingDate']
            return d

        loc = self.p['location']
        graph = [
            {
                '@type': 'ProfilePage',
                '@id': 'https://jefflouden.com/#profile',
                'url': self.p['url'],
                'name': self.title,
                'inLanguage': 'en-US',
                'dateModified': self.site['updated'],
                'isPartOf': {'@id': 'https://jefflouden.com/#website'},
                'mainEntity': {'@id': person},
                'primaryImageOfPage': {'@type': 'ImageObject', 'url': 'https://jefflouden.com/og.png', 'width': 1200, 'height': 630},
            },
            {
                '@type': 'WebSite',
                '@id': 'https://jefflouden.com/#website',
                'url': self.p['url'],
                'name': self.p['name'],
                'inLanguage': 'en-US',
                'publisher': {'@id': person},
                'copyrightHolder': {'@id': person},
                'copyrightYear': 2009,
            },
            {
                '@type': 'Person',
                '@id': person,
                'name': self.p['name'],
                'alternateName': [self.p['alternateName']],
                'givenName': self.p['givenName'],
                'familyName': self.p['familyName'],
                'url': self.p['url'],
                'mainEntityOfPage': {'@id': 'https://jefflouden.com/#profile'},
                'description': self.p['personDescription'],
                'jobTitle': self.job_title,
                'hasOccupation': {
                    '@type': 'Occupation',
                    'name': self.p['occupation'],
                    'occupationLocation': {'@type': 'City', 'name': loc['display']},
                },
                'worksFor': [{'@id': self.org_id(k)} for k in current],
                'alumniOf': [org(k, False) for k in past],
                'knowsAbout': self.p['knowsAbout'],
                'email': f'mailto:{self.p["email"]}',
                'image': {'@type': 'ImageObject', 'url': 'https://jefflouden.com/jeffrey-louden-square.jpg', 'width': 800, 'height': 800},
                'address': {'@type': 'PostalAddress', 'addressLocality': loc['locality'], 'addressRegion': loc['region'], 'addressCountry': loc['country']},
                'birthDate': self.p['birthDate'],
                'subjectOf': {'@type': 'DigitalDocument', 'name': f'{self.p["name"]} résumé', 'url': 'https://jefflouden.com/resume.pdf', 'encodingFormat': 'application/pdf'},
                'sameAs': [l['url'] for l in self.links],
            },
        ]
        graph += [org(k, True) for k in current]
        for product in self.products.values():
            graph.append({
                '@type': 'SoftwareApplication',
                '@id': product['url'].rstrip('/') + '/#software',
                'name': product['name'],
                'url': product['url'],
                'author': {'@id': person},
                'publisher': {'@id': self.org_id(product['publisher'])},
            })
        return dump_json({'@context': 'https://schema.org', '@graph': graph})

    def index_html(self):
        rel_me = [f'<link rel="me" href="{attr(l["url"])}">' for l in self.links]
        rel_me.append(f'<link rel="me" href="mailto:{attr(self.p["email"])}">')
        contact = []
        for l in self.links:
            if l['onPage']:
                brand = f' data-brand="{l["brand"]}"' if l.get('brand') else ''
                contact.append(f'<li><a class="u-url" href="{attr(l["url"])}" rel="me"{brand}>{esc(l["label"])}</a></li>')
        share = f'{self.p["bio"]} {self.p["summary"]}.'
        return template('index.html', {
            **self.base,
            'title': attr(self.title),
            'description': attr(self.p['description']),
            'share': attr(share),
            'image_alt': attr(f'{self.title}. {self.p["bio"]}'),
            'given_name': attr(self.p['givenName']),
            'family_name': attr(self.p['familyName']),
            'username': attr(self.p['username']),
            'rel_me': '\n'.join(rel_me),
            'job_title': esc(self.job_title),
            'job_org': esc(self.job_org),
            'bio': esc(self.p['bio']),
            'work': self.work_html(),
            'contact': '\n'.join(contact),
            'jsonld': self.jsonld(),
        })

    # The log and the 404 page.

    def entry_ids(self):
        return entry_ids(self.log['entries'])

    def entry_lines(self, e, root=''):
        """An entry's inner HTML. The feed passes the site's root so its links work anywhere."""
        lines = []
        if e['kind'] == 'quote':
            lines.append(f'<blockquote><p>“{html(e["quote"])}”</p></blockquote>')
            lines.append(f'<p class="who">{html(e["who"])}</p>')
        elif e.get('title'):
            title = f'<a href="{attr(e["url"])}">{esc(e["title"])}</a>' if e.get('url') else esc(e['title'])
            lines.append(f'<p class="what">{title}</p>')
        if e.get('image'):
            src = e['image']['src']
            width, height = self.images[src]
            img = f'<img src="{root}/{src}" alt="{attr(e["image"]["alt"])}" width="{width}" height="{height}" loading="lazy" decoding="async">'
            # With no title to carry it, a link goes on the photo itself.
            if e.get('url') and not e.get('title'):
                img = f'<a class="photo" href="{attr(e["url"])}">{img}</a>'
            lines.append(img)
        if e.get('text'):
            lines.append(f'<p>{html(e["text"])}</p>')
        if e.get('place'):
            lines.append(f'<p class="place">{esc(e["place"])}</p>')
        return lines

    def log_html(self):
        items = []
        for e, anchor in zip(self.log['entries'], self.entry_ids()):
            body = '\n'.join(f'  {l}' for l in self.entry_lines(e))
            items.append(f'<li id="{anchor}" data-kind="{attr(e["kind"])}" data-date="{e["date"]}">\n{body}\n</li>')
        return template('log.html', {**self.base, 'entries': '\n'.join(items)})

    def log_feed(self):
        """The log as an Atom feed. Every date is the day an entry was added, so the
        feed only changes when the log does."""
        root = self.p['url'].rstrip('/')
        entries = []
        for e, anchor in list(zip(self.log['entries'], self.entry_ids()))[:50]:
            if e['kind'] == 'quote':
                title = f'“{plain(e["quote"])}”'
            else:
                title = plain(e.get('title') or e.get('image', {}).get('alt') or e.get('text', ''))
            content = ''.join(self.entry_lines(e, root))
            related = f'\n  <link rel="related" href="{attr(e["url"])}"/>' if e.get('url') else ''
            entries.append(f'''<entry>
  <id>tag:jefflouden.com,{e["date"]}:log/{anchor}</id>
  <title>{esc(title)}</title>
  <link rel="alternate" type="text/html" href="{root}/log#{anchor}"/>{related}
  <published>{e["date"]}T00:00:00Z</published>
  <updated>{e["date"]}T00:00:00Z</updated>
  <content type="html">{esc(content)}</content>
</entry>''')
        newest = max((e['date'] for e in self.log['entries']), default=self.home_lastmod)
        return template('log.xml', {
            **self.base,
            'root': root,
            'updated': f'{newest}T00:00:00Z',
            'entries': '\n'.join(entries),
        })

    def not_found_html(self):
        return template('404.html', self.base)

    # Plain text for agents and terminals.

    def index_md(self):
        work = []
        for role in self.roles:
            line = f'- **{self.names(role)}** ({years(role["start"], role["end"])}): {plain(role["title"])}.'
            if role.get('summary'):
                line += f' {with_urls(role["summary"])}'
            work.append(f'{line} {self.orgs[role["orgs"][-1]]["url"]}')
        links = [f'- {l["label"]}: {l["url"]}' for l in self.links if l['onPage']]
        return template('index.md', {**self.base, 'bio': self.p['bio'], 'work': '\n'.join(work), 'links': '\n'.join(links)})

    def index_txt(self):
        rows, earlier = [], []
        for group in self.groups():
            first, last = group[-1], group[0]
            if int(first['start'][:4]) < EARLIER_BEFORE:
                earlier.append(self.names(last))
                continue
            lead = f'  {years(first["start"], last["end"]):<14}{self.names(last):<34}'
            for i, role in enumerate(group):
                title = plain(role['title'])
                product = self.built(role)
                suffix = f', built {product["name"]}' if product else ''
                # Keep under 80 columns: drop a title's trailing ", part" until it fits.
                while len(lead) + len(title + suffix) > 80 and ', ' in title:
                    title = title.rsplit(', ', 1)[0]
                rows.append((lead if i == 0 else ' ' * len(lead)) + title + suffix)
        if earlier:
            # One line per 80 columns, breaking between names, never inside one.
            line = '  Earlier       '
            for i, name in enumerate(earlier):
                name += ',' if i < len(earlier) - 1 else ''
                if len(line) > 16 and len(line) + 1 + len(name) > 80:
                    rows.append(line)
                    line = ' ' * 16
                line += ('' if len(line) == 16 else ' ') + name
            rows.append(line)
        links = [f'  {l["label"].lower():<11}{l["url"]}' for l in self.links if l['onPage']]
        return template('index.txt', {**self.base, 'bio': self.p['bio'], 'work': '\n'.join(rows), 'links': '\n'.join(links)})

    def llms_txt(self):
        work = []
        for role in self.roles:
            line = f'- {self.names(role)}: {plain(role["title"])} ({dates_long(role["start"], role["end"])}).'
            if role.get('location'):
                loc = role['location']
                line += f' {loc}' + ('' if loc.endswith('.') else '.')
            if role.get('summary'):
                line += f' {with_urls(role["summary"])}'
            work.append(f'{line} {self.orgs[role["orgs"][-1]]["url"]}')
        education = []
        for e in self.site['education']:
            if e['onResume']:
                school = e['school'] + (f' ({e["parent"]})' if e.get('parent') else '')
                education.append(f'- {school}: {e["credential"]} ({e["start"]} to {e["end"]})')
        links = [f'- {l["label"]}: {l["url"]}' for l in self.links]
        return template('llms.txt', {
            **self.base,
            'about': self.p['about'],
            'work': '\n'.join(work),
            'education': '\n'.join(education),
            'links': '\n'.join(links),
        })

    def humans_txt(self):
        user, domain = self.p['email'].split('@')
        links = [f'{l["label"] + ":":<16}{l["url"]}' for l in self.links if l['onPage']]
        return template('humans.txt', {
            **self.base,
            'email_spoken': f'{user} [at] {domain.replace(".", " [dot] ")}',
            'links': '\n'.join(links),
            'country': self.p['location']['countryName'],
            'hometown': self.p['hometown'],
            'last_update': max(self.home_lastmod, self.log_lastmod),
        })

    def sitemap_xml(self):
        return template('sitemap.xml', {'home_lastmod': self.home_lastmod, 'log_lastmod': self.log_lastmod})

    def ai_catalog(self):
        q = lambda items: ',\n'.join(json.dumps(i, ensure_ascii=False) for i in items)
        return template('ai-catalog.json', {
            **self.base,
            'alternate_name': self.p['alternateName'],
            'summary': self.p['summary'],
            'profile_queries': q(self.site['catalog']['profile']),
            'home_queries': q(self.site['catalog']['home']),
        })

    # The résumé and the contact card.

    def resume_html(self):
        work = []
        for role in self.roles:
            lines = [f'<p class="org">{self.org_links(role, brand=False)}</p>', f'<p class="title">{html(role["title"])}</p>']
            if role.get('resume'):
                lines.append(f'<p class="detail">{html(role["resume"])}</p>')
            work.append(self.resume_row(years(role['start'], role['end']), lines))
        education = []
        for e in self.site['education']:
            if e['onResume']:
                education.append(self.resume_row(f'{e["start"]} – {e["end"]}', [f'<p class="org">{esc(e["school"])}</p>', f'<p class="title">{esc(e["credential"])}</p>']))
        return template('resume.html', {**self.base, 'bio': esc(self.p['resumeBio']), 'work': '\n'.join(work), 'education': '\n'.join(education)})

    @staticmethod
    def resume_row(when, lines):
        body = '\n'.join(f'    {l}' for l in lines)
        return f'<li class="row">\n  <span class="years">{when}</span>\n  <div>\n{body}\n  </div>\n</li>'

    def vcard(self):
        v = lambda s: s.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;')
        loc = self.p['location']
        photo = base64.b64encode((SRC / 'vcard-photo.jpg').read_bytes()).decode()
        lines = [
            'BEGIN:VCARD',
            'VERSION:3.0',
            f'N:{v(self.p["familyName"])};{v(self.p["givenName"])};;;',
            f'FN:{v(self.p["name"])}',
            f'ORG:{v(self.job_org)}',
            f'TITLE:{v(self.job_title)}',
            f'EMAIL;TYPE=INTERNET,PREF:{self.p["email"]}',
            f'URL:{self.p["url"]}',
            f'ADR;TYPE=WORK:;;;{v(loc["locality"])};{v(loc["region"])};;{v(loc["country"])}',
            f'PHOTO;ENCODING=b;TYPE=JPEG:{photo}',
            'END:VCARD',
        ]
        folded = []
        for line in lines:
            folded.append(line[:75])
            folded += [' ' + line[i:i + 74] for i in range(75, len(line), 74)]
        return '\r\n'.join(folded) + '\r\n'

    # Headers, for _headers and the middleware.

    def headers(self, pages):
        hashes = []
        for page in pages:
            for script in re.findall(r'<script>(.*?)</script>', page, re.S):
                h = "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
                if h not in hashes:
                    hashes.append(h)
        sec = self.site['security']
        csp = '; '.join(d.replace('{scripts}', ' '.join(hashes)) for d in sec['csp'])
        lines = [f'{k}: {v}' for k, v in sec['headers'].items()] + [f'Content-Security-Policy: {csp}']
        return template('_headers', {'security_headers': '\n'.join(lines)})

    def middleware(self, current):
        sec = self.site['security']

        def js(s):
            if '{scripts}' in s:
                return '`' + s.replace('{scripts}', "'nonce-${nonce}'") + '`'
            return f'"{s}"' if "'" in s else f"'{s}'"

        block = ['const SECURITY = {']
        block += [f"  '{k}': {js(v)}," for k, v in sec['headers'].items()]
        block += ['};', '', 'function csp(nonce) {', '  return [']
        block += [f'    {js(d)},' for d in sec['csp']]
        block += ["  ].join('; ');", '}']
        start, end = '// BEGIN GENERATED\n', '// END GENERATED\n'
        if current.count(start) != 1 or current.count(end) != 1:
            raise BuildError('functions/_middleware.js lost its GENERATED markers')
        head, rest = current.split(start)
        _, tail = rest.split(end)
        return head + start + '\n'.join(block) + '\n' + end + tail


def dump_json(value, level=0, inline_items=False):
    """JSON laid out for reading: short values on one line, longer ones open."""
    pad, inner = '  ' * level, '  ' * (level + 1)
    if isinstance(value, dict):
        flat = '{ ' + ', '.join(f'{json.dumps(k)}: {dump_json(v, 0, True)}' for k, v in value.items()) + ' }'
        if inline_items or len(flat) + len(pad) <= 110:
            return flat
        items = [f'{inner}{json.dumps(k)}: {dump_json(v, level + 1, False)}' for k, v in value.items()]
        return '{\n' + ',\n'.join(items) + '\n' + pad + '}'
    if isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            flat = '[' + ', '.join(json.dumps(v, ensure_ascii=False) for v in value) + ']'
            if inline_items or len(flat) + len(pad) <= 110:
                return flat
        nodes = level <= 1  # The @graph's nodes open up, everything deeper stays one per line.
        items = [inner + dump_json(v, level + 1, not nodes) for v in value]
        return '[\n' + ',\n'.join(items) + '\n' + pad + ']'
    return json.dumps(value, ensure_ascii=False)


def security_txt_expiry(text, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    expires = datetime.datetime.fromisoformat(re.search(r'^Expires: (\S+)', text, re.M).group(1).replace('Z', '+00:00'))
    left = (expires - now).days
    if left < 90:
        raise BuildError(f'public/.well-known/security.txt expires in {left} days. Push Expires forward a year.')


def build():
    site = Site(load('site'), load('log'))
    out = {}
    out['public/404.html'] = site.not_found_html()
    out['public/log.html'] = site.log_html()
    out['public/log.xml'] = site.log_feed()
    out['public/index.html'] = site.index_html()
    out['public/index.md'] = site.index_md()
    out['public/index.txt'] = site.index_txt()
    out['public/llms.txt'] = site.llms_txt()
    out['public/humans.txt'] = site.humans_txt()
    out['public/sitemap.xml'] = site.sitemap_xml()
    out['public/.well-known/ai-catalog.json'] = site.ai_catalog()
    out['public/jeffrey-louden.vcf'] = site.vcard()
    out['public/.well-known/atproto-did'] = site.p['atprotoDid']
    out['resume/resume.html'] = site.resume_html()
    out['public/_headers'] = site.headers([out['public/404.html'], out['public/log.html']])
    out['functions/_middleware.js'] = site.middleware((ROOT / 'functions/_middleware.js').read_text(encoding='utf-8'))

    # Checks on what came out.
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', out['public/index.html'], re.S).group(1)
    json.loads(ld)
    json.loads(out['public/.well-known/ai-catalog.json'])
    for n, line in enumerate(out['public/index.txt'].splitlines(), 1):
        if len(line) > 80:
            raise BuildError(f'public/index.txt line {n} is {len(line)} columns, over 80')
    for path, text in out.items():
        if path != 'functions/_middleware.js' and '·' in text:
            raise BuildError(f'{path} has a middle dot')
    security_txt_expiry((PUBLIC / '.well-known' / 'security.txt').read_text(encoding='utf-8'))
    return out


def main():
    check = '--check' in sys.argv[1:]
    try:
        out = build()
    except BuildError as e:
        sys.exit(f'build.py: {e}')
    stale = []
    for path, text in out.items():
        target = ROOT / path
        data = text.encode('utf-8')
        if target.exists() and target.read_bytes() == data:
            continue
        stale.append(path)
        if not check:
            target.write_bytes(data)
    if check:
        if stale:
            sys.exit('build.py: out of date, run python3 build.py:\n  ' + '\n  '.join(stale))
        print('build.py: everything is up to date')
        return
    for path in stale:
        print(f'wrote {path}')
    if not stale:
        print('build.py: nothing changed')
    if 'resume/resume.html' in stale:
        print('resume/resume.html changed: run resume/build.sh to render public/resume.pdf')


if __name__ == '__main__':
    main()
