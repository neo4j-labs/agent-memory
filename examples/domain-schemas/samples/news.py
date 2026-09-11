"""News schema: articles, press releases and journalism.

The news schema focuses on people, organizations, locations, events and dates.

Sample data: synthetic news articles. Datelines, quotes and figures are invented
for demonstration; nothing here reports a real event.
"""

from __future__ import annotations

from samples.base import RELATIONS, Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Article 1: Tech Firms Face New Data-Portability Rules",
        meta="Source: Global Business Times | Date: January 15, 2025",
        content="""
        BRUSSELS - The regional competition authority announced sweeping new
        regulations targeting major technology companies on Monday, marking the most
        significant expansion of its digital services rules since implementation.

        Commissioner Helena Vos stated that the four largest platform operators
        must comply with new data portability requirements by June 2025.
        "Citizens deserve control over their digital lives," Vos said during a
        press conference at the regional parliament.

        The regulations require tech companies to allow users to export their
        data in standardized formats and prohibit certain algorithmic practices
        that the authority deems anticompetitive.

        Nadia Okafor, CEO of platform operator Lumen Systems, expressed concern
        about the timeline in a statement released Tuesday. "While we support the
        goals of data portability, the implementation deadline presents significant
        technical challenges."

        The announcement sent technology shares tumbling, with the sector index
        falling 2.3% in afternoon trading. Analysts at Harborline Research estimate
        compliance costs could reach $5 billion annually across affected companies.
        """,
    ),
    Document(
        title="Article 2: Climate Summit Reaches Historic Agreement",
        meta="Source: World News Network | Date: January 18, 2025",
        content="""
        DUBAI - World leaders reached a landmark climate agreement at the annual
        climate conference on Saturday, committing to a 60% reduction in
        global emissions by 2035.

        Summit chair Amara Diallo called the agreement "a turning point for
        humanity" during the closing ceremony. The deal was brokered after intense
        negotiations between the three largest emitting blocs.

        Delegates praised the agreement, with lead negotiator Tomas Lindqvist
        stating, "This proves that when nations work together, we can tackle the
        greatest challenges of our time." The delegation from the Pacific Alliance
        committed to tripling renewable energy capacity by 2030.

        Environmental groups had mixed reactions. Ocean Futures director Jennifer
        Morgan-Hale welcomed the emissions targets but criticized the lack of
        specific enforcement mechanisms.

        The agreement includes a $500 billion climate fund, with contributions
        from wealthy nations to help developing countries transition to clean
        energy. Several delegations successfully negotiated provisions for "just
        transition" financing.

        The next major review conference is scheduled for Glasgow in 2027.
        """,
    ),
    Document(
        title="Article 3: Earthquake Strikes Coastal Prefecture",
        meta="Source: Asia Pacific News | Date: January 20, 2025",
        content="""
        TOKYO - A powerful 7.6 magnitude earthquake struck a coastal peninsula
        early Sunday morning, causing widespread damage and triggering tsunami
        warnings across the inland sea.

        Regional governor Haruki Sato declared a state of emergency and mobilized
        emergency response units for rescue operations. "Our top priority is
        saving lives," Sato told reporters at an emergency press conference.

        The national meteorological agency reported the earthquake struck at 4:10 AM
        local time, with the epicenter located 10 kilometers beneath Wajima City.
        Aftershocks continued throughout the day, with at least 20 measuring
        above magnitude 5.0.

        Prefecture officials reported significant damage to infrastructure,
        including collapsed buildings in Suzu and Nanao cities. The Kenrokuen
        Garden in Kanazawa sustained minor damage.

        Relief agencies dispatched emergency medical teams to the affected region.
        International aid offers arrived from three neighbouring countries within
        hours of the disaster.

        The regional power utility reported no damage to nuclear facilities,
        though several thermal power plants were temporarily shut down as a
        precautionary measure.
        """,
    ),
)

USE_CASES = """
    1. Event Tracking:
       - Track earthquake aftermath and rescue operations
       - Monitor climate agreement implementation
       - Follow regulatory compliance deadlines

    2. Entity Monitoring:
       - Track mentions of specific people across articles
       - Monitor company news over time
       - Follow government actions

    3. Geographic Analysis:
       - Map news by location (Brussels, Dubai, Tokyo)
       - Track regional impacts of global events
       - Identify hotspots of activity

    4. Relationship Discovery:
       - Person-Organization connections
       - Organization-Event participation
       - Geographic event clustering
"""

SAMPLE = SampleSet(
    schema_name="news",
    title="News Article Analysis",
    blurb="Synthetic news articles (regulation, climate, disaster reporting).",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Key People in the News", "PERSON"),
        Highlight("Organizations Mentioned", "ORGANIZATION"),
        Highlight("Locations", "LOCATION"),
        Highlight("Events", "EVENT", limit=5),
    ),
    use_cases=USE_CASES,
    source_tag="news_articles",
    readback_query="climate agreement",
    demos=(RELATIONS,),
)
