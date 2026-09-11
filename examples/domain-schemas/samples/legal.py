"""Legal schema: court opinions, enforcement actions and contract disputes.

The legal schema includes case, person, organization, law, court, date and
monetary_amount.

Sample data: synthetic legal documents. Parties, counsel, judges, case numbers
and awards are invented; nothing below is legal advice or a real filing.
"""

from __future__ import annotations

from samples.base import Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Document 1: Court Opinion Summary",
        meta="Case: Tech Antitrust Case (synthetic)",
        content="""
        UNITED STATES DISTRICT COURT
        NORTHERN DISTRICT OF CALIFORNIA

        COMPETITION BUREAU, Plaintiff,
        v.
        MEGACORP TECHNOLOGIES, INC., Defendant.

        Case No. 3:23-cv-01234-ABC

        ORDER GRANTING PRELIMINARY INJUNCTION

        Before the Court is the Bureau's Motion for Preliminary Injunction seeking
        to block MegaCorp Technologies' proposed acquisition of CloudStart, Inc.
        for $12.4 billion.

        Judge Sarah Martinez finds that the Bureau has demonstrated a likelihood
        of success on the merits under Section 7 of the Clayton Act. The
        acquisition would combine the two largest cloud infrastructure providers,
        controlling approximately 65% of the relevant market.

        The Court notes testimony from CEO Richard Chen acknowledging internal
        documents stating the acquisition would "eliminate our primary competitor."
        Expert witness Dr. Michael Torres from Brightwater University provided
        economic analysis showing likely price increases of 15-20%.

        MegaCorp's counsel, represented by Gibbons Dunne LLP, argued
        that the merger would create efficiencies benefiting consumers. However,
        the Court finds these efficiency claims speculative and unsubstantiated.

        ORDERED: The preliminary injunction is GRANTED. MegaCorp is enjoined from
        completing the acquisition pending trial on the merits, scheduled to
        commence April 15, 2025.

        IT IS SO ORDERED.
        Dated: January 18, 2025
        Hon. Sarah Martinez, United States District Judge
        """,
    ),
    Document(
        title="Document 2: Securities Enforcement Action",
        meta="Case: Securities Fraud Settlement (synthetic)",
        content="""
        SECURITIES REGULATOR
        LITIGATION RELEASE NO. 25789

        Regulator Charges Former Executives of BioVenture Therapeutics with
        Securities Fraud

        January 10, 2025 - The securities regulator today announced that former
        BioVenture Therapeutics CEO James Morrison and CFO Linda Park have agreed
        to pay $4.2 million and $1.8 million, respectively, to settle charges that
        they misled investors about the company's clinical trial results.

        According to the complaint filed in the U.S. District Court for
        the Southern District of New York, Morrison and Park made materially
        false statements regarding the efficacy of BioVenture's lead drug
        candidate, BVT-401, in Phase II trials for pancreatic cancer treatment.

        The complaint alleges that between March 2023 and August 2023, the
        defendants concealed negative safety data from investors while selling
        $28 million in personal stock holdings. When the true trial results were
        disclosed in September 2023, BioVenture's stock price fell 72%.

        "Executives who deceive investors about clinical trial data undermine
        the integrity of our capital markets," said Enforcement Division
        Director Gabriela Renwick.

        Without admitting or denying the allegations, Morrison and Park agreed
        to officer-and-director bars of ten and five years, respectively.
        Morrison also agreed to disgorgement of $8.5 million plus prejudgment
        interest.

        The investigation was led by attorneys from the regulator's New York
        Regional Office.
        """,
    ),
    Document(
        title="Document 3: Contract Dispute Summary",
        meta="Case: Commercial Licensing Arbitration (synthetic)",
        content="""
        COMMERCIAL ARBITRATION TRIBUNAL

        AWARD

        In the Matter of Arbitration Between:

        INNOVATECH SOLUTIONS, INC., Claimant
        and
        GLOBAL ENTERPRISES LLC, Respondent

        Tribunal Case No. 01-24-0003-8765

        Arbitrator: Hon. Robert Williams (Ret.)

        SUMMARY OF DISPUTE:

        Claimant InnovaTech Solutions seeks damages of $47.5 million arising
        from Respondent Global Enterprises' alleged breach of a Software
        Licensing Agreement dated February 1, 2022. InnovaTech alleges that
        Global Enterprises exceeded the licensed user count by deploying
        the software to 15,000 users rather than the contracted 2,500 users.

        InnovaTech was represented by Lathrop & Wates LLP (Partner: Jennifer
        Adams). Global Enterprises was represented by Kirkwood & Ellery LLP
        (Partner: David Thompson).

        FINDINGS:

        After reviewing the License Agreement, audit reports from an independent
        accounting firm, and testimony from both parties' IT directors, the
        Arbitrator finds that Global Enterprises materially breached Section 4.2
        of the Agreement.

        However, InnovaTech's damages calculation is rejected as speculative.
        The Arbitrator applies the standard licensing fee methodology.

        AWARD:

        Global Enterprises shall pay InnovaTech Solutions:
        - Compensatory damages: $18,750,000
        - Audit costs: $425,000
        - Attorneys' fees: $2,100,000
        - Interest at 5% per annum from date of breach

        Total Award: $21,275,000 plus accrued interest

        This Award is final and binding. Dated: January 5, 2025.
        """,
    ),
)

USE_CASES = """
    1. Case Law Research:
       - Find similar cases by subject matter
       - Track judicial reasoning patterns
       - Identify influential precedents

    2. Party & Attorney Analytics:
       - Track law firm success rates
       - Identify expert witnesses by specialty
       - Map attorney-judge relationships

    3. Regulatory Tracking:
       - Monitor enforcement actions
       - Track penalty trends
       - Identify compliance risks

    4. Contract Analysis:
       - Extract key terms and obligations
       - Identify risky clauses
       - Track dispute patterns

    5. Due Diligence:
       - Map litigation history
       - Identify regulatory exposure
       - Track settlement patterns
"""

NOTE = """
    IMPORTANT: these documents are synthetic and are for demonstration only.
    In real legal applications, ensure compliance with attorney-client privilege
    and other applicable confidentiality requirements.
"""

SAMPLE = SampleSet(
    schema_name="legal",
    title="Legal Document Analysis",
    blurb="Synthetic court opinion, enforcement action and arbitration award.",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Cases & Legal Proceedings", "EVENT", subtypes=("CASE",), limit=8),
        Highlight("Parties, Attorneys & Judges", "PERSON", limit=12),
        Highlight("Organizations (Parties, Courts, Firms)", "ORGANIZATION"),
        Highlight("Courts & Tribunals", "ORGANIZATION", subtypes=("COURT",), limit=5),
        Highlight("Laws & Regulations", "OBJECT", subtypes=("LAW",), limit=8),
        Highlight("Monetary Amounts", "OBJECT", subtypes=("MONETARY_AMOUNT",), limit=8),
    ),
    use_cases=USE_CASES,
    source_tag="legal_documents",
    readback_query="licensing breach arbitration",
    note=NOTE,
)
