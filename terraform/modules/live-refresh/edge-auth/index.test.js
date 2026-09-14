'use strict';

// Unit tests for the Lambda@Edge Cognito auth handler. Run with `node --test`
// from this directory (or `node --test terraform/modules/live-refresh/edge-auth`).
// Fully offline: we generate an RSA keypair, publish it as the JWKS the handler
// verifies against (seeded via the _setJwksForTest hook), and sign our own
// tokens. No network, no real Cognito.
//
// The handler reads config.json from its own directory at require time, so we
// stage a copy of index.js + a synthetic config.json in a temp dir and require
// that, keeping the source tree clean and avoiding any bundled config.

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CFG = {
  issuer: 'https://issuer.example.com/pool',
  clientId: 'client-abc123',
  cognitoDomain: 'https://cog.example.com',
  region: 'us-east-1',
  clientSecret: 'shhh-secret',
  cloudfrontDomain: 'd123.cloudfront.net',
};

// Stage index.js + config.json in a temp dir and load the module from there.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'edge-auth-test-'));
fs.copyFileSync(path.join(__dirname, 'index.js'), path.join(tmp, 'index.js'));
fs.writeFileSync(path.join(tmp, 'config.json'), JSON.stringify(CFG));
const mod = require(path.join(tmp, 'index.js'));

// One RSA keypair for the whole suite; publish the public half as a JWKS.
const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', {
  modulusLength: 2048,
});
const KID = 'test-key-1';
const jwk = Object.assign(publicKey.export({ format: 'jwk' }), {
  kid: KID,
  alg: 'RS256',
  use: 'sig',
});
mod._setJwksForTest([jwk]);

function b64url(buf) {
  return Buffer.from(buf).toString('base64url');
}

function signToken(payload, opts = {}) {
  const header = { alg: opts.alg || 'RS256', kid: opts.kid || KID, typ: 'JWT' };
  const signingInput = b64url(JSON.stringify(header)) + '.' + b64url(JSON.stringify(payload));
  const sig = crypto.sign('RSA-SHA256', Buffer.from(signingInput), privateKey);
  return signingInput + '.' + b64url(sig);
}

function validPayload(overrides = {}) {
  const now = Math.floor(Date.now() / 1000);
  return Object.assign(
    {
      iss: CFG.issuer,
      aud: CFG.clientId,
      token_use: 'id',
      exp: now + 3600,
      iat: now,
      sub: 'user-1',
    },
    overrides
  );
}

test('verifyIdToken accepts a valid id token', async () => {
  const payload = await mod.verifyIdToken(signToken(validPayload()));
  assert.strictEqual(payload.sub, 'user-1');
});

test('verifyIdToken rejects an expired token', async () => {
  const token = signToken(validPayload({ exp: Math.floor(Date.now() / 1000) - 10 }));
  await assert.rejects(() => mod.verifyIdToken(token), /token expired/);
});

test('verifyIdToken rejects a bad audience', async () => {
  const token = signToken(validPayload({ aud: 'someone-else' }));
  await assert.rejects(() => mod.verifyIdToken(token), /bad audience/);
});

test('verifyIdToken rejects a bad issuer', async () => {
  const token = signToken(validPayload({ iss: 'https://evil.example.com' }));
  await assert.rejects(() => mod.verifyIdToken(token), /bad issuer/);
});

test('verifyIdToken rejects an access token (token_use != id)', async () => {
  const token = signToken(validPayload({ token_use: 'access' }));
  await assert.rejects(() => mod.verifyIdToken(token), /not an id token/);
});

test('verifyIdToken rejects a tampered signature', async () => {
  const token = signToken(validPayload());
  const tampered = token.slice(0, -4) + (token.slice(-4) === 'AAAA' ? 'BBBB' : 'AAAA');
  await assert.rejects(() => mod.verifyIdToken(tampered), /bad signature|error/i);
});

test('verifyIdToken rejects a malformed JWT', async () => {
  await assert.rejects(() => mod.verifyIdToken('not.a.jwt.at.all'), /malformed JWT/);
  await assert.rejects(() => mod.verifyIdToken('onlyonepart'), /malformed JWT/);
});

