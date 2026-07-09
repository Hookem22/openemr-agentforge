-- Seed script: two more sample patients extending docs/seed-sample-patients.sql, targeting
-- use-case coverage that Maria Gonzalez / James Whitfield don't exercise yet (see USER.md).
--
-- Usage:
--   /opt/homebrew/opt/mariadb/bin/mariadb -u willparks openemr < docs/seed-additional-patients.sql
--
-- Creates two patients:
--   Patient C "Robert Chen" - multi-problem chart with an UNRELATED chronic condition (knee
--     osteoarthritis) alongside cardiac history, to test UC-5's relevance filtering (a chest-pain
--     complaint should surface the cardiac history, not the knee history). Also carries a
--     deliberate, purely-structural drug/allergy conflict (prescribed a sulfa antibiotic despite a
--     documented sulfa allergy, with no note calling it out) to test UC-3's clinical-constraint
--     flagging -- the agent must catch this from the raw data, not from being told. Three
--     encounters over time exercise UC-2's diff logic beyond a single old/new pair, and an
--     abnormal troponin exercises UC-4.
--   Patient D "Dorothy Simmons" - stale, thin-but-not-empty chart: a single encounter over two
--     years old (tests "how old is this info" honesty) and an explicit "no known drug allergies"
--     entry (as opposed to James Whitfield, who has zero allergy rows at all). This distinguishes
--     UC-6's "verified absent" case from the plain "not recorded" case.

-- ---------------------------------------------------------------------------
-- Patient C: Robert Chen
-- ---------------------------------------------------------------------------
SET @pid_c = (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data);

INSERT INTO patient_data
  (fname, lname, DOB, sex, street, postal_code, city, state, country_code,
   phone_home, phone_cell, email, status, pubpid, pid, providerID, date, regdate)
VALUES
  ('Robert', 'Chen', '1968-09-02', 'Male', '1190 Riverside Pkwy', '78704', 'Austin', 'TX', 'USA',
   '5125550198', '5125550198', 'robert.chen@example.com', 'married', @pid_c, @pid_c, 1,
   NOW(), '2024-03-10 08:30:00');

-- --- Encounter 1: old, unrelated ortho visit ---
UPDATE sequences SET id = id + 1;
SET @enc_c1 = (SELECT id FROM sequences);
INSERT INTO form_encounter
  (date, reason, facility_id, pid, encounter, pc_catid, provider_id, class_code)
VALUES
  ('2024-03-10 08:30:00', 'Right knee pain, osteoarthritis follow-up', 3, @pid_c, @enc_c1, 5, 1, 'AMB');
UPDATE sequences SET id = id + 1;
SET @form_c1 = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2024-03-10 08:30:00', @enc_c1, 'New Patient Encounter', @form_c1, @pid_c, 'admin', 1, 'newpatient');

-- --- Encounter 2: prior cardiac ED visit, ~11 months ago ---
UPDATE sequences SET id = id + 1;
SET @enc_c2 = (SELECT id FROM sequences);
INSERT INTO form_encounter
  (date, reason, facility_id, pid, encounter, pc_catid, provider_id, class_code)
VALUES
  ('2025-08-20 14:10:00', 'Chest pain with exertion, ruled out for ACS', 3, @pid_c, @enc_c2, 5, 1, 'EMER');
UPDATE sequences SET id = id + 1;
SET @form_c2 = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2025-08-20 14:10:00', @enc_c2, 'New Patient Encounter', @form_c2, @pid_c, 'admin', 1, 'newpatient');

-- --- Encounter 3: recent ED visit ---
UPDATE sequences SET id = id + 1;
SET @enc_c3 = (SELECT id FROM sequences);
INSERT INTO form_encounter
  (date, reason, facility_id, pid, encounter, pc_catid, provider_id, class_code)
VALUES
  ('2026-07-06 21:05:00', 'Recurrent chest pain with shortness of breath', 3, @pid_c, @enc_c3, 5, 1, 'EMER');
UPDATE sequences SET id = id + 1;
SET @form_c3 = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2026-07-06 21:05:00', @enc_c3, 'New Patient Encounter', @form_c3, @pid_c, 'admin', 1, 'newpatient');

-- --- Conditions: cardiac (relevant to tonight) + unrelated orthopedic (should NOT surface for a chest-pain query) ---
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES ('2024-03-10', 'medical_problem', 'Osteoarthritis, right knee', '2024-03-10', 'ICD10:M17.11', 1, @pid_c, 'admin');
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES ('2025-08-20', 'medical_problem', 'Coronary artery disease', '2025-08-20', 'ICD10:I25.10', 1, @pid_c, 'admin');
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES ('2025-08-20', 'medical_problem', 'Hyperlipidemia', '2025-08-20', 'ICD10:E78.5', 1, @pid_c, 'admin');

-- --- Allergy ---
INSERT INTO lists (date, type, title, begdate, activity, reaction, pid, `user`)
VALUES ('2015-01-01 00:00:00', 'allergy', 'Sulfonamides (sulfa drugs)', '2015-01-01', 1, 'Hives', @pid_c, 'admin');

