# Clinical Co-Pilot Agent (dev skeleton)

Python/FastAPI + LangGraph service implementing the design in `../Gauntlet/Week 1/ARCHITECTURE.md`. Currently a
standalone service tested with a manually-obtained dev token — the real per-session auth-bridge
endpoint (OpenEMR side) hasn't been built yet; see `../` memory `agent-implementation.md` for the
build-order rationale.

## Local setup

```bash
cd agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY and DEV_BEARER_TOKEN
```

### Getting a DEV_BEARER_TOKEN

OpenEMR's OAuth2 server already supports a password grant (disabled by default — this is a dev-only
convenience, never use password grant in production, see `Documentation/api/AUTHENTICATION.md`).

1. One-time: enable the REST/FHIR APIs and password grant, and set the OAuth site address, e.g.:
   ```sql
   UPDATE globals SET gl_value='1' WHERE gl_name IN ('rest_api','rest_fhir_api','oauth_password_grant');
   UPDATE globals SET gl_value='http://localhost:8080' WHERE gl_name='site_addr_oath';
   ```
2. Register a confidential OAuth2 client (`application_type: "private"` is what makes it
   confidential and eligible for `user/*` scopes):
   ```bash
   curl -X POST http://localhost:8080/oauth2/default/registration \
     -H 'Content-Type: application/json' \
     --data '{
       "client_name": "Clinical Co-Pilot (dev)",
       "application_type": "private",
       "redirect_uris": ["http://localhost:8080/callback"],
       "grant_types": ["password", "refresh_token"],
       "scope": "openid offline_access api:oemr api:fhir user/Patient.read user/Encounter.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/DocumentReference.read"
     }'
   ```
3. Newly-registered clients need manual approval (`is_enabled=0` by default). Approve it:
   ```sql
   UPDATE oauth_clients SET is_enabled=1 WHERE client_name='Clinical Co-Pilot (dev)';
   ```
4. Request a token:
   ```bash
   curl -X POST http://localhost:8080/oauth2/default/token \
     -H 'Content-Type: application/x-www-form-urlencoded' \
     --data-urlencode 'grant_type=password' \
     --data-urlencode 'client_id=YOUR_CLIENT_ID' \
     --data-urlencode 'client_secret=YOUR_CLIENT_SECRET' \
     --data-urlencode 'scope=openid offline_access api:oemr api:fhir user/Patient.read user/Encounter.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/DocumentReference.read' \
     --data-urlencode 'user_role=users' \
     --data-urlencode 'username=admin' \
     --data-urlencode 'password=pass'
   ```
   The token expires in ~1 hour (`expires_in`); re-run this step to get a new one.

### Run the service

```bash
uvicorn app.main:app --reload --port 8000
```

### Try it

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  --data '{"patient_id": "<Maria Gonzalez FHIR id>", "message": "What is on file for this patient?"}'
```

Get a patient's FHIR id via `curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/apis/default/fhir/Patient`.
