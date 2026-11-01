#!/usr/bin/env python3
"""Builds the site from src/ into public/.

    python3 build.py           write every page that changed
    python3 build.py --check   change nothing, and fail if any page is stale

The facts live in src/site.json, the log in src/log.json and Arsenal's matches
in src/arsenal.json, each checked against the schema beside it. The
page shells live in src/templates, the pieces they share in src/partials. The built files are committed, so the site keeps working, and
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


# Schema checks: the subset of JSON Schema the schemas use.

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


# The site.

class Site:
    def __init__(self, site, log, arsenal=None):
        self.site, self.log = site, log
        self.arsenal = arsenal if arsenal is not None else load('arsenal')
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
        self.home_lastmod = site['updated'][:10]
        self.base = {
            'name': self.p['name'],
            'headline': self.headline,
            'email': self.p['email'],
            'url': self.p['url'],
            'location': self.p['location']['display'],
            'year': self.year,
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
        dates = [e['date'] for e in self.log['entries']]
        if dates != sorted(dates, reverse=True):
            raise BuildError('log entries must be newest first')
        kickoffs = [m['kickoff'] for m in self.arsenal['matches']]
        if kickoffs != sorted(kickoffs):
            raise BuildError('Arsenal matches must be soonest first')
        if not any(r['end'] is None for r in self.roles):
            raise BuildError('no current role to take the headline from')
        for key, product in self.products.items():
            if product['publisher'] not in self.orgs:
                raise BuildError(f'products.{key} has an unknown publisher')
        styles = (SRC / 'templates' / 'index.html').read_text(encoding='utf-8')
        for brand in {o['brand'] for o in self.orgs.values()} | {l['brand'] for l in self.links if 'brand' in l}:
            if f'[data-brand="{brand}"]' not in styles:
                raise BuildError(f'no data-brand="{brand}" rule in src/templates/index.html')
        # Jeff doesn't use semicolons in his writing, and titles never use a separator dot.
        copy = [self.p[k] for k in ('bio', 'resumeBio', 'summary', 'description', 'personDescription', 'about')]
        copy += [r.get(k, '') for r in self.roles for k in ('title', 'summary', 'resume')]
        copy += [p.get('description', '') for p in self.products.values()]
        copy += [e.get(k, '') for e in self.log['entries'] for k in ('title', 'text', 'quote', 'who', 'shows', 'place')]
        for s in copy:
            if ';' in s or '·' in s:
                raise BuildError(f'no semicolons or middle dots in the copy: {s!r}')

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
            'matches': self.matches_js(),
        })

    def matches_js(self):
        """Arsenal's matches for the page's script: kickoff (UTC), opponent, home, competition, one per line."""
        rows = [json.dumps([m['kickoff'], m['opponent'], m['home'], m['competition']], ensure_ascii=False).replace('<', '\\u003c')
                for m in self.arsenal['matches']]
        return '[\n' + ',\n'.join(rows) + '\n]' if rows else '[]'

    # The 404 page.

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
        links = [f'- {l["label"]}: {l["url"]}' for l in self.links]
        return template('llms.txt', {
            **self.base,
            'about': self.p['about'],
            'work': '\n'.join(work),
            'education': '\n'.join(self.education_lines()),
            'links': '\n'.join(links),
        })

    def education_lines(self):
        out = []
        for e in self.site['education']:
            if e['onResume']:
                school = e['school'] + (f' ({e["parent"]})' if e.get('parent') else '')
                out.append(f'- {school}: {e["credential"]} ({e["start"]} to {e["end"]})')
        return out

    def llms_full_txt(self):
        """The long profile for agents, unlinked from the site. See its template."""
        def role_text(role):
            product = self.built(role)
            # The résumé line is the fuller one. The summary stands in where there is none.
            text = with_urls(role.get('resume') or role.get('summary') or '')
            if product and product['url'] not in text:
                text = f'Built {product["name"]} ({product["url"]}). {text}'.strip()
            return text

        career = []
        for role in self.roles:
            where = f', {role["location"].rstrip(".")}' if role.get('location') else ''
            entry = [f'### {self.names(role)}: {plain(role["title"])}', '', f'{dates_long(role["start"], role["end"])}{where}.']
            if role_text(role):
                entry.append(role_text(role))
            entry.append(' '.join(self.orgs[k]['url'] for k in role['orgs']))
            career.append('\n'.join(entry))
        lead = next(r for r in self.roles if r['end'] is None)
        faq = [
            (f'Who is {self.p["name"]}?', self.p['about']),
            (f'Is {self.p["alternateName"]} the same person as {self.p["name"]}?', f'Yes. {self.p["alternateName"]} is the short form of his name, and both name the person at {self.p["url"]}'),
            (f'What does he do at {self.job_org}?', f'He has been {self.job_title} at {self.job_org} since {month_long(lead["start"])}.' + (f' His résumé puts it this way: "{plain(lead["resume"])}"' if lead.get('resume') else '')),
        ]
        for role in self.roles:
            product = self.built(role)
            if product:
                what = f'{product["name"]} ({product["url"]}) is {product["description"]}' if product.get('description') else f'{product["name"]} is at {product["url"]}.'
                faq.append((f'What is {product["name"]}?', f'{what} {self.p["name"]} built it at {self.names(role)}, which he founded in {month_long(role["start"])}.'))
        faq.append((f'How do I contact {self.p["alternateName"]}?', f'Email {self.p["email"]}. His résumé is at {self.p["url"]}resume.pdf'))
        return template('llms-full.txt', {
            **self.base,
            'about': self.p['about'],
            'alternate_name': self.p['alternateName'],
            'hometown': self.p['hometown'],
            'resume_bio': self.p['resumeBio'],
            'career': '\n\n'.join(career),
            'education': '\n'.join(self.education_lines()),
            'faq': '\n\n'.join(f'### {q}\n\n{a}' for q, a in faq),
            'links': '\n'.join(f'- {l["label"]}: {l["url"]}' for l in self.links),
        })

    def log_md(self):
        entries = []
        for e in self.log['entries']:
            kind = e['kind']
            if kind == 'quote':
                head = f'Quote from {e["who"]}'
            elif kind == 'note':
                head = 'Note'
            elif kind == 'photo':
                head = f'Photo: {e.get("title") or e["shows"]}'
            else:
                head = e['title']
            day = datetime.date.fromisoformat(e['date'])
            facts = [f'- Kind: {kind}', f'- Logged: {MONTHS[day.month - 1]} {day.day}, {day.year}']
            if kind == 'photo' and e.get('title'):
                facts.append(f'- Shows: {e["shows"]}')
            if e.get('url'):
                facts.append(f'- Link: {e["url"]}')
            if e.get('place'):
                facts.append(f'- Place: {e["place"]}')
            body = [f'## {head}', '', '\n'.join(facts)]
            if kind == 'quote':
                body += ['', f'> “{e["quote"]}”']
            if e.get('text'):
                body += ['', f'In his words: {with_urls(e["text"])}']
            entries.append('\n'.join(body))
        return template('log.md', {
            **self.base,
            'short_name': self.p['alternateName'].split()[0],
            'entries': '\n\n'.join(entries) if entries else 'Nothing logged yet.',
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
            'last_update': self.home_lastmod,
        })

    def sitemap_xml(self):
        return template('sitemap.xml', {'home_lastmod': self.home_lastmod})

    def ai_catalog(self):
        q = lambda items: ',\n'.join(json.dumps(i, ensure_ascii=False) for i in items)
        return template('ai-catalog.json', {
            **self.base,
            'alternate_name': self.p['alternateName'],
            'summary': self.p['summary'],
            'profile_queries': q(self.site['catalog']['profile']),
            'home_queries': q(self.site['catalog']['home']),
            'full_queries': q(self.site['catalog']['full']),
            'log_queries': q(self.site['catalog']['log']),
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
    site = Site(load('site'), load('log'), load('arsenal'))
    out = {}
    out['public/404.html'] = site.not_found_html()
    out['public/index.html'] = site.index_html()
    out['public/index.md'] = site.index_md()
    out['public/index.txt'] = site.index_txt()
    out['public/llms.txt'] = site.llms_txt()
    out['public/llms-full.txt'] = site.llms_full_txt()
    out['public/log.md'] = site.log_md()
    out['public/humans.txt'] = site.humans_txt()
    out['public/sitemap.xml'] = site.sitemap_xml()
    out['public/.well-known/ai-catalog.json'] = site.ai_catalog()
    out['public/jeffrey-louden.vcf'] = site.vcard()
    out['resume/resume.html'] = site.resume_html()
    out['public/_headers'] = site.headers([out['public/404.html']])
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
    # The agent files are Jeff's writing too, hand-written parts and all.
    for path in ('public/llms.txt', 'public/llms-full.txt', 'public/log.md', 'public/index.md'):
        if ';' in out[path]:
            raise BuildError(f'{path} has a semicolon')
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
