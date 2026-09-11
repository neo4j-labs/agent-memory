"""Podcast schema: transcripts, interviews and conversational content.

The podcast schema is tuned for business/tech shows: person, company, product,
concept, book, technology, role and metric.

Sample data: synthetic tech-podcast excerpts. The hosts, guests, companies and
products are invented, and so is every quote and figure. A handful of real,
widely used technology names (Kubernetes, TypeScript, React) are kept so the
schema has recognisable `technology` spans to find; no claim here is
attributable to any real person or company.
"""

from __future__ import annotations

from samples.base import BATCH, Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Episode 1: Growth Strategies with Elena Rodriguez",
        meta="Guest: Elena Rodriguez, VP of Growth at Northwind Payments",
        content="""
        Host: Elena, you've been at Northwind Payments for five years now and led
        their expansion into Latin America. What was the key insight that drove
        that growth?

        Elena: Great question. When I joined Northwind Payments, we were primarily
        focused on the US market. But I had experience at Pampas Market before, and
        I knew the opportunity in Brazil and Mexico was massive. The key was
        understanding that product-market fit looks different in emerging markets.

        We launched Northwind Launchpad specifically for entrepreneurs in these
        regions, and it was a game-changer. Our co-founder Aiden Fournier was
        incredibly supportive of the initiative. We grew from zero to processing
        over $10 billion in payments within three years.

        Host: That's incredible. You mentioned product-market fit - how do you
        measure that? Do you use a PMF survey?

        Elena: Absolutely. We use the "very disappointed" question religiously.
        But we also track activation metrics closely. At Northwind Payments, we
        define activation as the first successful API call. For Northwind
        Launchpad, it's when a founder receives their company registration number.
        """,
    ),
    Document(
        title="Episode 2: Building AI Products with Marcus Chen",
        meta="Guest: Marcus Chen, Co-founder & CTO at Helix Safety Labs",
        content="""
        Host: Marcus, Helix Safety Labs has become one of the leading AI safety
        companies. Can you tell us about the journey from leaving Corvus Research
        to starting Helix?

        Marcus: When Ada Whitfield, my co-founder, and I were at Corvus Research,
        we were working on large language models. We saw the potential but also the
        risks. That's when we decided to start Helix Safety Labs with a focus on
        AI safety.

        We developed Charter Learning as our core approach. The idea is to train
        AI systems using principles rather than just examples. Vela, our
        assistant, is built on this foundation.

        Host: How does Charter Learning differ from RLHF?

        Marcus: RLHF - Reinforcement Learning from Human Feedback - requires
        massive amounts of human annotation. Charter Learning is more scalable.
        We define a charter of principles, and the model learns to follow
        them through self-supervision.

        We recently published a paper about this approach at a safety workshop.
        The key finding was that models trained with Charter Learning showed 40%
        fewer harmful outputs than our own baseline model.
        """,
    ),
    Document(
        title="Episode 3: Scaling Engineering Teams with Priya Sharma",
        meta="Guest: Priya Sharma, VP of Engineering at Quartzline",
        content="""
        Host: Priya, Quartzline went from a small team to over 800 people after the
        acquisition fell through. How do you maintain engineering velocity
        at that scale?

        Priya: It's all about organizational design. Noor Haddad, our CEO, gave
        me a lot of autonomy to restructure the engineering org. We adopted a
        squad model and adapted it to our needs.

        Each squad owns a specific area - like the multiplayer editing engine or
        the component library. We use Linear for project management and have
        weekly engineering all-hands on Zoom.

        Host: I've heard great things about Quartzline's developer experience. What
        tools do you use internally?

        Priya: We're big believers in developer productivity. We built our own
        internal platform called DevX that handles CI/CD, feature flags, and
        observability. It's built on Kubernetes and uses Datadog for monitoring.

        We also heavily use TypeScript and React. Our design-to-code workflow
        integrates directly with our Storybook components. Engineers can literally
        copy production-ready code from the design specs.

        Host: Have you read "Team Topologies" by Matthew Skelton? It sounds like
        you're implementing many of those patterns.

        Priya: Yes! It's required reading for our engineering managers. The
        concept of stream-aligned teams versus platform teams really shaped how
        we think about organization.
        """,
    ),
)

USE_CASES = """
    1. Episode Discovery:
       - "Which episodes discuss activation metrics?"
       - Find every guest who mentioned a given product
       - Cluster episodes by the concepts they cover

    2. Guest & Company Graph:
       - Map guests to the companies and roles they held
       - Surface the products each company shipped
       - Track which books and frameworks guests recommend

    3. Recommendation:
       - Suggest episodes that share concepts with one a listener liked
       - Rank technologies by how often practitioners mention them
"""

SAMPLE = SampleSet(
    schema_name="podcast",
    title="Podcast Transcript Analysis",
    blurb="Synthetic tech-podcast transcript excerpts.",
    threshold=0.45,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Key People Mentioned", "PERSON"),
        Highlight("Companies & Organizations", "ORGANIZATION"),
        Highlight(
            "Products & Technologies",
            "OBJECT",
            subtypes=("PRODUCT", "TECHNOLOGY", "TOOL"),
            show_subtype=True,
        ),
        Highlight(
            "Concepts & Methodologies",
            "OBJECT",
            subtypes=("CONCEPT", "METHOD", "METRIC"),
        ),
        Highlight("Books Mentioned", "OBJECT", subtypes=("BOOK",), limit=5),
    ),
    use_cases=USE_CASES,
    source_tag="tech_podcast",
    readback_query="developer productivity platform",
    demos=(BATCH,),
    per_type_limit=5,
)
