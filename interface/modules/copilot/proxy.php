<?php

/**
 * Clinical Co-Pilot auth-bridge: step 3 of 3 (agent-implementation.md decision #1).
 *
 * The only endpoint the chat widget's JS actually calls per message. Runs inside the clinician's
 * real, already-authenticated OpenEMR session -- re-checks ACL here regardless of what the calling
 * page already checked, resolves the patient's FHIR id server-side (JS only ever knows the
 * OpenEMR-native `pid`, never the FHIR uuid or any OAuth token), and forwards the request to the
 * Python agent service with a real bearer token attached server-side. The browser never sees the
 * access/refresh tokens or the FHIR API directly -- this is what "authorization inheritance, not
 * reimplementation" (see agent/app/fhir_client.py) looks like end-to-end.
 */

// This script writes refreshed access/refresh tokens back to session on proactive renewal --
// without this, globals.php defaults to read-only/read_and_close session mode and that write
// would be silently discarded, forcing a spurious reauth on every request past token expiry.
$sessionAllowWrite = true;

require_once("../../globals.php");

use GuzzleHttp\Client;
use GuzzleHttp\Exception\RequestException;
use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;

require_once(__DIR__ . '/config.php');

header('Content-Type: application/json');

// The agent call below can legitimately take longer than PHP's default 30s max_execution_time
// (multiple tool calls + LLM latency, especially on a rich chart). Without raising this, PHP's own
// execution-time limit kills the script with a raw, uncatchable fatal error (HTML output) before
// Guzzle's own per-request timeout can throw a normal, already-handled RequestException -- this was
// observed directly (`Maximum execution time of 30 seconds exceeded ... CurlHandler.php`), and its
// raw HTML broke the widget's JSON.parse(). Keep this comfortably above the /chat call's own Guzzle
// timeout below so Guzzle's catchable timeout fires first.
set_time_limit(75);

function copilot_fail(int $status, string $error): never
{
    http_response_code($status);
    echo json_encode(['error' => $error]);
    exit;
}

if (!AclMain::aclCheckCore('patients', 'med')) {
    copilot_fail(403, 'not authorized to view patient records');
}

$body = json_decode(file_get_contents('php://input'), true);
if (!is_array($body)) {
    copilot_fail(400, 'invalid JSON body');
}

$session = SessionWrapperFactory::getInstance()->getActiveSession();

if (!CsrfUtils::verifyCsrfToken($body['csrf_token'] ?? '', session: $session)) {
    // The widget's embedded token can go stale if the shared OpenEMR session cookie rotates in the
    // background (e.g. another frame's heartbeat/reminders poll triggers a session id change) while
    // the chat panel sits open. Rather than dead-ending the clinician, hand back a fresh token bound
    // to whatever session actually served this request so the widget can silently retry once.
    http_response_code(403);
    echo json_encode([
        'error' => 'invalid_csrf',
        'csrf_token' => CsrfUtils::collectCsrfToken(session: $session),
    ]);
    exit;
}

$pid = (int) ($body['pid'] ?? 0);
$message = trim((string) ($body['message'] ?? ''));
$conversationHistory = $body['conversation_history'] ?? [];
// Citation Contract's required click-to-source visual overlay: a document uploaded via
// upload.php is persisted immediately, but a citation from it only carries a bbox when the
// intake-extractor worker processes it *within* a chat turn (graph.py's pending_document path),
// not from the standalone /ingest call upload.php makes. widget.php stashes the just-uploaded
// file and attaches it here as pending_document on the clinician's next question, so that
// question's answer can cite specific fields with a clickable source -- not just a citation-less
// "extracted N results" summary. See widget.php's pendingDocumentForChat for the JS side.
$pendingDocument = $body['pending_document'] ?? null;
if ($pid <= 0 || $message === '') {
    copilot_fail(400, 'pid and message are required');
}

// Resolve the OpenEMR-native pid to its FHIR Patient uuid -- the JS side never sees or handles
// FHIR ids directly, only the pid it already has from the page it's embedded in.
$patientRow = sqlQuery('SELECT `uuid` FROM `patient_data` WHERE `pid` = ?', [$pid]);
if (empty($patientRow['uuid'])) {
    copilot_fail(404, 'patient not found or has no FHIR uuid assigned yet');
}
$patientUuid = UuidRegistry::uuidToString($patientRow['uuid']);

$accessToken = $session->get('copilot_access_token');
$expiresAt = (int) ($session->get('copilot_token_expires_at') ?? 0);
$refreshToken = $session->get('copilot_refresh_token');

$httpClient = new Client(['timeout' => 30, 'connect_timeout' => 5]);

// Refresh proactively if expired/expiring within 60s; a hard failure here means the clinician
// needs to go through start.php again (session-bridge has no stored password to fall back on).
if (!empty($accessToken) && $expiresAt < (time() + 60) && !empty($refreshToken)) {
    try {
        $refreshResp = $httpClient->post(COPILOT_OAUTH_BASE . '/token', [
            'form_params' => [
                'grant_type' => 'refresh_token',
                'refresh_token' => $refreshToken,
                'client_id' => COPILOT_CLIENT_ID,
                'client_secret' => COPILOT_CLIENT_SECRET,
                'scope' => COPILOT_SCOPE,
            ],
        ]);
        $refreshed = json_decode((string) $refreshResp->getBody(), true);
        if (!empty($refreshed['access_token'])) {
            $accessToken = $refreshed['access_token'];
            $session->set('copilot_access_token', $accessToken);
            $session->set('copilot_refresh_token', $refreshed['refresh_token'] ?? $refreshToken);
            $session->set('copilot_token_expires_at', time() + (int) ($refreshed['expires_in'] ?? 3600));
        } else {
            $accessToken = null;
        }
    } catch (RequestException) {
        $accessToken = null;
    }
}

if (empty($accessToken)) {
    copilot_fail(401, 'reauth_required');
}

$chatPayload = [
    'patient_id' => $patientUuid,
    'message' => $message,
    'conversation_history' => $conversationHistory,
];
if ($pendingDocument !== null) {
    // patient_pid is the OpenEMR-native int pid attach_and_extract's document/procedure/medication
    // endpoints need -- distinct from patient_id above (the FHIR uuid), same convention upload.php
    // already uses for /ingest.
    $chatPayload['patient_pid'] = (string) $pid;
    $chatPayload['pending_document'] = $pendingDocument;
}

try {
    $agentResp = $httpClient->post(rtrim(COPILOT_AGENT_BASE_URL, '/') . '/chat', [
        'headers' => ['Authorization' => 'Bearer ' . $accessToken],
        'json' => $chatPayload,
        'http_errors' => false,
        // Longer than the shared client's default 30s -- the agent's own tool-calling/LLM turn can
        // legitimately run past that. Kept under Apache's own 60s `Timeout` directive (see
        // httpd-default.conf) so THIS timeout fires and is caught below instead of Apache silently
        // killing the connection first (which the browser sees as a bare "Failed to fetch", not a
        // clean JSON error) -- and comfortably under this script's set_time_limit(75) above too.
        'timeout' => 45,
    ]);
} catch (RequestException $exc) {
    copilot_fail(502, 'could not reach the Clinical Co-Pilot agent service: ' . $exc->getMessage());
}

http_response_code($agentResp->getStatusCode());
echo (string) $agentResp->getBody();
