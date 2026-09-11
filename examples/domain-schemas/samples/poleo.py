"""POLE+O schema: investigation / intelligence analysis.

The POLE+O model (Person, Object, Location, Event, Organization) is widely used
in law enforcement, intelligence and fraud investigation to track entities and
their relationships.

Sample data: a synthetic fraud investigation involving shell companies. Every
name, account and movement below is invented.
"""

from __future__ import annotations

from samples.base import RELATIONS, Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Document 1: Financial Intelligence Report",
        meta="Source: Financial Intelligence Report",
        content="""
        Subject: Suspicious Transaction Report - Operation Phantom

        Investigation into suspected money laundering operation centered on
        John Marcus Reynolds, CEO of Meridian Holdings LLC. Reynolds was observed
        meeting with Viktor Petrov at the Grand Continental Hotel in Zurich on
        March 15, 2024.

        Wire transfers totaling $2.3 million were traced from Meridian Holdings
        to offshore accounts at First Caribbean Trust in the Cayman Islands.
        The funds originated from Apex Trading Partners, a shell company registered
        in Delaware with no apparent business operations.

        A black Mercedes S-Class (license plate ZH-482991) registered to Meridian
        Holdings was observed at multiple meeting locations. Cell phone records
        indicate calls between Reynolds and known associate Maria Santos, who
        manages Cyprus-based Helios Investments.
        """,
    ),
    Document(
        title="Document 2: Field Surveillance Report",
        meta="Source: Field Surveillance Report",
        content="""
        Date: March 20, 2024
        Location: 1847 Harbor Drive, Miami, FL

        Subject John Reynolds arrived at the Oceanview Marina at 14:32 aboard
        a 45-foot yacht named "Sea Shadow" (registration FL-8827-MK). He was
        accompanied by two unidentified males in business attire.

        Reynolds met with Carlos Mendez, previously flagged in Operation Nightfall
        for suspected drug trafficking connections. The meeting lasted approximately
        45 minutes at the marina's private club.

        A laptop computer and multiple document folders were exchanged during the
        meeting. Reynolds departed at 16:15, traveling to Miami International Airport
        where he boarded a private jet (tail number N482JR) with flight plan filed
        to Nassau, Bahamas.
        """,
    ),
    Document(
        title="Document 3: Corporate Registry Analysis",
        meta="Source: Corporate Registry Analysis",
        content="""
        Entity Analysis: Meridian Holdings LLC Network

        Meridian Holdings LLC (Delaware, incorporated 2019) lists John Reynolds
        as sole director. The registered agent is Smith & Associates Legal Services
        at 100 Corporate Plaza, Wilmington, DE.

        Subsidiary relationships identified:
        - Apex Trading Partners (Delaware) - 100% owned
        - Pacific Rim Ventures (Nevada) - 60% owned
        - Northern Star Logistics (Wyoming) - 100% owned

        Cross-reference with the offshore leaks database shows Viktor Petrov as
        beneficial owner of Helios Investments (Cyprus), which holds minority
        stakes in Pacific Rim Ventures.

        Bank Secrecy Act filing from First National Bank of Miami flagged
        structured deposits totaling $890,000 across 12 transactions in February 2024.
        """,
    ),
)

USE_CASES = """
    1. Network Analysis:
       - Map subjects to the organisations they control
       - Surface shared addresses, vehicles and phone numbers
       - Trace beneficial ownership across jurisdictions

    2. Event Reconstruction:
       - Place subjects at meetings and locations over time
       - Link surveillance reports to the same subject
       - Follow funds between accounts and entities

    3. Lead Generation:
       - Find entities one hop from a flagged subject
       - Cluster cases that share an entity
       - Rank subjects by number of corroborating sources
"""

SAMPLE = SampleSet(
    schema_name="poleo",
    title="Investigation / Intelligence Analysis",
    blurb="Synthetic fraud investigation reports (POLE+O model).",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Key PERSON entities", "PERSON", limit=5),
        Highlight("Key ORGANIZATION entities", "ORGANIZATION", limit=5),
        Highlight("Key LOCATION entities", "LOCATION", limit=5),
        Highlight("Key OBJECT entities", "OBJECT", limit=5, show_subtype=True),
    ),
    use_cases=USE_CASES,
    source_tag="operation_phantom_investigation",
    readback_query="shell company network",
    demos=(RELATIONS,),
    per_type_limit=12,
)
