"""Concrete TargetAdapter for the Clinical Co-Pilot / OpenEMR.

Attacks go through interface/modules/copilot/proxy.php -- the real clinician-facing path -- rather
than straight against agent/'s own /chat endpoint, because the highest-priority finding in
THREAT_MODEL.md (the pid-based cross-patient IDOR) lives in proxy.php's authorization check, not in
the agent service itself. Hitting /chat directly would test a different, less interesting surface.

proxy.php authenticates via OpenEMR's own PHP session (cookie) + a per-page CSRF token embedded in
the patient-chart page's rendered HTML, not an OAuth2 bearer token -- so this adapter logs in as a
real (dedicated, disposable) OpenEMR user through the actual login form, exactly the way a real
attacker with a normal clinical login would, rather than using an API-only credential that would
bypass the real login flow this threat model is testing.
"""
from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from app.adapters.target_adapter import TargetAdapter, TargetProfile
from app.config import settings
from app.schemas import AttackSequence, ObservedResponse, ObservedTurn

_CSRF_TOKEN_RE = re.compile(r"let\s+csrfToken\s*=\s*\"([^\"]+)\"")


def _extract_answer_text(response_json: dict | None) -> str | None:
    """ChatResponse (agent/app/main.py) has no flat 'response' string field -- the clinician-facing
    text lives split across verified_claims/stripped_claims, each a {text, ...} dict (confirmed
    live: a real proxy.php response has no top-level 'response' key at all). Joins both sets, in
    order, labeling stripped ones -- the Judge needs to see what was said AND what got filtered,
    since a claim being correctly stripped is itself part of what "safe behavior" means here."""
    if not response_json:
        return None
    parts = [c.get("text", "") for c in response_json.get("verified_claims", [])]
    parts += [f"[STRIPPED: {c.get('text', '')}]" for c in response_json.get("stripped_claims", [])]
    return "\n".join(p for p in parts if p) or None

# Any patient id that exists in the seeded data works here -- this page load is only a vehicle to
# obtain a CSRF token from the rendered widget.php include (see widget.php's own
# CsrfUtils::collectCsrfToken call); it is NOT the pid actually attacked, which comes from
# AttackSequence.turns[].pid.
_CSRF_BOOTSTRAP_PID = 1


