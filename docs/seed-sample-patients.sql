-- Seed script: realistic sample patients for local ED-resident Co-Pilot testing (Stage 1 / USER.md).
--
-- Grounded in the same table structures OpenEMR's own PHP business logic writes to
-- (src/Services/PatientService.php::databaseInsert, src/Services/EncounterService.php::insertEncounter,
-- QueryUtils::generateId() -> sequences table). `uuid` columns are intentionally left NULL: OpenEMR's
-- UuidRegistry lazily backfills missing UUIDs the first time a row is read via the REST/FHIR API
-- (src/Common/Uuid/UuidRegistry.php::createMissingUuidForRow / populateAllMissingUuids), so this is safe.
--
-- Usage:
--   /opt/homebrew/opt/mariadb/bin/mariadb -u willparks openemr < docs/seed-sample-patients.sql
--
-- Creates two patients:
--   Patient A "Maria Gonzalez" - rich chart: 2 prior encounters (routine + ED), chronic conditions,
--     a new diagnosis + new/discontinued medications, allergy, vitals, labs (one abnormal), and a note.
--     Exercises UC-1 through UC-5.
--   Patient B "James Whitfield" - thin chart: demographics only, no encounters/history at all.
--     Exercises UC-6 (honest "nothing on file" response).

-- ---------------------------------------------------------------------------
-- Patient A: Maria Gonzalez
-- ---------------------------------------------------------------------------
SET @pid_a = (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data);

INSERT INTO patient_data
  (fname, lname, DOB, sex, street, postal_code, city, state, country_code,
   phone_home, phone_cell, email, status, pubpid, pid, providerID, date, regdate)
VALUES
  ('Maria', 'Gonzalez', '1965-03-14', 'Female', '482 Willow Creek Dr', '78701', 'Austin', 'TX', 'USA',
   '5125550142', '5125550142', 'maria.gonzalez@example.com', 'married', @pid_a, @pid_a, 1,
   NOW(), '2016-05-01 09:00:00');

-- --- Encounter 1: older routine visit (7+ months prior) ---
UPDATE sequences SET id = id + 1;
SET @enc1 = (SELECT id FROM sequences);

INSERT INTO form_encounter
  (date, reason, facility_id, pid, encounter, pc_catid, provider_id, class_code)
VALUES
  ('2025-11-02 10:15:00', 'Follow-up: hypertension and diabetes management', 3, @pid_a, @enc1, 5, 1, 'AMB');

UPDATE sequences SET id = id + 1;
SET @form1 = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2025-11-02 10:15:00', @enc1, 'New Patient Encounter', @form1, @pid_a, 'admin', 1, 'newpatient');

-- --- Encounter 2: recent ED visit (2 days ago) ---
UPDATE sequences SET id = id + 1;
SET @enc2 = (SELECT id FROM sequences);

INSERT INTO form_encounter
  (date, reason, facility_id, pid, encounter, pc_catid, provider_id, class_code)
VALUES
  ('2026-07-05 23:40:00', 'Chest pain and palpitations, onset ~2 hours prior to arrival', 3, @pid_a, @enc2, 5, 1, 'EMER');

UPDATE sequences SET id = id + 1;
SET @form2 = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2026-07-05 23:40:00', @enc2, 'New Patient Encounter', @form2, @pid_a, 'admin', 1, 'newpatient');

-- --- Conditions (lists, type=medical_problem) ---
-- Chronic, present at both encounters:
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES (NOW(), 'medical_problem', 'Type 2 diabetes mellitus', '2016-05-01', 'ICD10:E11.9', 1, @pid_a, 'admin');
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES (NOW(), 'medical_problem', 'Essential (primary) hypertension', '2016-05-01', 'ICD10:I10', 1, @pid_a, 'admin');
-- New diagnosis at the recent ED encounter (drives UC-2's "what's new" diff):
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES ('2026-07-05 23:40:00', 'medical_problem', 'Atrial fibrillation, new onset', '2026-07-05', 'ICD10:I48.91', 1, @pid_a, 'admin');

-- --- Allergy (lists, type=allergy) ---
INSERT INTO lists (date, type, title, begdate, activity, reaction, pid, `user`)
VALUES ('2018-02-10 00:00:00', 'allergy', 'Penicillins', '2018-02-10', 1, 'Rash', @pid_a, 'admin');

-- --- Medications (prescriptions) ---
-- Ongoing since the older encounter:
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_a, 1, @enc1, '2016-05-01', 'Lisinopril 10mg tablet', '1 tablet', '30', 1, 3, 1, NOW(), 'admin', '2016-05-01', 'Outpatient', 'Order');
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_a, 1, @enc1, '2016-05-01', 'Metformin 500mg tablet', '1 tablet twice daily', '60', 2, 3, 1, NOW(), 'admin', '2016-05-01', 'Outpatient', 'Order');
-- Discontinued between visits (drives UC-2's "discontinued medications" diff):
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, end_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_a, 1, @enc1, '2018-01-15', '2026-06-01', 'Simvastatin 20mg tablet', '1 tablet at bedtime', '30', 1, 0, 0, NOW(), 'admin', '2018-01-15', 'Outpatient', 'Order');
-- Newly started at the recent ED encounter for the new afib diagnosis:
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_a, 1, @enc2, '2026-07-05', 'Metoprolol tartrate 25mg tablet', '1 tablet twice daily', '60', 2, 0, 1, NOW(), 'admin', '2026-07-05', 'Outpatient', 'Order');

