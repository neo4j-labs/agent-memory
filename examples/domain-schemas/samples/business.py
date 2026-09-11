"""Business schema: earnings calls, market analysis and deal announcements.

The business schema focuses on companies, executives, products, industries,
financial metrics and business locations.

Sample data: synthetic business documents. The issuing firms, executives,
ratings and figures are invented — nothing here is investment research or
attributable to a real company. A few real chip and cloud brand names are kept
so the schema has recognisable `company`/`product` spans.
"""

from __future__ import annotations

from samples.base import Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Document 1: Earnings Call Transcript",
        meta="Source: TechVision Inc. | Date: Q4 2024",
        content="""
        Good afternoon, everyone. I'm Sarah Mitchell, CEO of TechVision Inc.,
        and joining me today is our CFO, Robert Chen.

        Q4 was a transformative quarter for TechVision. We achieved revenue of
        $2.4 billion, representing 34% year-over-year growth. Our cloud platform,
        TechVision Cloud, now serves over 50,000 enterprise customers, up from
        38,000 last year.

        Gross margin improved to 72%, driven by efficiencies in our AWS and
        Azure-hosted infrastructure. Operating margin reached 18%, exceeding
        our guidance of 15-17%.

        Our acquisition of DataStream Analytics, completed in October for
        $890 million, is already contributing to growth. DataStream's real-time
        analytics capabilities have enhanced our flagship product, Insight Pro.

        Looking ahead to 2025, we're raising guidance. We expect revenue of
        $11-11.5 billion, representing 25-30% growth. We're also announcing a
        new $500 million stock buyback program.

        I'll now turn it over to Robert for detailed financials.

        Robert Chen: Thank you, Sarah. Let me walk through the numbers. GAAP
        EPS was $1.87, above consensus of $1.72. Non-GAAP EPS was $2.14. Free
        cash flow reached $780 million, a record quarter.
        """,
    ),
    Document(
        title="Document 2: Market Analysis Report",
        meta="Source: Harborline Research | Date: January 2025",
        content="""
        SEMICONDUCTOR INDUSTRY OUTLOOK 2025

        Analyst: Jennifer Wong, Senior Technology Analyst

        Executive Summary: We maintain an Overweight rating on the semiconductor
        sector, with Aurora Silicon as our top pick. The AI chip market is projected
        to reach $150 billion by 2027, growing at 45% CAGR.

        Key Findings:

        Aurora Silicon continues to dominate the AI accelerator market with 82%
        share. Their new Basalt architecture, launching in Q2, offers 2x performance
        over the current generation. We raise our price target to $750 from $650.

        Vantage Micro is gaining traction with its VX300 chips. CEO Dana Ruiz has
        secured design wins at two hyperscale cloud providers. We upgrade Vantage
        Micro to Overweight with a $200 price target.

        Orion Foundry's turnaround faces headwinds. The foundry business lost
        $7 billion in 2024, and its next process node is delayed to late 2025.
        We maintain Underweight with a $25 target.

        Emerging Competitors:

        Waferline Systems and Tensile Compute are gaining attention in the startup
        space. Waferline's wafer-scale chip is being adopted by pharmaceutical
        companies for drug discovery.

        Geographic Risks:

        A single contract manufacturer in East Asia produces 90% of advanced chips.
        Geopolitical tension in the region remains the sector's biggest risk. Its
        Arizona fab is on track for 2025 production.
        """,
    ),
    Document(
        title="Document 3: Private Equity Deal Announcement",
        meta="Source: Summit Ridge Partners | Date: January 2025",
        content="""
        Summit Ridge Partners to Acquire CloudSecure for $8.5 Billion

        SAN FRANCISCO - Summit Ridge Partners announced today a definitive
        agreement to acquire CloudSecure Inc., a leading cybersecurity platform
        provider, for $8.5 billion in cash.

        Summit Ridge founder and CEO Robert Sinclair called the deal "a landmark
        investment in enterprise security." CloudSecure's CEO, Michelle Park, will
        continue leading the company post-acquisition.

        CloudSecure's platform protects over 2,000 large enterprises from
        cyber threats. The company achieved $1.2 billion in annual recurring
        revenue in 2024, growing 55% year-over-year.

        The transaction values CloudSecure at 7x revenue, a premium to peer
        companies in the sector, which trade at 5-6x.

        Summit Ridge will combine CloudSecure with its existing portfolio company,
        IdentityGuard, creating a comprehensive identity and security platform.
        The combined entity will be headquartered in Austin, Texas.

        Kestrel & Co. and Ridgeway Partners served as financial advisors to
        CloudSecure. Hale & Crane LLP provided legal counsel. The deal is expected
        to close in Q2 2025, subject to regulatory approval.
        """,
    ),
)

USE_CASES = """
    1. Competitive Intelligence:
       - Track competitor mentions and market positioning
       - Monitor executive changes and leadership commentary
       - Identify emerging threats and opportunities

    2. Investment Research:
       - Extract financial metrics (revenue, margins, growth)
       - Track analyst ratings and price targets
       - Monitor M&A activity and deal terms

    3. Market Mapping:
       - Identify industry participants and relationships
       - Track product launches and feature announcements
       - Map vendor-customer relationships

    4. Executive Tracking:
       - Monitor leadership changes
       - Track executive commentary and sentiment
       - Identify key decision-makers by company

    5. Supply Chain Analysis:
       - Map supplier relationships
       - Track geographic concentration risks
       - Monitor manufacturing locations
"""

SAMPLE = SampleSet(
    schema_name="business",
    title="Business Document Analysis",
    blurb="Synthetic earnings call, market analysis and M&A announcement.",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Companies", "ORGANIZATION", limit=12),
        Highlight("Executives & Key People", "PERSON"),
        Highlight(
            "Products & Platforms",
            "OBJECT",
            subtypes=("PRODUCT", "TECHNOLOGY"),
            show_subtype=True,
        ),
        Highlight("Business Locations", "LOCATION", limit=8),
    ),
    use_cases=USE_CASES,
    source_tag="business_documents",
    readback_query="semiconductor market outlook",
)
