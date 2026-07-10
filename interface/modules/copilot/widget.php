<?php

/**
 * Clinical Co-Pilot chat widget -- minimal MVP UI (agent-implementation.md, build order step 3).
 *
 * Meant to be `require`'d from inside a page that already has `$pid` and `$session` in scope (e.g.
 * demographics.php, right before </body>) -- it does NOT bootstrap globals.php itself. All it does
 * is render a floating chat panel and talk to proxy.php, which does all real auth/ACL/data work.
 * The browser never sees a FHIR id or an OAuth token -- only `pid` (already known to this page) and
 * a CSRF token (same convention as the rest of this page, see CsrfUtils usage above).
 */

use OpenEMR\Common\Csrf\CsrfUtils;

$copilotCsrfToken = CsrfUtils::collectCsrfToken(session: $session);

// Snapshot of whether this session already has a copilot OAuth session-bridge token, so the widget
// can show the "Authorize" prompt immediately on open instead of waiting for a failed chat attempt.
// This is a point-in-time check (a token present here could still turn out to be expired with no
// refresh token by the time a message is actually sent) -- the existing 401-driven reauth flow in
// the JS below still covers that case; this is purely a UX improvement, not a new auth boundary.
$copilotIsAuthorized = !empty($session->get('copilot_access_token'));
?>
<div id="copilot-widget">
    <button id="copilot-toggle" type="button" class="btn btn-primary" style="position:fixed; bottom:20px; right:20px; z-index:1050; border-radius:24px;">
        Clinical Co-Pilot
    </button>
    <div id="copilot-panel" style="display:none; position:fixed; bottom:70px; right:20px; width:360px; max-height:70vh; z-index:1050; background:#fff; border:1px solid #ccc; border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,.2); display:flex; flex-direction:column;">
        <div style="padding:8px 12px; border-bottom:1px solid #eee; font-weight:bold;">Clinical Co-Pilot</div>
        <div id="copilot-messages" style="flex:1; overflow-y:auto; padding:8px 12px; font-size:13px;"></div>
        <form id="copilot-form" style="display:flex; border-top:1px solid #eee; padding:6px;">
            <input id="copilot-input" type="text" placeholder="Ask about this patient..." style="flex:1; border:none; outline:none; font-size:13px;" autocomplete="off" />
            <button type="submit" class="btn btn-sm btn-primary">Send</button>
        </form>
    </div>
