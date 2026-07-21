<?php

/**
 * Clinical Co-Pilot document-preview auth-bridge (Citation Contract's required click-to-source
 * visual overlay -- Week 2 Final grader feedback).
 *
 * Unlike proxy.php/upload.php, this does NOT forward the request to OpenEMR's own REST API for the
 * document bytes: a real live test found DocumentService::getFile() (which OpenEMR's own
 * GET /api/patient/:pid/document/:did route calls) throws "CSRF key is empty" when invoked via a
 * Bearer-token REST request -- that code path's CSRF handling assumes a traditional browser
 * session, which a REST/Bearer caller doesn't have. Rather than patch OpenEMR core's CSRF logic
 * (security-sensitive, a bigger and riskier change than this feature needs), this script fetches
 * the document bytes itself via DocumentService::getFile() directly, from within its own real,
 * already-authenticated browser session -- the same "authorization inheritance, not
 * reimplementation" principle proxy.php/upload.php already use -- then POSTs just the raw bytes to
 * the agent's /document_preview for rasterization.
 *
 * A plain GET-triggered read with no side effects (viewing a preview changes nothing), so unlike
 * proxy.php/upload.php this does not require a CSRF token -- CSRF protection matters for
 * state-changing requests.
 */

$sessionAllowWrite = true;

require_once("../../globals.php");

use GuzzleHttp\Client;
use GuzzleHttp\Exception\RequestException;
use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Services\DocumentService;

require_once(__DIR__ . '/config.php');

header('Content-Type: application/json');

function copilot_preview_fail(int $status, string $error): never
{
    http_response_code($status);
    echo json_encode(['error' => $error]);
    exit;
}

if (!AclMain::aclCheckCore('patients', 'med')) {
    copilot_preview_fail(403, 'not authorized to view patient records');
}

$pid = (int) ($_GET['pid'] ?? 0);
$documentId = (int) ($_GET['document_id'] ?? 0);
if ($pid <= 0 || $documentId <= 0) {
    copilot_preview_fail(400, 'pid and document_id are required');
}

$documentResult = (new DocumentService())->getFile((string) $pid, (string) $documentId);
// getFile()'s 'file' key is already the raw byte content (C_Document::retrieve_action() with
// disable_exit=true returns file_get_contents($url) directly, not a path) -- an earlier version of
// this script wrongly treated it as a path and called is_readable()/file_get_contents() on it,
// which silently "worked" (both simply returned false on the garbage pseudo-path) and always
// reported a real, existing document as "not found". Found live via the actual browser flow.
if (empty($documentResult) || empty($documentResult['file'])) {
    copilot_preview_fail(404, 'document not found');
}
$fileBytes = $documentResult['file'];
$mimetype = $documentResult['mimetype'] ?: 'application/octet-stream';

$session = SessionWrapperFactory::getInstance()->getActiveSession();

$accessToken = $session->get('copilot_access_token');
$expiresAt = (int) ($session->get('copilot_token_expires_at') ?? 0);
$refreshToken = $session->get('copilot_refresh_token');

$httpClient = new Client(['timeout' => 20, 'connect_timeout' => 5]);

// Same proactive-refresh logic as proxy.php/upload.php -- the agent still gates this endpoint on a
// valid bearer token (defense in depth, uniform with every other agent endpoint) even though it no
// longer calls OpenEMR with it.
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
    copilot_preview_fail(401, 'reauth_required');
}

try {
    $agentResp = $httpClient->post(rtrim(COPILOT_AGENT_BASE_URL, '/') . '/document_preview', [
        'headers' => ['Authorization' => 'Bearer ' . $accessToken],
        'multipart' => [
            ['name' => 'mimetype', 'contents' => $mimetype],
            ['name' => 'file', 'contents' => $fileBytes, 'filename' => $documentResult['filename'] ?? 'document'],
        ],
        'http_errors' => false,
        'timeout' => 30,
    ]);
} catch (RequestException $exc) {
    copilot_preview_fail(502, 'could not reach the Clinical Co-Pilot agent service: ' . $exc->getMessage());
}

http_response_code($agentResp->getStatusCode());
echo (string) $agentResp->getBody();
