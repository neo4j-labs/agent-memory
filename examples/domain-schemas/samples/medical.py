"""Medical schema: clinical documents and medical literature.

The medical schema includes disease, drug, symptom, procedure, body_part, gene
and organism.

Sample data: synthetic clinical notes and trial summaries. No real patient data
appears here, the investigational drug and its results are invented, and nothing
below is medical advice.
"""

from __future__ import annotations

from samples.base import Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Document 1: Clinical Case Summary",
        meta="Source: Internal Medicine Case Report (synthetic)",
        content="""
        Case Presentation: A 58-year-old male with a history of type 2 diabetes
        mellitus and hypertension presented to the emergency department with
        acute onset chest pain radiating to the left arm, accompanied by
        shortness of breath and diaphoresis.

        Physical examination revealed blood pressure of 165/95 mmHg, heart rate
        of 102 bpm, and bilateral crackles in the lung bases. An electrocardiogram
        showed ST-segment elevation in leads V1-V4, consistent with an anterior
        wall myocardial infarction.

        Laboratory findings included troponin I of 4.2 ng/mL (normal <0.04),
        BNP of 890 pg/mL, and creatinine of 1.4 mg/dL. The patient was started
        on aspirin, clopidogrel, and heparin infusion.

        Emergent cardiac catheterization revealed 95% occlusion of the left
        anterior descending artery. Percutaneous coronary intervention with
        drug-eluting stent placement was performed successfully.

        Post-procedure, the patient was started on atorvastatin, metoprolol,
        and lisinopril. Echocardiography showed left ventricular ejection
        fraction of 40% with anterior wall hypokinesis.

        Patient was discharged on day 4 with cardiac rehabilitation referral.
        """,
    ),
    Document(
        title="Document 2: Drug Development Report",
        meta="Source: Phase III Clinical Trial Summary (synthetic)",
        content="""
        VELOXIB Phase III Results: Investigational JAK Inhibitor for Rheumatoid
        Arthritis

        Primary Endpoint Met: Veloxib demonstrated superiority over placebo and
        non-inferiority to an active comparator in patients with moderate-to-severe
        rheumatoid arthritis at 24 weeks.

        Study Population: 1,247 patients enrolled across 180 sites in North America
        and Europe. Patients had inadequate response to methotrexate and were
        TNF-naive.

        Efficacy Results:
        - ACR20 response: Veloxib 68% vs placebo 34% (p<0.001)
        - ACR50 response: Veloxib 42% vs active comparator 39%
        - DAS28-CRP remission: 28% vs 24% (active comparator)

        Safety Profile:
        - Most common adverse events: nasopharyngitis (12%), upper respiratory
          tract infection (9%), and headache (7%)
        - Serious infections occurred in 2.1% of patients
        - Two cases of herpes zoster reactivation
        - No cases of tuberculosis or opportunistic infections
        - Cardiovascular events: 0.8% (similar to active comparator)

        Mechanism: Veloxib selectively inhibits JAK1 and JAK2, reducing
        inflammatory cytokine signaling including interleukin-6 and interferon-gamma.

        The regulator has granted priority review. Marketing applications have also
        been submitted in Europe and Canada.
        """,
    ),
    Document(
        title="Document 3: Genomic Analysis Report",
        meta="Source: Cancer Genetics Laboratory (synthetic)",
        content="""
        Comprehensive Genomic Profiling Report

        Patient: [Anonymized]
        Diagnosis: Metastatic Non-Small Cell Lung Cancer (Adenocarcinoma)
        Specimen: Lung biopsy (FFPE)

        Genomic Findings:

        1. EGFR Exon 19 Deletion (p.E746_A750del) - DETECTED
           Clinical significance: Sensitizing mutation. Patient is a candidate
           for EGFR tyrosine kinase inhibitors including osimertinib,
           erlotinib, or gefitinib.

        2. TP53 R248W Mutation - DETECTED
           Clinical significance: Loss of function mutation. Associated with
           worse prognosis but does not affect EGFR TKI response.

        3. MET Amplification (Copy Number: 8) - DETECTED
           Clinical significance: May confer resistance to EGFR TKIs. Consider
           combination therapy with a MET inhibitor such as capmatinib
           or tepotinib.

        4. PD-L1 Expression: 45% (TPS)
           Clinical significance: Qualifies for checkpoint inhibitor
           monotherapy if EGFR TKI resistance develops.

        Microsatellite Status: Stable (MSS)
        Tumor Mutational Burden: 6 mutations/Mb (Low)

        Recommended Next Steps:
        - Initiate EGFR TKI therapy per protocol
        - Monitor for MET-driven resistance
        - Consider liquid biopsy for ctDNA monitoring
        """,
    ),
)

USE_CASES = """
    1. Clinical Decision Support:
       - Identify drug-drug interactions
       - Match patients to clinical trials
       - Suggest treatment options based on genomics

    2. Drug Discovery:
       - Map drug-target relationships
       - Identify repurposing opportunities
       - Track adverse event patterns

    3. Disease Understanding:
       - Connect symptoms to diseases
       - Map disease-gene relationships
       - Track disease progression patterns

    4. Treatment Optimization:
       - Match biomarkers to therapies
       - Identify resistance mechanisms
       - Personalize treatment plans

    5. Literature Mining:
       - Extract relationships from publications
       - Track emerging treatments
       - Identify research gaps
"""

NOTE = """
    IMPORTANT: these documents are synthetic and are for demonstration only.
    In real healthcare applications, ensure compliance with HIPAA, GDPR and
    other applicable regulations before extracting from clinical text.
"""

SAMPLE = SampleSet(
    schema_name="medical",
    title="Healthcare Document Analysis",
    blurb="Synthetic clinical case, trial summary and genomic report.",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Diseases & Conditions", "OBJECT", subtypes=("DISEASE",)),
        Highlight("Drugs & Medications", "OBJECT", subtypes=("DRUG",)),
        Highlight("Symptoms & Signs", "OBJECT", subtypes=("SYMPTOM",), limit=8),
        Highlight("Procedures & Interventions", "OBJECT", subtypes=("PROCEDURE",), limit=8),
        Highlight("Body Parts & Anatomy", "OBJECT", subtypes=("BODY_PART",), limit=8),
        Highlight("Genes & Biomarkers", "OBJECT", subtypes=("GENE",), limit=8),
    ),
    use_cases=USE_CASES,
    source_tag="medical_documents",
    readback_query="myocardial infarction treatment",
    note=NOTE,
)
