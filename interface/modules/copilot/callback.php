<?php

/**
 * Clinical Co-Pilot auth-bridge: step 2 of 3 (agent-implementation.md decision #1).
 *
 * Receives the authorization_code redirect from OpenEMR's own OAuth2 server, exchanges it
 * server-side for tokens (client_secret never leaves the server), and stores the resulting
 * access/refresh tokens in the PHP session -- the browser/JS never sees them directly. See
 * proxy.php, which is what the chat widget actually calls per-message.
 *
 * The PKCE verifier + return_url arrive packed into the signed `state` param (see start.php's
 * docstring) rather than the shared OpenEMR session, since that session was observed to be
 * silently clobbered mid-flow by the opener window/tab's background polling.
 */

// This script writes the resulting access/refresh tokens to session -- without this, globals.php
// defaults to read-only/read_and_close session mode and that write would be silently discarded.
$sessionAllowWrite = true;

require_once("../../globals.php");

use GuzzleHttp\Client;
use GuzzleHttp\Exception\RequestException;
use OpenEMR\Common\Session\SessionWrapperFactory;

require_once(__DIR__ . '/config.php');

$session = SessionWrapperFactory::getInstance()->getActiveSession();

function copilot_deny(string $message): never
{
    http_response_code(400);
    header('Content-Type: text/html');
    echo '<p>Clinical Co-Pilot authorization failed: ' . htmlspecialchars($message) . '</p>';
    exit;
}

/** Verifies the state param's HMAC signature and decodes its packed payload, or returns null. */
function copilot_decode_state(string $state): ?array
{
    $parts = explode('.', $state, 2);
    if (count($parts) !== 2) {
        return null;
    }
    [$body, $signature] = $parts;
    if (!hash_equals(hash_hmac('sha256', $body, COPILOT_CLIENT_SECRET), $signature)) {
        return null;
    }
    $json = base64_decode(strtr($body, '-_', '+/'), true);
    $payload = $json === false ? null : json_decode($json, true);
    return is_array($payload) ? $payload : null;
}

$state = $_GET['state'] ?? '';
$code = $_GET['code'] ?? '';
$statePayload = $state === '' ? null : copilot_decode_state($state);

if (empty($code) || $statePayload === null) {
    copilot_deny('invalid or missing state/code');
}
$codeVerifier = (string) ($statePayload['v'] ?? '');
$returnUrl = (string) ($statePayload['r'] ?? '');
if ($codeVerifier === '') {
    copilot_deny('missing PKCE verifier in state');
}

$client = new Client(['timeout' => 15, 'connect_timeout' => 5]);

try {
    $resp = $client->post(COPILOT_OAUTH_BASE . '/token', [
        'form_params' => [
            'grant_type' => 'authorization_code',
            'code' => $code,
            'redirect_uri' => COPILOT_REDIRECT_URI,
            'client_id' => COPILOT_CLIENT_ID,
            'client_secret' => COPILOT_CLIENT_SECRET,
            'code_verifier' => $codeVerifier,
        ],
    ]);
} catch (RequestException $exc) {
    copilot_deny('token exchange failed: ' . $exc->getMessage());
}

$tokens = json_decode((string) $resp->getBody(), true);
if (empty($tokens['access_token'])) {
    copilot_deny('token endpoint returned no access_token');
}

$session->set('copilot_access_token', $tokens['access_token']);
$session->set('copilot_refresh_token', $tokens['refresh_token'] ?? null);
$session->set('copilot_token_expires_at', time() + (int) ($tokens['expires_in'] ?? 3600));

// Popup flow: tell the opener window we're done, then close ourselves. Fall back to a plain
// redirect if this wasn't opened as a popup (window.opener is null).
?>
<!DOCTYPE html>
<html>
<body>
<script>
  if (window.opener) {
    window.opener.postMessage({ type: 'copilot-authorized' }, window.location.origin);
    window.close();
  } else if (<?= json_encode($returnUrl !== '') ?>) {
    window.location.href = <?= json_encode($returnUrl) ?>;
  } else {
    document.body.textContent = 'Clinical Co-Pilot is now authorized. You can close this window.';
  }
</script>
</body>
</html>