-- --- Vitals at the recent ED encounter (elevated pulse/BP, borderline-low O2 sat) ---
INSERT INTO form_vitals
  (date, pid, `user`, activity, bps, bpd, weight, height, temperature, pulse, respiration, oxygen_saturation)
VALUES
  ('2026-07-05 23:45:00', @pid_a, 'admin', 1, '152', '94', '181', '65', '98.6', '118', '18', '94');
UPDATE sequences SET id = id + 1;
SET @formv = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2026-07-05 23:45:00', @enc2, 'Vitals', @formv, @pid_a, 'admin', 1, 'vitals');

-- --- Labs at the recent ED encounter (one abnormal-flagged result: low potassium) ---
INSERT INTO procedure_order (provider_id, patient_id, encounter_id, date_collected, date_ordered, order_status, procedure_order_type)
VALUES (1, @pid_a, @enc2, '2026-07-05 23:50:00', '2026-07-05 23:42:00', 'complete', 'laboratory_test');
SET @porder = LAST_INSERT_ID();
INSERT INTO procedure_order_code (procedure_order_id, procedure_order_seq, procedure_code, procedure_name, procedure_source)
VALUES (@porder, 1, '80048', 'Basic Metabolic Panel', '1');
INSERT INTO procedure_order_code (procedure_order_id, procedure_order_seq, procedure_code, procedure_name, procedure_source)
VALUES (@porder, 2, '10839-9', 'Troponin I', '1');

INSERT INTO procedure_report (procedure_order_id, procedure_order_seq, date_collected, date_report, report_status, review_status)
VALUES (@porder, 1, '2026-07-05 23:50:00', '2026-07-06 00:20:00', 'complete', 'reviewed');
SET @preport1 = LAST_INSERT_ID();
INSERT INTO procedure_result (procedure_report_id, result_data_type, result_code, result_text, `date`, units, result, `range`, abnormal, result_status)
VALUES (@preport1, 'N', '2823-3', 'Potassium', '2026-07-06 00:20:00', 'mEq/L', '3.3', '3.5-5.1', 'low', 'final');

INSERT INTO procedure_report (procedure_order_id, procedure_order_seq, date_collected, date_report, report_status, review_status)
VALUES (@porder, 2, '2026-07-05 23:50:00', '2026-07-06 00:20:00', 'complete', 'reviewed');
SET @preport2 = LAST_INSERT_ID();
INSERT INTO procedure_result (procedure_report_id, result_data_type, result_code, result_text, `date`, units, result, `range`, abnormal, result_status)
VALUES (@preport2, 'N', '10839-9', 'Troponin I', '2026-07-06 00:20:00', 'ng/mL', '0.02', '0.00-0.04', 'no', 'final');

-- --- Progress note tied to the ED encounter ---
INSERT INTO pnotes (date, body, pid, `user`, activity, authorized, title)
VALUES
  ('2026-07-05 23:42:00',
   'Pt presents with acute-onset palpitations and chest pain x2h, described as fluttering, non-exertional. Denies SOB at rest. PMHx significant for HTN and T2DM. New finding of irregularly irregular rhythm on exam, EKG pending read. Started metoprolol for rate control pending cardiology consult.',
   @pid_a, 'admin', 1, 1, 'ED Triage Note');

-- ---------------------------------------------------------------------------
-- Patient B: James Whitfield (thin chart — true first-time walk-in, UC-6)
-- ---------------------------------------------------------------------------
SET @pid_b = (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data);

INSERT INTO patient_data
  (fname, lname, DOB, sex, street, postal_code, city, state, country_code,
   phone_home, phone_cell, status, pubpid, pid, providerID, date, regdate)
VALUES
  ('James', 'Whitfield', '1990-01-22', 'Male', '', '', '', '', 'USA',
   '', '', 'single', @pid_b, @pid_b, 1, NOW(), NOW());

SELECT @pid_a AS maria_gonzalez_pid, @pid_b AS james_whitfield_pid;
