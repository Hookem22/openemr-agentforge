<?php

/**
 * Clinical Co-Pilot document-upload auth-bridge (W2_ARCHITECTURE.md Section 2).
 *
 * Same pattern as proxy.php: runs inside the clinician's real, already-authenticated OpenEMR
 * session, re-checks ACL, resolves pid -> FHIR uuid server-side, and forwards to the Python
 * agent's /ingest endpoint with a real bearer token attached server-side. The browser only ever
 * sends the OpenEMR-native pid, a doc_type, and the raw file -- never a FHIR id or OAuth token.
 */

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

// Vision extraction over several page images can run longer than a plain chat turn -- see
// proxy.php's identical rationale for why this must be raised (PHP's execution-time limit would
// otherwise kill the script with a raw, uncatchable fatal error before Guzzle's own timeout below
// can throw a normal, already-handled RequestException).
set_time_limit(120);

function copilot_upload_fail(int $status, string $error): never
{
    http_response_code($status);
    echo json_encode(['error' => $error]);
    exit;
}

if (!AclMain::aclCheckCore('patients', 'med')) {
    copilot_upload_fail(403, 'not authorized to view patient records');
}

$session = SessionWrapperFactory::getInstance()->getActiveSession();

if (!CsrfUtils::verifyCsrfToken($_POST['csrf_token'] ?? '', session: $session)) {
    http_response_code(403);
    echo json_encode([
        'error' => 'invalid_csrf',
        'csrf_token' => CsrfUtils::collectCsrfToken(session: $session),
    ]);
    exit;
}

$pid = (int) ($_POST['pid'] ?? 0);
$docType = (string) ($_POST['doc_type'] ?? '');
if ($pid <= 0 || !in_array($docType, ['lab_pdf', 'intake_form'], true)) {
    copilot_upload_fail(400, 'pid and a valid doc_type (lab_pdf or intake_form) are required');
}
if (empty($_FILES['file']) || ($_FILES['file']['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK) {
    copilot_upload_fail(400, 'a file upload is required');
}

$patientRow = sqlQuery('SELECT `uuid` FROM `patient_data` WHERE `pid` = ?', [$pid]);
if (empty($patientRow['uuid'])) {
    copilot_upload_fail(404, 'patient not found or has no FHIR uuid assigned yet');
}
$patientUuid = UuidRegistry::uuidToString($patientRow['uuid']);

$accessToken = $session->get('copilot_access_token');
$expiresAt = (int) ($session->get('copilot_token_expires_at') ?? 0);
$refreshToken = $session->get('copilot_refresh_token');

$httpClient = new Client(['timeout' => 90, 'connect_timeout' => 5]);

// Same proactive-refresh logic as proxy.php.
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
    copilot_upload_fail(401, 'reauth_required');
}

try {
    $agentResp = $httpClient->post(rtrim(COPILOT_AGENT_BASE_URL, '/') . '/ingest', [
        'headers' => ['Authorization' => 'Bearer ' . $accessToken],
        'multipart' => [
            // Unlike /chat (whose FHIR tool calls need the FHIR patient uuid), most of /ingest's
            // downstream calls are against OpenEMR's standard (non-FHIR) API, which takes the
            // native integer pid. patient_uuid is sent too -- the allergy endpoint specifically
            // requires it (see agent/app/ingestion.py::persist_intake_facts).
            ['name' => 'patient_id', 'contents' => (string) $pid],
            ['name' => 'patient_uuid', 'contents' => $patientUuid],
            ['name' => 'doc_type', 'contents' => $docType],
            [
                'name' => 'file',
                'contents' => fopen($_FILES['file']['tmp_name'], 'r'),
                'filename' => $_FILES['file']['name'],
            ],
        ],
        'http_errors' => false,
        'timeout' => 100,
    ]);
} catch (RequestException $exc) {
    copilot_upload_fail(502, 'could not reach the Clinical Co-Pilot agent service: ' . $exc->getMessage());
}

http_response_code($agentResp->getStatusCode());
echo (string) $agentResp->getBody();
