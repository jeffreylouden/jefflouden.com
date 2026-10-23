// Markdown for agents: requests for the home page that send
// `Accept: text/markdown` get public/index.md instead of HTML.
// Everything else falls through to the static site, with a fresh CSP nonce.
// Only "/" and "/index.html" reach this function (see public/_routes.json).

const LINK = [
  '</llms.txt>; rel="describedby"; type="text/markdown"',
  '</index.md>; rel="alternate"; type="text/markdown"',
  '</.well-known/ai-catalog.json>; rel="ai-catalog"; type="application/json"',
  '</sitemap.xml>; rel="sitemap"; type="application/xml"',
  '</humans.txt>; rel="author"',
].join(', ');

// _headers does not apply to Function responses, so repeat what "/" needs.
const SECURITY = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), browsing-topics=()',
  'Cross-Origin-Opener-Policy': 'same-origin',
};

// The same policy as `/*` in _headers, with a per-response nonce where that
// one carries the 404 page's script hash. Cloudflare reads the nonce from
// this header and stamps it on the scripts it injects at the edge (bot
// detection, Web Analytics), so they run without 'unsafe-inline'.
function csp(nonce) {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' https://static.cloudflareinsights.com`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self'",
    "connect-src 'self' https://cloudflareinsights.com",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
    'upgrade-insecure-requests',
  ].join('; ');
}

function withHeaders(response, extra) {
  const res = new Response(response.body, response);
  for (const [k, v] of Object.entries({ ...SECURITY, ...extra })) res.headers.set(k, v);
  res.headers.append('Vary', 'Accept');
  return res;
}

export async function onRequest({ request, next, env }) {
  const wantsMarkdown = /\btext\/markdown\b/i.test(request.headers.get('Accept') || '');

  if (wantsMarkdown) {
    const md = await env.ASSETS.fetch(new URL('/index.md', request.url));
    if (md.ok) {
      const body = await md.text();
      return withHeaders(new Response(request.method === 'HEAD' ? null : body), {
        'Content-Type': 'text/markdown; charset=utf-8',
        'x-markdown-tokens': String(Math.ceil(body.length / 4)),
        'Link': LINK,
      });
    }
  }

  // Never answer 304: a cached page would keep its old nonce under a new
  // header and every script on it would be refused.
  const headers = new Headers(request.headers);
  headers.delete('If-None-Match');
  headers.delete('If-Modified-Since');
  const page = await next(new Request(request, { headers }));

  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))));
  const res = withHeaders(page, {
    'Link': LINK,
    'Content-Security-Policy': csp(nonce),
    'Cache-Control': 'no-cache',
  });
  res.headers.delete('ETag');
  res.headers.delete('Last-Modified');

  return new HTMLRewriter()
    .on('script', { element(el) { el.setAttribute('nonce', nonce); } })
    .transform(res);
}