test('parseCookies handles multiple pairs and headers, ignores malformed', () => {
  const cookies = mod.parseCookies({
    cookie: [
      { value: 'CS360_AUTH=abc; CS360_STATE=xyz' },
      { value: 'other=1; broken' },
    ],
  });
  assert.strictEqual(cookies.CS360_AUTH, 'abc');
  assert.strictEqual(cookies.CS360_STATE, 'xyz');
  assert.strictEqual(cookies.other, '1');
  assert.strictEqual(cookies.broken, undefined);
});

test('parseCookies returns empty object when no cookie header', () => {
  assert.deepStrictEqual(mod.parseCookies({}), {});
});

test('redirect builds a 302 with location, no-store, and multiple Set-Cookie', () => {
  const resp = mod.redirect('https://x.example/', ['a=1; Path=/', 'b=2; Path=/']);
  assert.strictEqual(resp.status, '302');
  assert.strictEqual(resp.headers.location[0].value, 'https://x.example/');
  assert.match(resp.headers['cache-control'][0].value, /no-store/);
  assert.strictEqual(resp.headers['set-cookie'].length, 2);
});

test('trustedHost prefers the configured CloudFront domain over the Host header', () => {
  const host = mod.trustedHost({ headers: { host: [{ value: 'attacker.example' }] } });
  assert.strictEqual(host, CFG.cloudfrontDomain);
});

// ── Handler-level tests: CSRF gate + bearer injection ────────────────────────
// These drive exports.handler end-to-end. The paths exercised here never make a
// network call: verifyIdToken uses the seeded JWKS, and the CSRF-reject path
// returns before the token exchange. Bearer injection and pass-through use only
// the seeded JWKS.

function makeEvent(uri, opts = {}) {
  const headers = {};
  if (opts.cookies) headers.cookie = [{ value: opts.cookies }];
  return { Records: [{ cf: { request: { uri, querystring: opts.querystring || '', headers } } }] };
}

test('handler injects a Bearer token for /quota when the session cookie is valid', async () => {
  const token = signToken(validPayload());
  const event = makeEvent('/quota', { cookies: `CS360_AUTH=${token}` });
  const out = await mod.handler(event);
  // A valid session passes the request through (not a 302) with Authorization set.
  assert.ok(!out.status, 'should pass request through, not redirect');
  assert.strictEqual(out.headers.authorization[0].value, `Bearer ${token}`);
});

test('handler does NOT inject Authorization for the dashboard path', async () => {
  const token = signToken(validPayload());
  const event = makeEvent('/', { cookies: `CS360_AUTH=${token}` });
  const out = await mod.handler(event);
  assert.ok(!out.status, 'valid session passes through');
  assert.strictEqual(out.headers.authorization, undefined);
});

test('handler redirects to Cognito with a fresh state cookie when unauthenticated', async () => {
  const out = await mod.handler(makeEvent('/'));
  assert.strictEqual(out.status, '302');
  assert.match(out.headers.location[0].value, /\/oauth2\/authorize/);
  const setCookies = out.headers['set-cookie'].map((c) => c.value).join('\n');
  assert.match(setCookies, /CS360_STATE=/);
  assert.match(setCookies, /HttpOnly/);
});

test('handler rejects /callback when the OAuth state does not match (CSRF gate)', async () => {
  // code present, but returned state != state cookie -> must NOT exchange the
  // code; falls through to a fresh authorize redirect.
  const event = makeEvent('/callback', {
    querystring: 'code=abc&state=attacker',
    cookies: 'CS360_STATE=legit',
  });
  const out = await mod.handler(event);
  assert.strictEqual(out.status, '302');
  assert.match(out.headers.location[0].value, /\/oauth2\/authorize/);
});

test('handler rejects /callback when the state cookie is absent', async () => {
  const event = makeEvent('/callback', { querystring: 'code=abc&state=whatever' });
  const out = await mod.handler(event);
  assert.strictEqual(out.status, '302');
  assert.match(out.headers.location[0].value, /\/oauth2\/authorize/);
});

test('handler falls through to login when the session cookie is invalid/expired', async () => {
  const expired = signToken(validPayload({ exp: Math.floor(Date.now() / 1000) - 10 }));
  const out = await mod.handler(makeEvent('/', { cookies: `CS360_AUTH=${expired}` }));
  assert.strictEqual(out.status, '302');
  assert.match(out.headers.location[0].value, /\/oauth2\/authorize/);
});