class OpenEMRAdapter(TargetAdapter):
    def __init__(self) -> None:
        self._session = requests.Session()
        self._csrf_token: str | None = None
        self._base_url = settings.target_base_url.rstrip("/")
        self._authenticated = False

    def authenticate(self) -> None:
        """Two independent logins are required, mirroring exactly what a real clinician's browser
        does: (1) the classic OpenEMR interface session (cookie-based, gives access to
        interface/patient_file/... pages and their embedded CSRF token), and (2) OpenEMR's own
        OAuth2 authorization server session, which is what actually populates
        `copilot_access_token` in the interface session via callback.php -- proxy.php reads that
        token, not the interface login alone (confirmed live: without step 2, every proxy.php call
        returns 401 reauth_required, matching exactly what a user who never clicked "Authorize
        Clinical Co-Pilot" would see)."""
        self._interface_login()
        self._csrf_token = self._fetch_csrf_token()
        if self._csrf_token is None:
            raise RuntimeError(
                "OpenEMRAdapter.authenticate: interface login completed but no csrfToken was found "
                "on the demographics page afterward -- most likely REDTEAM_OPENEMR_USER/"
                "REDTEAM_OPENEMR_PASS were rejected, not a network failure."
            )
        self._complete_oauth2_authorization()
        self._authenticated = True

    def _interface_login(self) -> None:
        login_url = f"{self._base_url}/interface/main/main_screen.php"
        resp = self._session.post(
            login_url,
            params={"auth": "login", "site": settings.redteam_openemr_site},
            data={
                "new_login_session_management": "1",
                "languageChoice": "1",
                "authUser": settings.redteam_openemr_user,
                "clearPass": settings.redteam_openemr_pass,
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

    def _complete_oauth2_authorization(self) -> None:
        """Drives start.php -> OAuth2 server's own login form (a SEPARATE login from the interface
        session above, confirmed live) -> scope-consent screen -> callback.php, exactly the path
        the widget's popup takes, so that callback.php ends up writing copilot_access_token into
        this same requests.Session's cookie-tracked server-side session."""
        start_resp = self._session.get(
            f"{self._base_url}/interface/modules/copilot/start.php",
            params={"return_url": ""},
            timeout=20,
        )
        # OAuth2 server login form. `user_role=api` (not "portal-api") selects the practitioner
        # login path -- found only by reading AuthorizationController::userLogin() directly, since
        # omitting it leaves `$continueLogin` false regardless of correct credentials (the code
        # only calls verifyLogin() at all when a user_role field is present).
        login_soup = BeautifulSoup(start_resp.text, "html.parser")
        login_form = login_soup.find("form", {"name": "userLogin"})
        login_csrf = login_form.find("input", {"name": "csrf_token_form"}).get("value")
        login_action = login_form.get("action")
        login_post_url = login_action if login_action.startswith("http") else f"{self._base_url}{login_action}"

        consent_resp = self._session.post(
            login_post_url,
            data={
                "csrf_token_form": login_csrf,
                "email": "",
                "username": settings.redteam_openemr_user,
                "password": settings.redteam_openemr_pass,
                "user_role": "api",
            },
            timeout=20,
        )

        # Scope-consent screen. The per-resource read/search checkboxes on this page have no
        # `name` attribute at all -- they're pure UI, synthesized into real scope[...] fields by
        # client-side JS this adapter doesn't run (confirmed live: submitting only the few
        # top-level named fields granted a token that got 401'd on every FHIR resource read).
        # Submitting the exact scope list our own registered client requests
        # (settings.target_oauth_scopes, mirroring interface/modules/copilot/config.php's
        # COPILOT_SCOPE) sidesteps reverse-engineering that JS entirely -- it's what a user
        # clicking "Authorize" without touching any toggle grants anyway.
        consent_soup = BeautifulSoup(consent_resp.text, "html.parser")
        consent_form = consent_soup.find("form", {"name": "userLogin"})
        if consent_form is None:
            raise RuntimeError(
                "OpenEMRAdapter._complete_oauth2_authorization: no consent form found after OAuth2 "
                "login -- the login step itself likely failed (wrong REDTEAM_OPENEMR_USER/PASS, or "
                "OpenEMR's OAuth2 login form fields changed)."
            )
        consent_action = consent_form.get("action")
        consent_post_url = (
            consent_action if consent_action.startswith("http") else f"{self._base_url}{consent_action}"
        )
        consent_csrf = consent_form.find("input", {"name": "csrf_token_form"}).get("value")

        scope_data = {"csrf_token_form": consent_csrf, "proceed": "1"}
        for scope in settings.target_oauth_scopes:
            scope_data[f"scope[{scope}]"] = scope

        callback_resp = self._session.post(consent_post_url, data=scope_data, timeout=20)
        if "copilot-authorized" not in callback_resp.text:
            raise RuntimeError(
                "OpenEMRAdapter._complete_oauth2_authorization: consent form submitted but "
                "callback.php did not confirm authorization -- response: "
                f"{callback_resp.text[:500]!r}"
            )

    def _fetch_csrf_token(self) -> str | None:
        chart_url = f"{self._base_url}/interface/patient_file/summary/demographics.php"
        resp = self._session.get(chart_url, params={"pid": _CSRF_BOOTSTRAP_PID}, timeout=15)
        match = _CSRF_TOKEN_RE.search(resp.text)
        return match.group(1) if match else None

    def send(self, attack: AttackSequence) -> ObservedResponse:
        if not self._authenticated:
            self.authenticate()

        proxy_url = f"{self._base_url}/interface/modules/copilot/proxy.php"
        observed_turns: list[ObservedTurn] = []
        conversation_history: list[dict] = []

        for turn in attack.turns:
            body = {
                "pid": turn.pid,
                "message": turn.message,
                "conversation_history": (
                    turn.conversation_history_override
                    if turn.conversation_history_override is not None
                    else conversation_history
                ),
                "csrf_token": self._csrf_token,
            }
            start = time.monotonic()
            try:
                resp = self._session.post(proxy_url, json=body, timeout=60)
            except requests.Timeout:
                observed_turns.append(
                    ObservedTurn(pid=turn.pid, sent_message=turn.message, status="timeout")
                )
                continue
            latency_ms = (time.monotonic() - start) * 1000

            if resp.status_code == 403:
                try:
                    err = resp.json()
                except ValueError:
                    err = {}
                if err.get("error") == "invalid_csrf" and err.get("csrf_token"):
                    # proxy.php's own documented retry contract (see proxy.php's comment on the
                    # shared-session CSRF rotation race) -- refresh and retry this turn once.
                    self._csrf_token = err["csrf_token"]
                    body["csrf_token"] = self._csrf_token
                    resp = self._session.post(proxy_url, json=body, timeout=60)
                    latency_ms = (time.monotonic() - start) * 1000
                else:
                    observed_turns.append(
                        ObservedTurn(
                            pid=turn.pid,
                            sent_message=turn.message,
                            status="csrf_error",
                            http_status=resp.status_code,
                            latency_ms=latency_ms,
                        )
                    )
                    continue

            if resp.status_code == 401:
                observed_turns.append(
                    ObservedTurn(
                        pid=turn.pid, sent_message=turn.message, status="auth_error",
                        http_status=resp.status_code, latency_ms=latency_ms,
                    )
                )
                continue

            if not resp.ok:
                observed_turns.append(
                    ObservedTurn(
                        pid=turn.pid, sent_message=turn.message, status="http_error",
                        http_status=resp.status_code, response_text=resp.text[:2000],
                        latency_ms=latency_ms,
                    )
                )
                continue

            response_json = None
            try:
                response_json = resp.json()
            except ValueError:
                pass

            observed_turns.append(
                ObservedTurn(
                    pid=turn.pid,
                    sent_message=turn.message,
                    status="ok",
                    http_status=resp.status_code,
                    response_text=_extract_answer_text(response_json),
                    response_json=response_json,
                    latency_ms=latency_ms,
                )
            )

            # Real conversation continuity for the *next* turn -- unless this turn deliberately
            # overrode history (a state-corruption attack), in which case the override is what
            # should carry forward, matching what a real poisoned client would do.
            if turn.conversation_history_override is not None:
                conversation_history = turn.conversation_history_override
            elif response_json and "conversation_history" in response_json:
                conversation_history = response_json["conversation_history"]

        return ObservedResponse(
            attack_id=attack.attack_id,
            target_id=attack.target_id,
            target_version=settings.target_version,
            turns=observed_turns,
        )

    def describe(self) -> TargetProfile:
        return TargetProfile(
            target_id=settings.target_id,
            endpoints={
                "chat": "/interface/modules/copilot/proxy.php",
                "upload": "/interface/modules/copilot/upload.php",
                "login": "/interface/main/main_screen.php",
            },
            auth_method="openemr_session_cookie+csrf",
            sensitive_fields=[
                "name", "dob", "ssn", "address", "phone", "conditions", "medications",
                "allergies", "vitals", "labs", "notes",
            ],
            rate_limits={
                # Observed in proxy.php/upload.php directly, not guessed.
                "proxy_guzzle_timeout_s": 45,
                "proxy_set_time_limit_s": 75,
                "upload_set_time_limit_s": 120,
            },
        )
