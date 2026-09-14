'use strict';

// Cognito OAuth2 authorization-code viewer-request handler for CloudFront
// Lambda@Edge. Vanilla Node.js 20 - no npm dependencies (built-in https +
// crypto only), because Lambda@Edge cannot install packages at deploy time
// beyond what is bundled and cannot use environment variables.
//
// Config is read from a bundled config.json (rendered by Terraform via
// templatefile from the Cognito user pool / app-client outputs). Lambda@Edge
// forbids environment variables, so the Cognito settings are baked into the
// deployment bundle instead.
//
// Flow (one CloudFront viewer-request per browser request):
//   1. Valid session cookie (a Cognito id_token) present and verifiable
//      (RS256 via JWKS, checked exp/iss/aud) -> pass the request to the origin.
//   2. Request to /callback carrying a `code` query param -> exchange the code
//      at the Cognito token endpoint, set an httpOnly Secure cookie holding the
//      id_token, and 302 back to '/'.
//   3. Otherwise -> 302 to the Cognito hosted-UI /oauth2/authorize endpoint.

const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

// config.json is rendered by Terraform (from config.json.tftpl) into the
// deployment bundle, so it is always present at runtime. It is intentionally
// not committed to the repo. Guard the read so importing this module without a
// config.json (unit tests, or any tooling that loads the file) does not throw
// at import time and abort the process; the unit tests require a staged copy
// that has its own config.json, and Lambda@Edge always ships one.
let CONFIG = {};
try {
  CONFIG = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8')
  );
} catch (e) {
  console.error('edge-auth: config.json not readable at import:', e.message);
}

// Name of the session cookie that carries the Cognito id_token.
const SESSION_COOKIE = 'CS360_AUTH';

// Short-lived cookie holding the OAuth `state` value, set before redirecting to
// the hosted UI and verified on /callback to prevent login CSRF.
const STATE_COOKIE = 'CS360_STATE';

// JWKS is cached in module scope so it survives across warm invocations of the
// same edge container (signing keys rotate rarely; a cold container re-fetches).
let jwksCache = null;

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, (res) => {
        let body = '';
        res.on('data', (chunk) => {
          body += chunk;
        });
        res.on('end', () => {
          if (res.statusCode < 200 || res.statusCode >= 300) {
            reject(new Error('GET ' + url + ' -> HTTP ' + res.statusCode));
            return;
          }
          resolve(body);
        });
      })
      .on('error', reject);
  });
}

