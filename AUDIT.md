# Security & Compliance Audit — OpenEMR Fork

**Scope:** Static code/configuration audit of this repository, performed before adding the Clinical Co-Pilot AI
agent (per the assignment's hard gate — no AI work begins until this audit is complete). The application has
not yet been run or deployed, so this is a source-level review, not a live penetration test.

---

## Summary (read this first)

This audit reviewed authentication, session management, authorization, the REST/FHIR API surface, data
protection, and HIPAA-relevant compliance controls in the OpenEMR fork at `openemr-base-clean-main`. OpenEMR is
a mature, actively maintained EHR with genuinely strong foundations — bcrypt/argon2 password hashing with
auto-rehash, IP- and username-based brute-force lockout, broad CSRF token coverage, TOTP/U2F multi-factor
support, a real encryption-at-rest subsystem (`CryptoGen`, AES-256-CBC + HMAC), and audit logging that is
**enabled by default and logs reads, not just writes** (`src/Common/Logging/EventAuditLogger.php`). These are
not incidental; they're the kind of controls a system handling PHI needs, and they materially reduce the work
required to reach a defensible posture.

That said, three findings stood out as the most consequential, and directly shape how the Clinical Co-Pilot
must be built:

**1. CORS is misconfigured to allow credentialed cross-origin requests from any origin.**
`src/RestControllers/Subscriber/CORSListener.php` reflects whatever `Origin` header a request sends directly
back as `Access-Control-Allow-Origin`, while also setting `Access-Control-Allow-Credentials: true`. This
combination is one of the more dangerous CORS misconfigurations possible: a malicious website can make
authenticated, credentialed requests against the FHIR/REST API from a victim's browser and read the response.
The OpenEMR maintainers have flagged this themselves with a `// TODO` comment in the code, so it's a known,
unresolved issue, not an oversight on our part. Any agent-facing API endpoints we add must not inherit this
listener's behavior unchanged.

**2. There is no PHI de-identification/redaction capability anywhere in the codebase, and highly sensitive
fields like SSN are stored in plaintext.** `sql/database.sql` defines the `ssn` column with the comment
"Should be encrypted in application," but nothing in the codebase actually encrypts it automatically — the
encryption infrastructure exists (`src/Common/Crypto/CryptoGen.php`) but isn't wired to that field. Combined
with zero built-in de-identification, every FHIR/REST response — and therefore every payload the Clinical
Co-Pilot would send to an LLM — contains full, unredacted patient identifiers unless we add filtering
ourselves. This is the single biggest architectural constraint on the agent design.

**3. There is no data retention policy and no breach detection/alerting.** OpenEMR retains patient records and
audit logs indefinitely by default, and the only way to discover unauthorized or anomalous access (e.g., an
agent or compromised credential bulk-reading records) is a human manually reviewing the `log`/`log_comment_encrypt`
audit tables. For an AI agent that will be issuing many automated data-access calls, we cannot rely on this
alone — the agent's own observability layer will need to carry the anomaly-detection burden.

Secondary findings (detailed below) include legacy SQL string-concatenation in the billing module, default
`openemr`/`openemr` database credentials in the template config, optional (non-enforced) MFA and password
policies, and a manual-only patient amendment workflow. None of these are blockers, but they inform the
architecture: any new API surface for the agent should use parameterized queries and the modern `Services/`
layer exclusively, must not reuse `CORSListener` as-is, must treat SSN and similarly sensitive fields as
requiring filtering before they ever reach an LLM prompt, and must implement its own request-level anomaly
logging rather than assuming OpenEMR's audit trail alone satisfies breach-detection requirements.

---

## Part 1: Security Audit

### 1.1 Authentication

| Finding | Severity | Evidence |
|---|---|---|
| Modern password hashing (bcrypt/argon2i/argon2id), configurable via `gbl_auth_hash_algo`, with automatic rehash on login if the configured algorithm changes | Informational (strength) | `src/Common/Auth/AuthHash.php:27-180` |
| Password complexity/expiration policies exist but default to **off** (`password_expiration_days` defaults to 0; no enforced complexity rules by default) | Medium | `src/Common/Auth/AuthUtils.php:1012-1098` |
| Brute-force protection: per-username and per-IP failed-login counters with lockout, configurable thresholds, admin unlock UI | Informational (strength) | `src/Common/Auth/AuthUtils.php:294-1416`; `library/ajax/login_counter_ip_tracker.php` |
| MFA (TOTP + U2F) is implemented and available, but **optional per user** — no global enforcement mechanism | Medium | `library/classes/Totp.class.php`; `src/Common/Auth/MfaUtils.php:75-83` |
| Example/legacy accounts with SHA1 password hashes exist in `sql/example_patient_users.sql`, and inactive service accounts (`phimail-service`, `portal-user`, `oe-system`) use the literal placeholder password "NoLogin" | Low | `sql/example_patient_users.sql:8-9`; `sql/official_additional_users.sql:1-4` — these are example/service files, not loaded into a production DB by default, and the service accounts are `active=0` |
| Default database credentials (`openemr`/`openemr`) ship in the template config | High | `sites/default/sqlconf.php:6-10` — must be changed at deployment; nothing in the app forces this |

### 1.2 Session Management

| Finding | Severity | Evidence |
|---|---|---|
| Core (non-API) sessions intentionally disable `HttpOnly` to support existing multi-tab/multi-session JS behavior | Medium | `src/Common/Session/SessionConfigurationBuilder.php:83-91` |
| OAuth2 and API sessions correctly set `Secure`, `HttpOnly`, and appropriate `SameSite` | Informational | `SessionConfigurationBuilder.php:94-111` |
| 4-hour default session lifetime with idle-timeout enforcement and audit-logged forced logout | Informational | `src/Common/Session/SessionUtil.php:21-96`; `library/auth.inc.php:107-118` |
| No explicit `session_regenerate_id()` on login; session-hijacking protection instead relies on comparing a stored password hash in-session | Medium | `src/Common/Auth/AuthUtils.php:837-861, 1526-1539` |

### 1.3 Authorization / Access Control

- Legacy phpGACL (`gacl/`, wrapped by `src/Gacl/GaclApi.php`) governs UI-level function/role permissions (e.g.,
  "can view encounters"); modern code increasingly uses `src/Common/Acl/AclMain.php` as a thinner wrapper over
  the same GACL data. This model is **role/function-based**, not inherently per-patient — facility assignment
  provides some data segmentation, but a user granted "view encounters" can generally view any patient's
  encounters within their facility scope, not just an assigned panel.
- The REST/FHIR API layer has **stronger, more granular** authorization: OAuth2 scopes are CRUDS + category
  filtered (e.g. `patient/Observation.rs?category=vital-signs`), and patient-context tokens are bound to a
  single patient UUID (`RestRequest::getPatientUUIDString()`), enforced in
  `src/RestControllers/Subscriber/AuthorizationListener.php`.
- **Implication for the Co-Pilot:** building the agent's data access on top of the FHIR/REST service layer (not
  by re-implementing GACL checks) gets us the more precise, already-audited authorization model for free.