</div>
<script>
(function () {
    const pid = <?php echo js_escape($pid); ?>;
    let csrfToken = <?php echo js_escape($copilotCsrfToken); ?>;
    let isAuthorized = <?php echo $copilotIsAuthorized ? 'true' : 'false'; ?>;
    const proxyUrl = <?php echo js_escape($GLOBALS['web_root'] ?? ''); ?> + '/interface/modules/copilot/proxy.php';
    const startUrl = <?php echo js_escape($GLOBALS['web_root'] ?? ''); ?> + '/interface/modules/copilot/start.php';
    const QUICK_START_MESSAGE = "Tell me about this patient before today's visit";

    let conversationHistory = [];
    let quickStartEl = null;
    let loadingEl = null;

    const toggleBtn = document.getElementById('copilot-toggle');
    const panel = document.getElementById('copilot-panel');
    const messagesEl = document.getElementById('copilot-messages');
    const form = document.getElementById('copilot-form');
    const input = document.getElementById('copilot-input');
    const sendBtn = form.querySelector('button[type="submit"]');

    toggleBtn.addEventListener('click', function () {
        panel.style.display = (panel.style.display === 'none') ? 'flex' : 'none';
    });

    function addLine(text, kind) {
        const div = document.createElement('div');
        div.style.margin = '4px 0';
        if (kind === 'user') {
            div.style.fontWeight = 'bold';
        } else if (kind === 'warn') {
            div.style.color = '#a94442';
        }
        div.textContent = text;
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function showLoading() {
        if (loadingEl) {
            return;
        }
        loadingEl = document.createElement('div');
        loadingEl.style.margin = '4px 0';
        loadingEl.style.fontStyle = 'italic';
        loadingEl.style.color = '#888';
        loadingEl.textContent = 'Thinking...';
        messagesEl.appendChild(loadingEl);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        input.disabled = true;
        sendBtn.disabled = true;
    }

    function hideLoading() {
        if (loadingEl) {
            loadingEl.remove();
            loadingEl = null;
        }
        input.disabled = false;
        sendBtn.disabled = false;
    }

    function addQuickStartPrompt() {
        if (quickStartEl || !isAuthorized) {
            return;
        }
        const div = document.createElement('div');
        div.style.margin = '4px 0';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-primary';
        btn.textContent = QUICK_START_MESSAGE;
        btn.addEventListener('click', function () {
            submitMessage(QUICK_START_MESSAGE);
        });
        div.appendChild(btn);
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        quickStartEl = div;
    }

    function removeQuickStartPrompt() {
        if (quickStartEl) {
            quickStartEl.remove();
            quickStartEl = null;
        }
    }

    // window.open() only bypasses the popup blocker when called synchronously inside a real user
    // gesture (e.g. a click handler) -- calling it from inside a fetch().then() callback (as the
    // 401 response arrives asynchronously) gets silently blocked by the browser. So instead of
    // auto-opening on 401, render a button the clinician must click; that click is the gesture the
    // popup needs. `onAuthorized` runs once the popup reports success -- either resuming a pending
    // message (mid-conversation reauth) or just unlocking the quick-start prompt (first-open case).
    function addAuthorizePrompt(onAuthorized) {
        const div = document.createElement('div');
        div.style.margin = '4px 0';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-secondary';
        btn.textContent = 'Authorize Clinical Co-Pilot';
        btn.addEventListener('click', function () {
            btn.disabled = true;
            const popup = window.open(startUrl + '?return_url=' + encodeURIComponent(window.location.href), 'copilot_auth', 'width=500,height=650');
            if (!popup) {
                addLine('Error: pop-up was blocked -- allow pop-ups for this site and try again.', 'warn');
                btn.disabled = false;
                return;
            }
            function onMessage(event) {
                if (event.origin !== window.location.origin) {
                    return;
                }
                if (event.data && event.data.type === 'copilot-authorized') {
                    window.removeEventListener('message', onMessage);
                    div.remove();
                    isAuthorized = true;
                    onAuthorized();
                }
            }
            window.addEventListener('message', onMessage);
        });
        div.appendChild(btn);
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderAnswer(result) {
        (result.verified_claims || []).forEach(function (c) {
            addLine('\u2022 ' + c.text, 'answer');
        });
        (result.stripped_claims || []).forEach(function (c) {
            addLine('[withheld -- could not verify: ' + c.reason + ']', 'warn');
        });
        (result.tool_failures || []).forEach(function (f) {
            addLine('[' + f.tool + ' lookup failed: ' + f.error + ']', 'warn');
        });
        conversationHistory = result.conversation_history || conversationHistory;
    }

    function send(message, allowRetry) {
        if (allowRetry === undefined) {
            allowRetry = true;
        }
        return fetch(proxyUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pid: pid,
                message: message,
                conversation_history: conversationHistory,
                csrf_token: csrfToken,
            }),
        }).then(function (resp) {
            if (resp.status === 401) {
                return resp.json().then(function () {
                    const err = new Error('reauth_required');
                    err.reauthRequired = true;
                    throw err;
                });
            }
            if (resp.status === 403) {
                return resp.json().then(function (body) {
                    // The shared OpenEMR session's CSRF key can rotate in the background (e.g. another
                    // frame's polling triggers a session id change) while the panel is open. proxy.php
                    // hands back a fresh token in this case -- retry once with it before giving up.
                    if (body.error === 'invalid_csrf' && body.csrf_token && allowRetry) {
                        csrfToken = body.csrf_token;
                        return send(message, false);
                    }
                    throw new Error(body.error || ('HTTP ' + resp.status));
                });
            }
            if (!resp.ok) {
                return resp.json().then(function (body) {
                    throw new Error(body.error || ('HTTP ' + resp.status));
                });
            }
            return resp.json();
        });
    }

    function submitMessage(message) {
        removeQuickStartPrompt();
        addLine(message, 'user');
        showLoading();
        send(message).then(function (result) {
            hideLoading();
            renderAnswer(result);
        }).catch(function (err) {
            hideLoading();
            if (err.reauthRequired) {
                addAuthorizePrompt(function () {
                    submitMessage(message);
                });
                return;
            }
            addLine('Error: ' + err.message, 'warn');
        });
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        const message = input.value.trim();
        if (!message) {
            return;
        }
        input.value = '';
        submitMessage(message);
    });

    // First-open state: prompt for authorization immediately if this session doesn't already have a
    // copilot token, rather than waiting for the clinician to type a message and hit a 401. Once
    // authorized (or if already authorized on load), show a one-click prompt for the standard
    // "what changed" opener instead of requiring the clinician to type it out.
    if (isAuthorized) {
        addQuickStartPrompt();
    } else {
        addAuthorizePrompt(function () {
            addQuickStartPrompt();
        });
    }
})();
</script>
