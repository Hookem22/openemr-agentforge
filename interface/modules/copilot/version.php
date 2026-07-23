<?php

/**
 * Reports the git commit actually deployed on this instance -- no PHI, no session, no ACL check
 * needed, just a commit hash. Exists so the redteam platform (agentforge-security) can record the
 * REAL target_version a test ran against, instead of trusting a static env var on its own side that
 * silently goes stale the moment a new build deploys without someone remembering to update it
 * (confirmed live: AgentForge report #13's re-test history showed six straight "passes" all labeled
 * with the same build, when in fact three different real commits were live across those runs).
 *
 * DEPLOYED_COMMIT_SHA is set from Railway's own RAILWAY_GIT_COMMIT_SHA build arg (see the root
 * Dockerfile) -- "unknown" if unset (e.g. a non-Railway/local build), which callers should treat as
 * "live version could not be determined," not an error.
 */

header('Content-Type: application/json');
echo json_encode(['commit' => getenv('DEPLOYED_COMMIT_SHA') ?: 'unknown']);
