<?php

/**
 * Clinical Co-Pilot auth-bridge: step 1 of 3 (agent-implementation.md decision #1).
 *
 * Kicks off a standard OAuth2 authorization_code + PKCE flow against this same OpenEMR instance's
 * own OAuth2 server. Because the browser already carries an authenticated OpenEMR session cookie,
 * hitting /oauth2/default/authorize reuses that login (no separate credential entry) -- the
 * clinician only sees the one-time scope-consent screen, not a second login. globals.php's own
 * bootstrap already enforces that a real OpenEMR session exists before this file's code runs.
 *
 * This endpoint is meant to be opened in a small popup/iframe by the chat widget the first time a
 * session needs (re-)authorization; callback.php closes the loop.
 *
 * The PKCE verifier and return_url are NOT stored in the shared OpenEMR session -- confirmed via
 * access-log analysis that the opener window/tab's background polling (dated_reminders_counter.php,
 * background_service/$run, both firing every ~10-20s against the same session) can read-then-write
 * a stale session snapshot in the middle of this popup's short life, silently clobbering whatever
 * this script and callback.php write/read in between (observed directly: a poll landed exactly
 * between this script's write and callback.php's read, and callback.php then failed with "invalid
 * or missing state/code"). Instead, the verifier + return_url are packed into the `state` param
 * itself and HMAC-signed with the client secret -- callback.php verifies the signature and decodes
 * them with no session dependency at all for this part of the flow.
 */

// Force the site explicitly rather than relying on $session->get('site_id') -- the OAuth2
// authorize/login/scope-confirm round-trip this script kicks off has been observed to leave the
// session without a site_id by the time the flow lands back on callback.php (same session-reliability
// class of issue as the state-param docstring above), which makes globals.php throw
// MissingSiteIdException (a Symfony BadRequestHttpException). OpenEMR's ErrorHandler renders any
// uncaught HttpExceptionInterface with an EMPTY body, so that surfaces to the browser as a bare,
// unhelpful 400 with no message. This is a single-site deployment, so 'default' is always correct.
$_GET['site'] = 'default';

require_once("../../globals.php");

require_once(__DIR__ . '/config.php');

// PKCE: random verifier + its S256 challenge (belt-and-suspenders even for a confidential client).
$codeVerifier = rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
$codeChallenge = rtrim(strtr(base64_encode(hash('sha256', $codeVerifier, true)), '+/', '-_'), '=');

// Pack verifier + return_url into the state param itself (see docstring above) instead of the
// session. Signed so a client can't tamper with the verifier or return_url.
$statePayload = [
    'v' => $codeVerifier,
    'r' => (string) ($_GET['return_url'] ?? ''),
    'n' => bin2hex(random_bytes(8)), // uniqueness/anti-caching only, not itself validated
];
$stateBody = rtrim(strtr(base64_encode(json_encode($statePayload)), '+/', '-_'), '=');
$state = $stateBody . '.' . hash_hmac('sha256', $stateBody, COPILOT_CLIENT_SECRET);

$params = [
    'response_type' => 'code',
    'client_id' => COPILOT_CLIENT_ID,
    'redirect_uri' => COPILOT_REDIRECT_URI,
    'scope' => COPILOT_SCOPE,
    'state' => $state,
    'code_challenge' => $codeChallenge,
    'code_challenge_method' => 'S256',
];

header('Location: ' . COPILOT_OAUTH_BASE . '/authorize?' . http_build_query($params));
exit;
