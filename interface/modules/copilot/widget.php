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
        <div id="copilot-upload-row" style="display:flex; gap:6px; padding:6px 12px; border-top:1px solid #eee;">
            <select id="copilot-doc-type" style="font-size:12px;">
                <option value="lab_pdf">Lab PDF</option>
                <option value="intake_form">Intake form</option>
            </select>
            <input id="copilot-file-input" type="file" accept=".pdf,image/*" style="display:none;" />
            <button id="copilot-upload-btn" type="button" class="btn btn-sm btn-outline-secondary">Upload document</button>
        </div>
        <form id="copilot-form" style="display:flex; border-top:1px solid #eee; padding:6px;">
            <input id="copilot-input" type="text" placeholder="Ask about this patient..." style="flex:1; border:none; outline:none; font-size:13px;" autocomplete="off" />
            <button type="submit" class="btn btn-sm btn-primary">Send</button>
        </form>
    </div>
    <!-- Citation Contract's required click-to-source visual overlay (Week 2 Final grader feedback):
         shows the exact source page a claim's citation points to, with a highlight box at its bbox. -->
    <div id="copilot-preview-overlay" style="display:none; position:fixed; inset:0; z-index:1100; background:rgba(0,0,0,.5); align-items:center; justify-content:center;">
        <div style="background:#fff; border-radius:8px; max-width:92vw; max-height:92vh; overflow:auto; padding:16px; position:relative;">
            <button id="copilot-preview-close" type="button" class="btn btn-sm btn-outline-secondary" style="position:absolute; top:8px; right:8px;">Close</button>
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                <button id="copilot-preview-prev" type="button" class="btn btn-sm btn-outline-secondary">&laquo; Prev</button>
                <span id="copilot-preview-page-label" style="font-size:12px; color:#555;"></span>
                <button id="copilot-preview-next" type="button" class="btn btn-sm btn-outline-secondary">Next &raquo;</button>
            </div>
            <div id="copilot-preview-image-wrap" style="position:relative; display:inline-block; line-height:0;">
                <img id="copilot-preview-image" style="max-width:80vw; max-height:75vh; display:block;" />
                <div id="copilot-preview-highlight" style="display:none; position:absolute; border:3px solid #e01e1e; background:rgba(224,30,30,.15); pointer-events:none;"></div>
            </div>
        </div>
    </div>