### 1.4 API Security

| Finding | Severity | Evidence |
|---|---|---|
| **CORS reflects any `Origin` and allows credentials** — see Summary. | **Critical** | `src/RestControllers/Subscriber/CORSListener.php:56-57, 66-73` (confirmed directly; includes maintainers' own `// TODO` acknowledging the risk) |
| CSRF token coverage is broad across legacy form handlers (~280+ call sites use `CsrfUtils`/`checkCsrfInput`); the login form itself has no CSRF check (a common, defensible design choice since login doesn't rely on an existing session) | Low–Medium | `src/Common/Csrf/CsrfUtils.php`; `interface/login/login.php` |
| Rate limiting/brute-force lockout is real and configurable (see 1.1) | Informational | as above |
| No rate limiting specifically on the REST/FHIR API layer beyond the OAuth2 token lifecycle itself | Medium | no dedicated API rate-limiter found in `src/RestControllers/` |

### 1.5 Data Protection

| Finding | Severity | Evidence |
|---|---|---|
| SSN stored in plaintext despite a code comment saying it should be encrypted; other demographic PII (name, DOB, phone, address, driver's license) also plaintext in `patient_data` | **Critical / High** | `sql/database.sql:1245` (ssn), and adjacent column definitions |
| Real encryption-at-rest infrastructure exists (AES-256-CBC + HMAC-SHA384, key-versioned, two-tier DB+filesystem key storage) but is not automatically applied to sensitive fields — it must be deliberately invoked | High | `src/Common/Crypto/CryptoGen.php` |
| Legacy SQL built via string concatenation + `add_escape_custom()` (a `mysqli_real_escape_string` wrapper) instead of parameterized queries, concentrated in the billing module | High | `library/formdata.inc.php:24-29`; `interface/billing/edit_payment.php` (multiple sites) — modern `Services/` layer uses proper parameter binding throughout |
| A handful of legacy form handlers pass `$_GET`/`$_REQUEST` values toward output without visible escaping at the call site; most of the codebase consistently uses `text()`/`attr()`/`xlt()`/`js_escape()` helpers | Medium | e.g. `interface/forms/painmap/view.php:28`, `interface/forms/eye_mag/save.php:1247` |
| HTTPS is assumed/hardcoded in a couple of MFA code paths but not enforced anywhere at the application level (no HSTS header, no redirect-to-HTTPS logic); this is left entirely to the deployer/web-server config | Medium | `interface/usergroup/mfa_u2f.php:25`; `src/Common/Auth/MfaUtils.php:58` |
| File uploads are filtered via a MIME whitelist (`isWhiteFile()`, gated by the `secure_upload` global) but there's no evidence of at-rest encryption for stored documents | Medium | `src/Services/DocumentService.php:127-160`; `library/sanitize.inc.php:113` |

---

## Part 2: Compliance & Regulatory Audit

### 2.1 Audit Logging (HIPAA §164.312(b) — Audit Controls)

- Audit logging is **on by default**: `enable_auditlog` defaults to `1`, and critically, **read (SELECT) access
  to patient records is logged by default**, not just writes (`audit_events_query` defaults to `1`).
  (`library/globals.inc.php`; enforcement logic in `src/Common/Logging/EventAuditLogger.php:440-444`)
- Logged events include patient-record changes, HTTP request/page-view history, lab orders/results,
  scheduling, security/admin actions, and amendments, stored in `log`, `log_comment_encrypt`, `api_log`, and
  `extended_log` tables.
- Optional ATNA/syslog export (RFC 3881) exists but defaults to **off** (`enable_atna_audit` = `0`).
- **This is directly reusable** for the assignment's observability/traceability requirements — the Co-Pilot's
  own tool calls should be designed to also emit into (or alongside) this same audit trail rather than
  building a parallel, disconnected logging system.

### 2.2 Data Retention

- **No built-in retention or purge policy exists** for patient records, audit logs, or documents. Everything
  is retained indefinitely by default. The only related tooling found is a manual/optional weekly cron backup
  of the log table (`Documentation/README-Log-Backup.txt`) — a backup mechanism, not a retention/purge policy.
- **Risk:** indefinite retention increases the scope and duration of any future breach, and complicates any
  future data-minimization or "right to be forgotten"-style request. This needs an explicit organizational
  policy decision; OpenEMR will not enforce one for us.

### 2.3 Breach Notification

- **No built-in breach detection or alerting exists.** There is no anomalous-access detection (e.g., bulk
  export by one credential, after-hours access spikes) and no automated admin notification mechanism —
  `src/Common/Logging/BreakglassChecker.php` only flags a user as an emergency/"breakglass" user, it does not
  alert anyone. Breach discovery today would require manual review of the audit tables in 2.1.
- **Implication for the agent:** the observability/alerting layer we build for the Co-Pilot (the assignment's
  required p95-latency/error-rate/tool-failure alerts) should be extended to also cover anomalous data-access
  volume from the agent itself, since OpenEMR provides no equivalent safety net.

### 2.4 PHI-to-LLM-Provider / Business Associate Considerations

- OpenEMR has **zero de-identification or redaction capability** anywhere in the codebase (confirmed — no
  matches for de-identification/redaction/anonymization logic). Every FHIR/REST response returns full,
  identifiable patient data.
- Sending any of this data to a third-party LLM API is a HIPAA Business Associate relationship and requires a
  signed BAA with the LLM provider before any PHI is transmitted — this is an organizational/legal step, not a
  code change, and must happen before the agent goes live regardless of which provider is chosen.
- Because there's no in-app redaction, the Co-Pilot's own architecture will need to either (a) minimize what
  patient data is included in prompts to only what's needed to answer the specific question, and/or (b) add an
  explicit filtering/minimization layer before data reaches the LLM call. This should be a first-class decision
  in `ARCHITECTURE.md`, not an afterthought.

### 2.5 Patient Rights — Amendments

- The `amendments`/`amendments_history` tables and associated UI (`interface/patient_file/summary/*amendments*.php`)
  support HIPAA's patient-right-to-amend requirement, but the workflow is **entirely manual**: an admin user
  with the `amendment` ACL permission must create/approve/reject the amendment record by hand. There's no
  patient-facing self-service request flow or automated timeline tracking.

### 2.6 Bulk Export Auditing

- The FHIR `$export` bulk operation (`src/RestControllers/FHIR/Operations/FhirOperationExportRestController.php`)
  is access-controlled via OAuth2 system scopes (`system/Patient.$export`, etc.) and logs who/when at the
  **application log** level, but this is not integrated into the `audit_master`-style audit trail and has no
  per-patient granularity — a client with export scope can export all patients of that type in one call.

### 2.7 Backup / Disaster Recovery

- A backup mechanism exists (`interface/main/backup.php`, `contrib/util/backup_oemr.sh` — full DB + web
  directory tarball), but there's no documented retention period, no backup encryption, and no documented
  restore/testing procedure. Backups therefore carry the same plaintext-PHI exposure as the live database
  (see 1.5).

### 2.8 Project-Level Compliance Posture

- OpenEMR's own documentation (`Documentation/api/README.md`, `Documentation/api/DEVELOPER_GUIDE.md`) is
  explicit that regulatory compliance is the responsibility of the deploying/integrating organization — the
  project provides tools (audit logging, encryption primitives, ACLs) but does not claim HIPAA compliance out
  of the box. This audit should be read in that spirit: nothing here is a defect report against OpenEMR so
  much as a list of what we, as the deploying/integrating team, are responsible for configuring or building on
  top of before handling real PHI.

---

## What This Audit Changes About the Agent Plan

1. Any new agent-facing API endpoints reuse the modern `Services/` layer and FHIR/REST authorization model
   (patient-scoped OAuth2 tokens), not GACL directly and not the current `CORSListener` as-is.
2. Prompt construction must treat PHI minimization as an architectural requirement, not a nice-to-have, given
   there is no in-app de-identification to fall back on.
3. The agent's observability layer must include anomaly/volume alerting on its own data access, since OpenEMR
   provides no breach-detection safety net.
4. A BAA with whichever LLM provider is ultimately chosen is a hard prerequisite before any real PHI reaches
   the agent — independent of which provider or hosting environment is selected.
5. Fields like SSN should not be included in any data passed to the LLM unless explicitly required and
   filtered/encrypted appropriately, since they are stored (and would otherwise be retrieved) in plaintext.
