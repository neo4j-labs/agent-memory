# Transcript data

This directory holds the podcast transcripts the loader ingests. **The real
corpus is not in the repository.** Episode transcripts are the podcast's
copyrighted material and we do not have redistribution rights, so `data/` ships
empty and `data/samples/` ships three short synthetic transcripts instead.

## What ships here

| Path | Contents | Tracked in git |
|------|----------|----------------|
| `data/samples/*.txt` | 3 synthetic transcripts (fictional guests, ~25 turns each) | yes |
| `data/*.txt` | Your own transcripts — the 299-episode corpus, or anything in the format below | no (by convention: do not commit transcripts you cannot redistribute) |

`scripts/load_transcripts.py` resolves the data directory in this order:

1. `--data-dir <path>` if you pass it
2. `data/` if it contains any `.txt` files
3. `data/samples/` otherwise

So `make load-sample` works on a clean clone, against the synthetic fixtures,
with no data acquisition step. Drop real transcripts into `data/` and the same
commands pick them up instead.

## File format

One file per episode, named `<Guest Name>.txt`. The filename becomes the session
id: `lenny-podcast-<slugified-guest-name>` (diacritics folded to ASCII, so
`Tobi Lütke.txt` → `lenny-podcast-tobi-lutke`).

Inside the file, each speaker turn starts with a marker line:

```
Speaker Name (HH:MM:SS):
Body text, which may span
several lines.

(HH:MM:SS):
A continuation turn — no speaker name means "same speaker as above".

Other Speaker (HH:MM:SS):
...
```

Rules the parser enforces:

- The marker line must end with `(HH:MM:SS):` and nothing else.
- Speaker names may contain Unicode letters, spaces, `.`, `-` and `'`.
- Blank lines are ignored; everything else is body text for the current turn.
- A turn with no body text is dropped.

`parse_transcript()` in `scripts/load_transcripts.py` is the reference
implementation, and `tests/examples/test_lennys_memory_example.py` pins its
behaviour (including the continuation-turn and Unicode cases).

## Where to get the real transcripts

We deliberately do not ship a scraper. If you want the full corpus:

1. Obtain transcripts you have the right to use — for example, export your own
   podcast's transcripts, or use a transcription service on episodes you are
   licensed to process.
2. Save one `.txt` per episode into this directory, named after the guest, in
   the format above.
3. Run the pipeline:

   ```bash
   make load-full                    # ingest + entity extraction
   make backfill-relationships       # GLiREL relationships (no LLM)
   make enrich-entities              # Wikipedia/Diffbot enrichment
   make geocode-locations            # coordinates for LOCATION entities
   ```

## About the synthetic samples

The three sample files are entirely fabricated: the guests, the companies and
the quotes do not exist and are not attributed to any real person. They exist so
the pipeline, the tests and the agent tools can be exercised end to end. They
intentionally cover the parser's edge cases:

- `Rowan Keeler.txt` — continuation turns (a `(HH:MM:SS):` line with no speaker)
- `Renée Delacroix.txt` — a speaker name with a diacritic
- `Mei Takahashi.txt` — multiple named speakers, short turns

They also mention organizations and cities, so entity extraction, enrichment and
geocoding all have something to find.