function httpsPostForm(urlString, formObj, extraHeaders) {
  return new Promise((resolve, reject) => {
    const u = new URL(urlString);
    const data = Object.keys(formObj)
      .map((k) => encodeURIComponent(k) + '=' + encodeURIComponent(formObj[k]))
      .join('&');
    const options = {
      hostname: u.hostname,
      port: 443,
      path: u.pathname + u.search,
      method: 'POST',
      headers: Object.assign(
        {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Content-Length': Buffer.byteLength(data),
        },
        extraHeaders || {}
      ),
    };
    const req = https.request(options, (res) => {
      let body = '';
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(
            new Error(
              'POST ' + urlString + ' -> HTTP ' + res.statusCode + ' ' + body
            )
          );
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (e) {
          reject(new Error('token endpoint returned non-JSON: ' + e.message));
        }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function getJwks(forceRefetch) {
  if (jwksCache && !forceRefetch) {
    return jwksCache;
  }
  const raw = await httpsGet(CONFIG.issuer + '/.well-known/jwks.json');
  jwksCache = JSON.parse(raw).keys;
  return jwksCache;
}

function base64UrlDecode(str) {
  return Buffer.from(str, 'base64url');
}

// Verifies a Cognito id_token: RS256 signature against the pool JWKS, plus the
// exp / iss / aud claims. Throws on any failure; the caller treats a throw as
// "not authenticated" and falls through to the login redirect.
async function verifyIdToken(token) {
  const parts = token.split('.');
  if (parts.length !== 3) {
    throw new Error('malformed JWT');
  }
  const header = JSON.parse(base64UrlDecode(parts[0]).toString('utf8'));
  const payload = JSON.parse(base64UrlDecode(parts[1]).toString('utf8'));

  let keys = await getJwks();
  let jwk = keys.find((k) => k.kid === header.kid);
  if (!jwk) {
    // The signing key may have rotated since we cached the JWKS; refetch once
    // before rejecting, so a rotation does not make us reject valid tokens.
    keys = await getJwks(true);
    jwk = keys.find((k) => k.kid === header.kid);
  }
  if (!jwk) {
    throw new Error('no matching JWK for kid ' + header.kid);
  }

  const pubKey = crypto.createPublicKey({ key: jwk, format: 'jwk' });
  const signingInput = parts[0] + '.' + parts[1];
  const signature = base64UrlDecode(parts[2]);
  const verified = crypto.verify(
    'RSA-SHA256',
    Buffer.from(signingInput),
    pubKey,
    signature
  );
  if (!verified) {
    throw new Error('bad signature');
  }

  const now = Math.floor(Date.now() / 1000);
  if (typeof payload.exp === 'number' && now >= payload.exp) {
    throw new Error('token expired');
  }
  if (payload.iss !== CONFIG.issuer) {
    throw new Error('bad issuer');
  }
  if (payload.aud !== CONFIG.clientId) {
    throw new Error('bad audience');
  }
  if (payload.token_use !== 'id') {
    throw new Error('not an id token');
  }
  return payload;
}

function parseCookies(headers) {
  const out = {};
  const cookieHeaders = headers.cookie || [];
  for (const h of cookieHeaders) {
    for (const pair of h.value.split(';')) {
      const idx = pair.indexOf('=');
      if (idx === -1) {
        continue;
      }
      const k = pair.slice(0, idx).trim();
      const v = pair.slice(idx + 1).trim();
      out[k] = v;
    }
  }
  return out;
}

function redirect(location, setCookieValues) {
  const headers = {
    location: [{ key: 'Location', value: location }],
    'cache-control': [
      { key: 'Cache-Control', value: 'no-cache, no-store, max-age=0' },
    ],
  };
  const cookies = [].concat(setCookieValues || []).filter(Boolean);
  if (cookies.length) {
    headers['set-cookie'] = cookies.map((v) => ({ key: 'Set-Cookie', value: v }));
  }
  return {
    status: '302',
    statusDescription: 'Found',
    headers: headers,
  };
}

function hostOf(request) {
  return request.headers.host[0].value;
}

// The Host header CloudFront passes to a viewer-request Lambda@Edge is the
// client-controlled Host from the incoming request, not a value CloudFront
// itself verifies. Trusting it to build redirect_uri/authorize/callback URLs
// would let a request with a forged Host header steer the OAuth redirect
// (and, via the Cognito allowed-callback-URL check, a spoofed request would
// simply be rejected there - but building URLs off attacker input is still
// the wrong default). Prefer the known dashboard domain baked into the
// deployment bundle (CONFIG.cloudfrontDomain, from var.cloudfront_domain);
// only fall back to the Host header when that config value is empty (e.g.
// phase 1 of the two-phase apply, before the CloudFront domain is known).
function trustedHost(request) {
  if (CONFIG.cloudfrontDomain) {
    return CONFIG.cloudfrontDomain;
  }
  return hostOf(request);
}

exports.handler = async (event) => {
  const request = event.Records[0].cf.request;
  const host = trustedHost(request);
  // redirect_uri must match a registered Cognito callback URL exactly; derive
  // it from the configured dashboard domain (falling back to the Host header
  // only when that config is not yet set).
  const redirectUri = 'https://' + host + '/callback';

  // 1. Already authenticated? Verify the session cookie and pass through.
  const cookies = parseCookies(request.headers);
  const idToken = cookies[SESSION_COOKIE];
  if (idToken) {
    try {
      await verifyIdToken(idToken);
      // Requests routed to the API Gateway origin (the /quota behavior, see
      // cloudfront.tf) carry no auth of their own besides this header - the
      // COGNITO_USER_POOLS authorizer on GET /quota expects a bearer token,
      // not the session cookie. Inject it here so the browser's same-origin
      // fetch("/quota") is authenticated without embedding the token in page
      // JavaScript. /callback and the dashboard path never match this and
      // fall through unchanged. Never log the token.
      if (request.uri === '/quota' || request.uri.indexOf('/quota') === 0) {
        request.headers.authorization = [
          { key: 'Authorization', value: 'Bearer ' + idToken },
        ];
      }
      return request;
    } catch (e) {
      // Invalid/expired token: fall through to re-authenticate. Log a
      // reason string (never the token) so on-call can alarm on a spike.
      console.error('auth: id_token verification failed:', e.message);
    }
  }

  // 2. Callback from the hosted UI carrying an authorization code?
  if (request.uri.indexOf('/callback') === 0) {
    const params = new URLSearchParams(request.querystring || '');
    const code = params.get('code');
    const returnedState = params.get('state');
    const expectedState = cookies[STATE_COOKIE];
    // Verify the OAuth state matches the value we set before the login
    // redirect. A missing or mismatched state means the callback was not
    // initiated by us (login CSRF); reject it and start a fresh, stateful login.
    if (code && returnedState && expectedState && returnedState === expectedState) {
      try {
        const basic = Buffer.from(
          CONFIG.clientId + ':' + CONFIG.clientSecret
        ).toString('base64');
        const tokenResp = await httpsPostForm(
          CONFIG.cognitoDomain + '/oauth2/token',
          {
            grant_type: 'authorization_code',
            client_id: CONFIG.clientId,
            code: code,
            redirect_uri: redirectUri,
          },
          { Authorization: 'Basic ' + basic }
        );
        const sessionCookie =
          SESSION_COOKIE +
          '=' +
          tokenResp.id_token +
          '; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=3600';
        // Clear the one-time state cookie now that it has been consumed.
        const clearState =
          STATE_COOKIE + '=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0';
        return redirect('https://' + host + '/', [sessionCookie, clearState]);
      } catch (e) {
        // Token exchange failed: fall through to a fresh authorize redirect.
        // Log a reason string (never the code, token, or client secret) so
        // on-call can alarm on a spike.
        console.error('auth: token exchange failed:', e.message);
      }
    } else if (code) {
      console.error('auth: OAuth state missing or mismatched on callback');
    }
  }

  // 3. No/invalid session -> send the viewer to the Cognito hosted UI with a
  // fresh CSRF state value, stored in a short-lived cookie for verification on
  // the callback above.
  const state = crypto.randomBytes(16).toString('hex');
  const stateCookie =
    STATE_COOKIE +
    '=' +
    state +
    '; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600';
  const authorizeUrl =
    CONFIG.cognitoDomain +
    '/oauth2/authorize' +
    '?client_id=' +
    encodeURIComponent(CONFIG.clientId) +
    '&response_type=code' +
    '&scope=openid+email' +
    '&state=' +
    encodeURIComponent(state) +
    '&redirect_uri=' +
    encodeURIComponent(redirectUri);
  return redirect(authorizeUrl, stateCookie);
};

// Exported for unit tests only. Lambda@Edge invokes `exports.handler`; these
// extra exports are inert at runtime and let the test suite exercise the pure
// helpers offline (seeding the JWKS avoids any network call).
exports.parseCookies = parseCookies;
exports.verifyIdToken = verifyIdToken;
exports.redirect = redirect;
exports.trustedHost = trustedHost;
exports._setJwksForTest = (keys) => {
  jwksCache = keys;
};
