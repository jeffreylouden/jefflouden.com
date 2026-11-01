// Tests for functions/_middleware.js. Run with `node --test tests/middleware.test.mjs`.
// Cloudflare's runtime is stood in for by the files in public/, a next() that
// serves public/index.html, and an HTMLRewriter that stamps script tags.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

// The middleware is an ES module in a folder without a package.json, so load its source as one.
const { onRequest } = await import('data:text/javascript,' + encodeURIComponent(await read('functions/_middleware.js')));

globalThis.HTMLRewriter = class {
  on(selector, handlers) {
    this.handlers = handlers;
    return this;
  }
  async transform(res) {
    let nonce;
    this.handlers.element({ setAttribute: (name, value) => { nonce = value; } });
    const body = (await res.text()).replace(/<script\b/g, `<script nonce="${nonce}"`);
    return new Response(body, res);
  }
};

async function request(headers = {}, method = 'GET') {
  const seen = {};
  const env = {
    ASSETS: {
      async fetch(url) {
        const path = new URL(url).pathname;
        if (path === '/index.md' || path === '/index.txt') return new Response(await read('public' + path));
        return new Response('not found', { status: 404 });
      },
    },
  };
  const next = async (req) => {
    seen.headers = req.headers;
    return new Response(await read('public/index.html'), {
      headers: { 'Content-Type': 'text/html', 'ETag': '"abc"', 'Last-Modified': 'Fri, 23 Oct 2026 00:00:00 GMT' },
    });
  };
  const res = await onRequest({ request: new Request('https://jefflouden.com/', { method, headers }), next, env });
  return { res, body: await res.text(), seen };
}

const BROWSER = 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Safari/605.1.15';

test('Accept: text/markdown gets index.md', async () => {
  const { res, body } = await request({ 'Accept': 'text/markdown', 'User-Agent': BROWSER });
  assert.equal(body, await read('public/index.md'));
  assert.equal(res.headers.get('Content-Type'), 'text/markdown; charset=utf-8');
  assert.equal(res.headers.get('x-markdown-tokens'), String(Math.ceil(body.length / 4)));
  assert.match(res.headers.get('Link'), /<\/llms\.txt>; rel="describedby"/);
  assert.match(res.headers.get('Link'), /<\/llms-full\.txt>; rel="describedby"/);
  assert.match(res.headers.get('Link'), /<\/log\.md>; rel="describedby"/);
});

test('HEAD for Markdown has no body', async () => {
  const { res, body } = await request({ 'Accept': 'text/markdown' }, 'HEAD');
  assert.equal(body, '');
  assert.equal(res.headers.get('Content-Type'), 'text/markdown; charset=utf-8');
});

for (const agent of ['curl/8.7.1', 'Wget/1.21.4', 'HTTPie/3.2.2', 'xh/0.22.0']) {
  test(`${agent} gets the terminal page`, async () => {
    const { res, body } = await request({ 'User-Agent': agent, 'Accept': '*/*' });
    assert.equal(body, await read('public/index.txt'));
    assert.equal(res.headers.get('Content-Type'), 'text/plain; charset=utf-8');
  });
}

test('curl asking for HTML gets HTML', async () => {
  const { body } = await request({ 'User-Agent': 'curl/8.7.1', 'Accept': 'text/html' });
  assert.match(body, /^<!doctype html>/);
});

test('text browsers get HTML', async () => {
  const { body } = await request({ 'User-Agent': 'Lynx/2.9.0 libwww-FM/2.14' });
  assert.match(body, /^<!doctype html>/);
});

test('HTML gets a fresh nonce on every script and in the CSP', async () => {
  const a = await request({ 'User-Agent': BROWSER, 'Accept': 'text/html' });
  const b = await request({ 'User-Agent': BROWSER, 'Accept': 'text/html' });
  const nonce = a.res.headers.get('Content-Security-Policy').match(/'nonce-([^']+)'/)[1];
  assert.notEqual(nonce, b.res.headers.get('Content-Security-Policy').match(/'nonce-([^']+)'/)[1]);
  const scripts = a.body.match(/<script\b[^>]*>/g);
  assert.ok(scripts.length >= 3);
  for (const tag of scripts) assert.ok(tag.includes(`nonce="${nonce}"`), tag);
  assert.doesNotMatch(a.res.headers.get('Content-Security-Policy'), /unsafe-inline'[^;]*script|script-src[^;]*unsafe-inline/);
});

test('HTML is never answered from a cache that would hold an old nonce', async () => {
  const { res, seen } = await request({ 'User-Agent': BROWSER, 'If-None-Match': '"abc"', 'If-Modified-Since': 'Fri, 23 Oct 2026 00:00:00 GMT' });
  assert.equal(seen.headers.get('If-None-Match'), null);
  assert.equal(seen.headers.get('If-Modified-Since'), null);
  assert.equal(res.headers.get('ETag'), null);
  assert.equal(res.headers.get('Last-Modified'), null);
  assert.equal(res.headers.get('Cache-Control'), 'no-cache');
});

test('every answer carries the security headers from _headers', async () => {
  const headers = (await read('public/_headers')).split('\n/fonts/*')[0];
  const expected = [...headers.matchAll(/^  ([\w-]+): (.+)$/gm)].filter(([, k]) => k !== 'Content-Security-Policy');
  assert.ok(expected.length >= 6);
  for (const accept of ['text/markdown', 'text/html']) {
    const { res } = await request({ 'Accept': accept, 'User-Agent': BROWSER });
    for (const [, k, v] of expected) assert.equal(res.headers.get(k), v, `${k} for ${accept}`);
    assert.match(res.headers.get('Vary'), /Accept, User-Agent/);
  }
});

test('the HTML policy matches the static one but for the nonce', async () => {
  const { res } = await request({ 'User-Agent': BROWSER });
  const fromHeaders = (await read('public/_headers')).match(/Content-Security-Policy: (.+)/)[1].replace(/ ?'sha256-[^']+'/g, '');
  assert.equal(res.headers.get('Content-Security-Policy').replace(/ ?'nonce-[^']+'/, ''), fromHeaders);
});