</div>
<script>
(function () {
    const pid = <?php echo js_escape($pid); ?>;
    let csrfToken = <?php echo js_escape($copilotCsrfToken); ?>;
    let isAuthorized = <?php echo $copilotIsAuthorized ? 'true' : 'false'; ?>;
    const proxyUrl = <?php echo js_escape($GLOBALS['web_root'] ?? ''); ?> + '/interface/modules/copilot/proxy.php';
    const uploadUrl = <?php echo js_escape($GLOBALS['web_root'] ?? ''); ?> + '/interface/modules/copilot/upload.php';
    const startUrl = <?php echo js_escape($GLOBALS['web_root'] ?? ''); ?> + '/interface/modules/copilot/start.php';
    const previewUrl = <?php echo js_escape($GLOBALS['web_root'] ?? ''); ?> + '/interface/modules/copilot/document_preview.php';
    const QUICK_START_MESSAGE = "Tell me about this patient before today's visit";

    let conversationHistory = [];
    // Citation Contract's required click-to-source visual overlay: a document uploaded via
    // uploadDocument() is persisted immediately (unchanged from before), but a citation from it
    // only carries a bbox when the intake-extractor worker processes it *within* a chat turn, not
    // from that standalone upload call. Stash the just-uploaded file here so the clinician's very
    // next question attaches it as pending_document too -- giving that answer a chance to cite
    // specific fields with a clickable source, on top of the existing extraction summary.
    let pendingDocumentForChat = null;
    let quickStartEl = null;
    let loadingEl = null;

    const toggleBtn = document.getElementById('copilot-toggle');
    const panel = document.getElementById('copilot-panel');
    const messagesEl = document.getElementById('copilot-messages');
    const form = document.getElementById('copilot-form');
    const input = document.getElementById('copilot-input');
    const sendBtn = form.querySelector('button[type="submit"]');
    const docTypeSelect = document.getElementById('copilot-doc-type');
    const fileInput = document.getElementById('copilot-file-input');
    const uploadBtn = document.getElementById('copilot-upload-btn');

    const previewOverlay = document.getElementById('copilot-preview-overlay');
    const previewImage = document.getElementById('copilot-preview-image');
    const previewHighlight = document.getElementById('copilot-preview-highlight');
    const previewPageLabel = document.getElementById('copilot-preview-page-label');
    const previewPrevBtn = document.getElementById('copilot-preview-prev');
    const previewNextBtn = document.getElementById('copilot-preview-next');
    const previewCloseBtn = document.getElementById('copilot-preview-close');

    toggleBtn.addEventListener('click', function () {
        panel.style.display = (panel.style.display === 'none') ? 'flex' : 'none';
    });

    // Citation Contract's required click-to-source visual overlay. previewCache avoids re-fetching
    // (and re-rasterizing, on the agent side) the same document every time a different claim citing
    // it is clicked. currentPreview holds the state needed to redraw on Prev/Next navigation.
    const previewCache = {};
    let currentPreview = null; // {pages, pageMimetype, pageIndex, bbox}

    function renderPreviewPage() {
        if (!currentPreview) {
            return;
        }
        const idx = currentPreview.pageIndex;
        previewImage.src = 'data:' + currentPreview.pageMimetype + ';base64,' + currentPreview.pages[idx];
        previewPageLabel.textContent = 'Page ' + (idx + 1) + ' of ' + currentPreview.pages.length;
        previewPrevBtn.disabled = (idx <= 0);
        previewNextBtn.disabled = (idx >= currentPreview.pages.length - 1);

        const bbox = currentPreview.bbox;
        if (bbox && bbox.page === idx) {
            previewHighlight.style.display = 'block';
            previewHighlight.style.left = (bbox.x0 * 100) + '%';
            previewHighlight.style.top = (bbox.y0 * 100) + '%';
            previewHighlight.style.width = ((bbox.x1 - bbox.x0) * 100) + '%';
            previewHighlight.style.height = ((bbox.y1 - bbox.y0) * 100) + '%';
        } else {
            previewHighlight.style.display = 'none';
        }
    }

    function fetchDocumentPreview(documentId) {
        if (previewCache[documentId]) {
            return Promise.resolve(previewCache[documentId]);
        }
        const url = previewUrl + '?pid=' + encodeURIComponent(pid) + '&document_id=' + encodeURIComponent(documentId);
        return fetch(url).then(function (resp) {
            return resp.json().then(function (body) {
                if (!resp.ok) {
                    throw new Error(body.error || body.detail || ('HTTP ' + resp.status));
                }
                return body;
            });
        }).then(function (data) {
            previewCache[documentId] = data;
            return data;
        });
    }

    function showSourcePreview(documentId, bbox) {
        fetchDocumentPreview(documentId).then(function (data) {
            const pages = data.pages_base64 || [];
            if (!pages.length) {
                addLine('No preview available for this source document.', 'warn');
                return;
            }
            const requestedPage = bbox && typeof bbox.page === 'number' ? bbox.page : 0;
            currentPreview = {
                pages: pages,
                pageMimetype: data.page_mimetype,
                pageIndex: Math.max(0, Math.min(requestedPage, pages.length - 1)),
                bbox: bbox || null,
            };
            renderPreviewPage();
            previewOverlay.style.display = 'flex';
        }).catch(function (err) {
            addLine('Preview error: ' + err.message, 'warn');
        });
    }

    previewPrevBtn.addEventListener('click', function () {
        if (!currentPreview || currentPreview.pageIndex <= 0) {
            return;
        }
        currentPreview.pageIndex -= 1;
        renderPreviewPage();
    });

    previewNextBtn.addEventListener('click', function () {
        if (!currentPreview || currentPreview.pageIndex >= currentPreview.pages.length - 1) {
            return;
        }
        currentPreview.pageIndex += 1;
        renderPreviewPage();
    });

    previewCloseBtn.addEventListener('click', function () {
        previewOverlay.style.display = 'none';
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
            const div = document.createElement('div');
            div.style.margin = '4px 0';
            div.appendChild(document.createTextNode('\u2022 ' + c.text));

            // Citation Contract's required click-to-source visual overlay: only document-sourced
            // claims carry a bbox (FHIR/guideline citations have no page location to show).
            const source = c.source || {};
            if (source.source_type === 'document' && source.bbox) {
                const link = document.createElement('button');
                link.type = 'button';
                link.className = 'btn btn-link btn-sm';
                link.style.padding = '0';
                link.style.marginLeft = '4px';
                link.style.fontSize = '11px';
                link.style.verticalAlign = 'baseline';
                link.textContent = '[view source]';
                link.addEventListener('click', function () {
                    showSourcePreview(source.source_id, source.bbox);
                });
                div.appendChild(link);
            }
            messagesEl.appendChild(div);
        });
        messagesEl.scrollTop = messagesEl.scrollHeight;
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
        const requestBody = {
            pid: pid,
            message: message,
            conversation_history: conversationHistory,
            csrf_token: csrfToken,
        };
        if (pendingDocumentForChat) {
            requestBody.pending_document = pendingDocumentForChat;
        }
        return fetch(proxyUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
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
            pendingDocumentForChat = null;  // sent (or attempted) -- don't reattach to a later message
            renderAnswer(result);
        }).catch(function (err) {
            hideLoading();
            if (err.reauthRequired) {
                addAuthorizePrompt(function () {
                    submitMessage(message);
                });
                return;
            }
            pendingDocumentForChat = null;
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

    function renderIngestResult(docType, result) {
        const extraction = result.extraction || {};
        if (docType === 'lab_pdf') {
            const results = extraction.results || [];
            addLine('Extracted ' + results.length + ' lab result(s) from the document.', 'answer');
            results.forEach(function (r) {
                const flag = r.abnormal_flag ? ' [ABNORMAL]' : '';
                const conf = r.confidence !== undefined ? ' (confidence ' + Math.round(r.confidence * 100) + '%)' : '';
                addLine('• ' + r.test_name + ': ' + r.value + ' ' + (r.unit || '') + flag + conf, 'answer');
            });
        } else {
            const meds = extraction.current_medications || [];
            const allergies = extraction.allergies || [];
            const familyHistory = extraction.family_history || [];
            addLine('Extracted ' + meds.length + ' medication(s), ' + allergies.length + ' allerg(y/ies), ' + familyHistory.length + ' family history entr(y/ies).', 'answer');
            if (extraction.chief_concern && extraction.chief_concern.text) {
                addLine('Chief concern: ' + extraction.chief_concern.text, 'answer');
            }
        }
        if (result.was_deduped) {
            addLine('(This exact document was already processed before -- no new records created.)', 'warn');
        }
    }

    function uploadDocument(file, docType, allowRetry) {
        if (allowRetry === undefined) {
            allowRetry = true;
        }
        const formData = new FormData();
        formData.append('pid', pid);
        formData.append('doc_type', docType);
        formData.append('csrf_token', csrfToken);
        formData.append('file', file);

        return fetch(uploadUrl, { method: 'POST', body: formData }).then(function (resp) {
            if (resp.status === 401) {
                return resp.json().then(function () {
                    const err = new Error('reauth_required');
                    err.reauthRequired = true;
                    throw err;
                });
            }
            if (resp.status === 403) {
                return resp.json().then(function (body) {
                    if (body.error === 'invalid_csrf' && body.csrf_token && allowRetry) {
                        csrfToken = body.csrf_token;
                        return uploadDocument(file, docType, false);
                    }
                    throw new Error(body.error || ('HTTP ' + resp.status));
                });
            }
            if (!resp.ok) {
                return resp.json().then(function (body) {
                    throw new Error(body.detail || body.error || ('HTTP ' + resp.status));
                });
            }
            return resp.json();
        });
    }

    uploadBtn.addEventListener('click', function () {
        fileInput.click();
    });

    // btoa(String.fromCharCode(...)) chokes on large files (call-stack limits on the spread) --
    // this chunks the conversion instead, same approach as most base64-encode-a-Blob snippets.
    function _fileToBase64(file) {
        return file.arrayBuffer().then(function (buffer) {
            const bytes = new Uint8Array(buffer);
            let binary = '';
            const CHUNK = 0x8000;
            for (let i = 0; i < bytes.length; i += CHUNK) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
            }
            return btoa(binary);
        });
    }

    function submitUpload(file, docType) {
        removeQuickStartPrompt();
        addLine('Uploading ' + file.name + ' (' + docType + ')...', 'user');
        showLoading();
        uploadDocument(file, docType).then(function (result) {
            hideLoading();
            fileInput.value = '';
            renderIngestResult(docType, result);
            // Citation Contract's required click-to-source overlay: stash this file so the
            // clinician's very next question attaches it as pending_document too, giving that
            // answer a chance to cite specific fields from it with a clickable, bbox-located
            // source -- this standalone /ingest call above never produces one on its own.
            return _fileToBase64(file).then(function (base64) {
                pendingDocumentForChat = {
                    data_base64: base64,
                    filename: file.name,
                    doc_type: docType,
                    mimetype: file.type || 'application/pdf',
                };
                addLine('(Ask a question now to get an answer that can cite specific fields from this document.)', 'warn');
            });
        }).catch(function (err) {
            hideLoading();
            if (err.reauthRequired) {
                addAuthorizePrompt(function () {
                    submitUpload(file, docType);
                });
                return;
            }
            fileInput.value = '';
            addLine('Upload error: ' + err.message, 'warn');
        });
    }

    fileInput.addEventListener('change', function () {
        const file = fileInput.files[0];
        if (!file) {
            return;
        }
        submitUpload(file, docTypeSelect.value);
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