-- --- Medications: ongoing cardiac meds + a deliberate sulfa/allergy conflict, newly ordered tonight ---
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_c, 1, @enc_c2, '2025-08-20', 'Atorvastatin 40mg tablet', '1 tablet at bedtime', '30', 1, 3, 1, NOW(), 'admin', '2025-08-20', 'Outpatient', 'Order');
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_c, 1, @enc_c2, '2025-08-20', 'Aspirin 81mg tablet', '1 tablet daily', '30', 1, 5, 1, NOW(), 'admin', '2025-08-20', 'Outpatient', 'Order');
-- Deliberate conflict: this drug is in a class (sulfonamide) the patient has a documented allergy
-- to. No note/flag calls this out anywhere -- it exists purely as structured data so an agent must
-- cross-reference the allergy list itself rather than being told about the conflict.
INSERT INTO prescriptions
  (patient_id, provider_id, encounter, start_date, drug, dosage, quantity, `interval`, refills, active, `datetime`, `user`, txDate, usage_category_title, request_intent_title)
VALUES
  (@pid_c, 1, @enc_c3, '2026-07-06', 'Sulfamethoxazole/Trimethoprim DS tablet', '1 tablet twice daily', '20', 2, 0, 1, NOW(), 'admin', '2026-07-06', 'Outpatient', 'Order');

-- --- Vitals at the recent encounter ---
INSERT INTO form_vitals
  (date, pid, `user`, activity, bps, bpd, weight, height, temperature, pulse, respiration, oxygen_saturation)
VALUES
  ('2026-07-06 21:10:00', @pid_c, 'admin', 1, '148', '90', '198', '70', '98.4', '102', '20', '96');
UPDATE sequences SET id = id + 1;
SET @formv_c = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2026-07-06 21:10:00', @enc_c3, 'Vitals', @formv_c, @pid_c, 'admin', 1, 'vitals');

-- --- Labs at the recent encounter: elevated troponin ---
INSERT INTO procedure_order (provider_id, patient_id, encounter_id, date_collected, date_ordered, order_status, procedure_order_type)
VALUES (1, @pid_c, @enc_c3, '2026-07-06 21:20:00', '2026-07-06 21:07:00', 'complete', 'laboratory_test');
SET @porder_c = LAST_INSERT_ID();
INSERT INTO procedure_order_code (procedure_order_id, procedure_order_seq, procedure_code, procedure_name, procedure_source)
VALUES (@porder_c, 1, '10839-9', 'Troponin I', '1');

INSERT INTO procedure_report (procedure_order_id, procedure_order_seq, date_collected, date_report, report_status, review_status)
VALUES (@porder_c, 1, '2026-07-06 21:20:00', '2026-07-06 22:05:00', 'complete', 'reviewed');
SET @preport_c = LAST_INSERT_ID();
INSERT INTO procedure_result (procedure_report_id, result_data_type, result_code, result_text, `date`, units, result, `range`, abnormal, result_status)
VALUES (@preport_c, 'N', '10839-9', 'Troponin I', '2026-07-06 22:05:00', 'ng/mL', '0.15', '0.00-0.04', 'high', 'final');

-- ---------------------------------------------------------------------------
-- Patient D: Dorothy Simmons (stale, thin-but-not-empty chart)
-- ---------------------------------------------------------------------------
SET @pid_d = (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data);

INSERT INTO patient_data
  (fname, lname, DOB, sex, street, postal_code, city, state, country_code,
   phone_home, phone_cell, email, status, pubpid, pid, providerID, date, regdate)
VALUES
  ('Dorothy', 'Simmons', '1942-11-30', 'Female', '760 Maple St', '78745', 'Austin', 'TX', 'USA',
   '5125550177', '', 'dsimmons@example.com', 'widowed', @pid_d, @pid_d, 1,
   NOW(), '2023-02-14 09:00:00');

-- --- Single, over-two-years-stale encounter ---
UPDATE sequences SET id = id + 1;
SET @enc_d1 = (SELECT id FROM sequences);
INSERT INTO form_encounter
  (date, reason, facility_id, pid, encounter, pc_catid, provider_id, class_code)
VALUES
  ('2023-02-14 09:00:00', 'Routine visit, medication reconciliation', 3, @pid_d, @enc_d1, 5, 1, 'AMB');
UPDATE sequences SET id = id + 1;
SET @form_d1 = (SELECT id FROM sequences);
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, authorized, formdir)
VALUES ('2023-02-14 09:00:00', @enc_d1, 'New Patient Encounter', @form_d1, @pid_d, 'admin', 1, 'newpatient');

-- --- Chronic condition, stable, no follow-up encounters since ---
INSERT INTO lists (date, type, title, begdate, diagnosis, activity, pid, `user`)
VALUES ('2023-02-14', 'medical_problem', 'Osteoporosis', '2020-06-01', 'ICD10:M81.0', 1, @pid_d, 'admin');

-- --- Explicit "no known drug allergies" entry -- a verified-absent record, distinct from
--     James Whitfield's chart, which has zero allergy rows at all (truly unrecorded/unknown). ---
INSERT INTO lists (date, type, title, begdate, activity, reaction, pid, `user`)
VALUES ('2023-02-14 09:00:00', 'allergy', 'No Known Drug Allergies (NKDA) - verified at visit', '2023-02-14', 1, '', @pid_d, 'admin');

SELECT @pid_c AS robert_chen_pid, @pid_d AS dorothy_simmons_pid;
