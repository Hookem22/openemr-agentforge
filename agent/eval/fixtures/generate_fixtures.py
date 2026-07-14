"""Generates synthetic lab-PDF and intake-form fixture documents for the 4 patients seeded by
docs/seed-sample-patients.sql / docs/seed-additional-patients.sql -- for manual Stage 1 testing now,
and reusable as golden-set source documents in Stage 4.

Content is grounded in each patient's actual seeded chart (conditions, allergies, medications) so a
clinician reviewing extraction output against the real chart can sanity-check it. One fixture
(Robert Chen's lab PDF) is deliberately rendered in faint, low-contrast text to exercise the
low-confidence extraction path (W2_ARCHITECTURE.md's "vision extraction without invention" hard
problem) -- not every real-world scan is clean.

Run: `python agent/eval/fixtures/generate_fixtures.py` (writes into this same directory).
"""
from __future__ import annotations

import os

import fitz  # pymupdf

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def _write_pdf(filename: str, lines: list[str], font_size: float = 11, color: tuple[float, float, float] = (0, 0, 0)) -> None:
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontsize=font_size, color=color)
        y += font_size + 7
    doc.save(os.path.join(OUT_DIR, filename))
    doc.close()
    print(f"wrote {filename}")


def maria_gonzalez() -> None:
    _write_pdf(
        "maria_gonzalez_lab.pdf",
        [
            "Riverside Community Lab",
            "Patient: Maria Gonzalez     DOB: 1985-03-14",
            "Collected: 2026-07-12 08:15    Ordering: Dr. A. Reyes",
            "",
            "Test                     Result      Units      Reference Range    Flag",
            "Hemoglobin A1c           7.4         %          4.0-5.6            HIGH",
            "Glucose, Fasting         142         mg/dL      70-99              HIGH",
            "Sodium                   139         mmol/L     136-145",
            "Potassium                4.2         mmol/L     3.5-5.1",
            "Creatinine               0.9         mg/dL      0.6-1.3",
        ],
    )
    _write_pdf(
        "maria_gonzalez_intake.pdf",
        [
            "New Patient / Follow-Up Intake Form",
            "Name: Maria Gonzalez     DOB: 1985-03-14     Sex: F",
            "",
            "Chief concern: Increased thirst and fatigue over the past 3 weeks.",
            "",
            "Current medications:",
            "  - Lisinopril 10mg daily",
            "",
            "Allergies:",
            "  - Penicillin: rash",
            "",
            "Family history:",
            "  - Mother: type 2 diabetes",
            "  - Father: hypertension",
        ],
    )


def james_whitfield() -> None:
    _write_pdf(
        "james_whitfield_lab.pdf",
        [
            "Riverside Community Lab",
            "Patient: James Whitfield     DOB: 1990-11-02",
            "Collected: 2026-07-11 09:00    Ordering: Dr. A. Reyes",
            "",
            "Test                     Result      Units      Reference Range    Flag",
            "White Blood Cell Count   6.1         K/uL       4.5-11.0",
            "Hemoglobin               15.2        g/dL       13.5-17.5",
            "Platelets                240         K/uL       150-400",
            "Glucose, Fasting         88          mg/dL      70-99",
        ],
    )
    _write_pdf(
        "james_whitfield_intake.pdf",
        [
            "New Patient Intake Form",
            "Name: James Whitfield     DOB: 1990-11-02     Sex: M",
            "",
            "Chief concern: General wellness checkup, no complaints today.",
            "",
            "Current medications: None",
            "",
            "Allergies: None known",
            "",
            "Family history:",
            "  - No significant family history reported",
        ],
    )


def robert_chen() -> None:
    # Deliberately faint/low-contrast text -- simulates a poorly-scanned fax, to exercise the
    # low-confidence extraction path rather than every fixture being a clean render.
    _write_pdf(
        "robert_chen_lab.pdf",
        [
            "Metro Cardiology Lab (faxed copy)",
            "Patient: Robert Chen     DOB: 1968-06-22",
            "Collected: 2026-07-13 06:40    Ordering: Dr. L. Osei",
            "",
            "Test                     Result      Units      Reference Range    Flag",
            "Troponin I               0.09        ng/mL      0.00-0.04          HIGH",
            "Basic Metabolic Panel:",
            "  Sodium                 137         mmol/L     136-145",
            "  Potassium              4.6         mmol/L     3.5-5.1",
            "  Creatinine             1.1         mg/dL      0.6-1.3",
        ],
        color=(0.72, 0.72, 0.72),
    )
    _write_pdf(
        "robert_chen_intake.pdf",
        [
            "Follow-Up Visit Intake Form",
            "Name: Robert Chen     DOB: 1968-06-22     Sex: M",
            "",
            "Chief concern: Recurrent chest tightness, worse with exertion, over 2 days.",
            "",
            "Current medications:",
            "  - Atorvastatin 40mg at bedtime",
            "  - Aspirin 81mg daily",
            "  - Sulfamethoxazole/Trimethoprim DS twice daily (started for a UTI last week)",
            "",
            "Allergies:",
            "  - Sulfonamides (sulfa drugs): hives",
            "",
            "Family history:",
            "  - Father: myocardial infarction at age 58",
        ],
    )


def dorothy_simmons() -> None:
    _write_pdf(
        "dorothy_simmons_lab.pdf",
        [
            "Riverside Community Lab",
            "Patient: Dorothy Simmons     DOB: 1948-09-30",
            "Collected: 2026-07-13 07:50    Ordering: Dr. A. Reyes",
            "",
            "Test                     Result      Units      Reference Range    Flag",
            "Calcium                  9.4         mg/dL      8.5-10.2",
            "Vitamin D, 25-OH         28          ng/mL      30-100             LOW",
            "Basic Metabolic Panel:",
            "  Sodium                 141         mmol/L     136-145",
            "  Potassium              4.0         mmol/L     3.5-5.1",
        ],
    )
    _write_pdf(
        "dorothy_simmons_intake.pdf",
        [
            "Annual Wellness Visit Intake Form",
            "Name: Dorothy Simmons     DOB: 1948-09-30     Sex: F",
            "",
            "Chief concern: Annual wellness visit, no acute complaints.",
            "",
            "Current medications:",
            "  - Alendronate 70mg weekly",
            "",
            "Allergies:",
            "  - No Known Drug Allergies (NKDA) - verified at visit",
            "",
            "Family history:",
            "  - Mother: osteoporosis, hip fracture at age 75",
        ],
    )


if __name__ == "__main__":
    maria_gonzalez()
    james_whitfield()
    robert_chen()
    dorothy_simmons()
