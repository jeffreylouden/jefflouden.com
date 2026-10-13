// Markdown for agents: requests for the home page that send
// `Accept: text/markdown` get public/index.md instead of HTML.
// Everything else falls through to the static site untouched.
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
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), browsing-topics=()',
};

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

  return withHeaders(await next(), { 'Link': LINK });
}
