"""Entertainment schema: film, television and awards coverage.

The entertainment schema includes actor, director, film, tv_show, character,
award, studio and genre.

Sample data: synthetic reviews and entertainment news. The films, shows,
performers, studios and awards below are invented, as are all ratings and
nominations.
"""

from __future__ import annotations

from samples.base import Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Content 1: Sand Kings: Part Three - Review",
        meta="Type: Movie Review",
        content="""
        Director Dominic Vaillant delivers another masterpiece with Sand Kings:
        Part Three, the epic conclusion to his adaptation of Francis Hale's
        beloved sci-fi novel. Theo Marchand returns as Paul Varian, now fully
        embracing his role as the messianic leader of the Dunewalkers.

        The film opens with a stunning 20-minute battle sequence shot entirely
        in large format by cinematographer Greer Fraser. Zaya Nakamura's Chani
        gets significantly more screen time, and her performance anchors the
        emotional core of the film.

        New additions to the cast include Oskar Elias returning in flashback
        sequences and Florence Pennington as Princess Irulan. Javier Barden's
        Stilgar provides unexpected comic relief while Christopher Welkin brings
        gravitas to Emperor Shaddam IV.

        Composer Hal Zimmerman's score is even more ambitious than the previous
        installments, incorporating traditional desert instrumentation with his
        signature electronic soundscapes.

        Ridgeline Pictures and Legendary Dunes have crafted a fitting conclusion
        that should satisfy both fans of the book and newcomers to the franchise.
        This is bold, challenging science fiction that demands to be seen on the
        biggest screen possible.

        Rating: 9.5/10
        """,
    ),
    Document(
        title="Content 2: The Kitchen Season 4 Preview",
        meta="Type: TV Show Analysis",
        content="""
        Streaming network Vantage has renewed The Kitchen for a fourth season,
        with showrunner Christopher Stoller promising an even more intense
        exploration of the Chicago restaurant scene. Jeremy Allen Whitley will
        return as Carmen "Carmy" Berzetti, along with Ayo Edebayo as Sydney
        Adamson and Ebon Moss-Bach as Richard "Richie" Jerimovic.

        Season 3 ended with The Kitchen finally earning its first fine-dining
        star, a moment that executive producer Joanna Calloway described as "just
        the beginning of the pressure." The show has become known for its anthology
        episode format, with Season 2's "Fishes" episode winning multiple industry
        awards including Outstanding Directing for a Comedy Series.

        Guest stars confirmed for Season 4 include Jon Berenthal reprising his
        role as Carmy's late brother Michael in flashbacks, and new additions
        John Mulrooney and Olivia Coleford in undisclosed roles.

        The show's depiction of kitchen culture has drawn praise from professional
        chefs, with Thomas Kellerman and David Chan serving as consultants. Chef
        Matty Mathison, who plays Neil Fak, continues to choreograph the cooking
        sequences.

        The Kitchen has revitalized the half-hour comedy format, with rival
        streamers reportedly developing several kitchen-set series in response.
        Vantage's streaming numbers for the show remain the platform's highest for
        any original series.
        """,
    ),
    Document(
        title="Content 3: Golden Reel Nominations 2025",
        meta="Type: Awards Coverage",
        content="""
        The Academy of Screen Arts announced the 97th Golden Reel Awards
        nominations this morning, with Christopher Nolandt's Atomic Light leading
        the pack with 13 nominations.

        Cillian Murtagh earned his first Best Actor nomination for his portrayal
        of the title physicist. He faces competition from Bradley Cooperman for
        Maestro Nights, Paul Giamatto for The Holdout, and newcomer Colman
        Domingue for Rustling.

        Best Actress nominees include Emma Stonewall for Poor Creatures, Lily
        Glaston for Flower Moon Killers, and Margot Rabe for Dollhouse.

        Marta Scorsese's Flower Moon Killers received 10 nominations, while Greta
        Gerwald's Dollhouse earned 8, including Best Picture. The Ridgeline
        Pictures phenomenon's billion-dollar box office has translated into major
        awards recognition.

        Streaming studio Orchard Originals scored nominations for both Flower Moon
        Killers and Bonaparte, marking the streamer's strongest showing. Indie
        distributor A22 continues its dominant run with nominations across multiple
        categories for Past Tense and The Quiet Zone.

        The ceremony will be hosted by Jimmy Kimball and broadcast live from the
        Starlight Theatre in Los Angeles on March 10th.
        """,
    ),
)

USE_CASES = """
    1. Actor Filmography:
       - Track actor appearances across films
       - Identify frequent collaborations
       - Analyze career trajectories

    2. Director-Actor Networks:
       - Map director-actor relationships
       - Find recurring collaborations
       - Analyze creative partnerships

    3. Franchise Tracking:
       - Connect sequels and spin-offs
       - Track characters across films
       - Map franchise expansion

    4. Awards Analysis:
       - Track nominations and wins
       - Identify award-season patterns
       - Compare studio success rates

    5. Recommendation Engine:
       - Connect similar films by genre
       - Recommend based on cast/crew
       - Suggest based on viewing history
"""

SAMPLE = SampleSet(
    schema_name="entertainment",
    title="Movie and TV Analysis",
    blurb="Synthetic film review, TV preview and awards coverage.",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Actors & Performers", "PERSON", subtypes=("ACTOR",)),
        Highlight("Directors", "PERSON", subtypes=("DIRECTOR",), limit=8),
        Highlight("People (any role)", "PERSON", limit=12),
        Highlight("Films", "OBJECT", subtypes=("FILM",)),
        Highlight("TV Shows", "OBJECT", subtypes=("TV_SHOW",), limit=8),
        Highlight("Studios & Companies", "ORGANIZATION", limit=8),
        Highlight("Awards", "OBJECT", subtypes=("AWARD",), limit=5),
        Highlight("Characters", "OBJECT", subtypes=("CHARACTER",), limit=8),
    ),
    use_cases=USE_CASES,
    source_tag="entertainment_content",
    readback_query="science fiction film",
)
