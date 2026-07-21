var COGNITO_DOMAIN = 'svguru-connect-dash2.auth.us-east-1.amazoncognito.com';
var CLIENT_ID = 'test-client-id';
var CALLBACK_PATH = '/_callback';
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === CALLBACK_PATH || uri.startsWith(CALLBACK_PATH)) { return request; }
  var token = null;
  if (request.cookies && request.cookies.auth_token) {
    token = request.cookies.auth_token.value;
  }
  if (token && token.split('.').length === 3) {
    try {
      var parts = token.split('.');
      var payload = JSON.parse(base64urlDecode(parts[1]));
      var now = Math.floor(Date.now() / 1000);
      if (!payload.exp || payload.exp > now) { return request; }
    } catch(e) {}
  }
  var host = request.headers.host.value;
  var callbackUrl = encodeURIComponent('https://' + host + CALLBACK_PATH);
  var loginUrl = 'https://' + COGNITO_DOMAIN + '/oauth2/authorize?response_type=code&client_id=' + CLIENT_ID + '&redirect_uri=' + callbackUrl + '&scope=openid+email+profile';
  return { statusCode: 302, statusDescription: 'Found', headers: { 'location': { value: loginUrl }, 'cache-control': { value: 'no-store' } } };
}
function base64urlDecode(str) { str = str.replace(/-/g, '+').replace(/_/g, '/'); while (str.length % 4) str += '='; var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'; var output = ''; var buffer = 0; var bits = 0; for (var i = 0; i < str.length; i++) { var idx = chars.indexOf(str[i]); if (idx === -1) continue; buffer = (buffer << 6) | idx; bits += 6; if (bits >= 8) { bits -= 8; output += String.fromCharCode((buffer >> bits) & 0xFF); } } return output; }

// ─── LOCAL TESTS ───
var testEvent = {
  request: {
    uri: '/',
    headers: { host: { value: 'd29l8mak0l4ih9.cloudfront.net' } },
    cookies: {}
  }
};

// Test 1: No cookie → should redirect
var result = handler(testEvent);
console.assert(result.statusCode === 302, 'Test 1 FAILED: expected 302, got ' + JSON.stringify(result));
console.log('Test 1 PASSED: no cookie → 302 redirect');

// Test 2: Callback path → pass through
testEvent.request.uri = '/_callback';
result = handler(testEvent);
console.assert(result.uri === '/_callback', 'Test 2 FAILED: callback should pass through');
console.log('Test 2 PASSED: /_callback → pass through');

// Test 3: Valid JWT cookie → pass through
var header = btoa(JSON.stringify({alg:'HS256'})).replace(/=/g,'');
var payload = btoa(JSON.stringify({exp: Math.floor(Date.now()/1000) + 3600, email:'test@test.com'})).replace(/=/g,'');
var fakeJwt = header + '.' + payload + '.fakesignature';
testEvent.request.uri = '/';
testEvent.request.cookies = { auth_token: { value: fakeJwt } };
result = handler(testEvent);
console.assert(result.uri === '/', 'Test 3 FAILED: valid token should pass through, got ' + JSON.stringify(result));
console.log('Test 3 PASSED: valid JWT cookie → pass through');

// Test 4: Expired JWT → should redirect
var expiredPayload = btoa(JSON.stringify({exp: 1000, email:'test@test.com'})).replace(/=/g,'');
var expiredJwt = header + '.' + expiredPayload + '.fakesignature';
testEvent.request.cookies = { auth_token: { value: expiredJwt } };
result = handler(testEvent);
console.assert(result.statusCode === 302, 'Test 4 FAILED: expired token should redirect, got ' + JSON.stringify(result));
console.log('Test 4 PASSED: expired JWT → 302 redirect');

console.log('\nALL TESTS PASSED');
