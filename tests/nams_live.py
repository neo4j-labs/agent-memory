"""Helpers for tests that write to the **live** hosted NAMS service.

Why a dedicated throwaway workspace is mandatory
================================================
The SDK cannot undo what these tests create. ``clear_session`` deletes a
conversation but *not* the entities NAMS extracted from it, and
``NamsLongTermMemory`` declares a ``DELETE /entities/{id}`` endpoint spec
(``_SPEC_DELETE_ENTITY``) while exposing no ``delete_entity()`` method — so
there is no public route to remove an entity. Entities, and the resolver's
pending fuzzy-merge candidates, therefore accumulate **permanently** in
whatever workspace the credentials happen to point at. A live run is a one-way
write, and every repeat run grows a review backlog somebody has to clear by
hand in the NAMS UI.

So live-NAMS tests gate on ``NAMS_E2E_WORKSPACE_ID``: a workspace that exists
only to be polluted. It is deliberately a *different* variable from
``MEMORY_WORKSPACE_ID`` — the one a developer's ``.env`` already points at
their working workspace — so a working workspace can never become the target
by ambient default, and the gate refuses to run when the two are equal.

Verifying the gate locally
==========================
The ``opik`` pytest plugin loads the repo-root ``.env`` into ``os.environ``
whatever the shell says, so under a plain ``uv run pytest`` these variables are
present and the gate looks inert. Disable that plugin to see it fire::

    uv run pytest <path> -p no:opik

Both prior attempts at this gate lost time to that. In CI, where there is no
``.env``, the gate holds either way.

Entity names that cannot pile up in the resolver's review queue
===============================================================
``unique_codename`` exists because the first cut of the e2e fixture built its
entity name from one stem plus a hex run suffix (``Zorbium89980F486C``,
``Zorbium868C72705A``, ``ZorbiumEEEF8D7F7E``). Those are ~85% similar to each
other, so every run handed NAMS' resolver a fresh pending fuzzy-merge candidate
against every previous run's entity — the exact backlog no SDK call can clear.
See ``_CODENAMES`` for the replacement and its measured similarity bound.
"""

from __future__ import annotations

import os
import random

import pytest

#: The throwaway workspace these tests are allowed to write to.
E2E_WORKSPACE_ENV = "NAMS_E2E_WORKSPACE_ID"

#: Variables that name a workspace somebody actually works in; the throwaway
#: one must not equal any of them. ``NAMS_SANDBOX_WORKSPACE_ID`` is the
#: integration suite's alias for the same thing (``integration/nams/conftest.py``).
WORKING_WORKSPACE_ENVS = ("MEMORY_WORKSPACE_ID", "NAMS_SANDBOX_WORKSPACE_ID")

#: Stated in both skip reasons: the gate is not a style preference.
_WHY = (
    "these tests leave behind entities -- and the pending fuzzy-merge "
    "candidates NAMS' resolver queues for them -- that cannot be deleted "
    "through the SDK (clear_session removes the conversation only; "
    "NamsLongTermMemory exposes no delete_entity())"
)

#: Repeated in both reasons because it is the single thing that makes this gate
#: look broken when you try to verify it (see the module docstring).
_HOW_TO_VERIFY = "Verify this skip with `-p no:opik` -- the opik plugin loads .env into os.environ."


def throwaway_workspace_id() -> str | None:
    """The configured throwaway workspace id, or ``None`` when unset/blank."""
    return os.environ.get(E2E_WORKSPACE_ENV, "").strip() or None


def throwaway_workspace_skip_reason() -> str | None:
    """Why a live-NAMS test must not run here, or ``None`` when it may.

    Two refusals, never a silent proceed:

    * ``NAMS_E2E_WORKSPACE_ID`` unset — no throwaway workspace was nominated.
    * it equals a working-workspace variable — the nominated workspace is one
      somebody uses.
    """
    workspace = throwaway_workspace_id()
    if workspace is None:
        return (
            f"{E2E_WORKSPACE_ENV} is not set. Live-NAMS tests run only against a "
            f"dedicated throwaway workspace, because {_WHY}. It is a separate "
            f"variable from {WORKING_WORKSPACE_ENVS[0]} so a working workspace "
            f"cannot become the target by ambient default. {_HOW_TO_VERIFY}"
        )
    for name in WORKING_WORKSPACE_ENVS:
        if (os.environ.get(name, "").strip() or None) == workspace:
            return (
                f"{E2E_WORKSPACE_ENV} equals {name}: the throwaway workspace must "
                f"not be the working one, because {_WHY}. Point "
                f"{E2E_WORKSPACE_ENV} at a separate, disposable workspace. "
                f"{_HOW_TO_VERIFY}"
            )
    return None


def skip_without_throwaway_workspace() -> str:
    """Skip the calling module unless a throwaway workspace is configured.

    Call at **module scope, before every other gate**, so this safety check
    wins over a credential or service-reachability skip: otherwise a missing
    ``MEMORY_API_KEY`` reports first and hides whether the workspace gate is
    even wired up.

    Returns:
        The throwaway workspace id, to pass explicitly into ``NamsConfig`` /
        ``build_nams_settings`` rather than letting ``MemorySettings`` pick one
        up from the ambient environment.
    """
    reason = throwaway_workspace_skip_reason()
    if reason is not None:
        pytest.skip(reason, allow_module_level=True)
    workspace = throwaway_workspace_id()
    assert workspace is not None, "throwaway_workspace_skip_reason() passed with no workspace"
    return workspace


# ---------------------------------------------------------------------------
# Per-run entity codenames
# ---------------------------------------------------------------------------

#: Pronounceable nonsense words, one drawn per live run to name that run's
#: fixture entity. Two properties matter, and neither survives the
#: stem-plus-suffix scheme this replaces:
#:
#: * **Mutually distant.** Built by greedy rejection sampling that admitted a
#:   candidate only when it scored <= 55 against every word already in the pool
#:   under *all four* of ``rapidfuzz``'s ``ratio``, ``WRatio``,
#:   ``partial_ratio`` and ``token_sort_ratio``. Measured worst case over all
#:   8128 pairs: **54.5** (``Zuczuto`` / ``Fuzocal``), far below the 85% that
#:   made NAMS' resolver queue a merge candidate. No word is a prefix, suffix or
#:   substring of another, so substring-flavoured scorers cannot inflate a pair
#:   either -- which is why a codename is one word and not two concatenated
#:   (concatenating two pool words shares a whole half verbatim and measured
#:   81.5 under ``partial_ratio``).
#: * **Not real words.** Nothing here can collide with, or be mistaken for,
#:   genuine entities already in a workspace, so a recall assertion on a
#:   codename is an assertion about *this run's* memory.
#:
#: Two runs either draw different codenames (<= 54.5% similar, no candidate) or
#: the same one (1 in 128), which is an *exact* match: NAMS resolves it to the
#: one existing entity and queues nothing. Runs stay individually traceable
#: regardless, because the store name, user id and sink metadata still carry the
#: hex run id.
# fmt: off
_CODENAMES = (
    "Daplokid", "Dudekad", "Zikaken", "Ripolisi", "Kunmima", "Gepdimiz", "Zasovor", "Memfapol",
    "Feksenoc", "Puftisom", "Zokfeto", "Noravot", "Vabzebu", "Defkitek", "Fomubig", "Sicveru",
    "Sovegof", "Fupmodo", "Vavezin", "Tebobizo", "Kocgure", "Dafeduz", "Rumzabem", "Filapor",
    "Bibrigek", "Makmimiv", "Bagpavoc", "Gugrasa", "Mavzubef", "Nenazem", "Sarezolu", "Pakuceko",
    "Sikumesu", "Tavtalac", "Tafbekog", "Gabiduko", "Gagudili", "Sotbokun", "Pacabag", "Gecomef",
    "Mokobus", "Govirul", "Bevuzuc", "Tuczibaz", "Genutiga", "Lukzesek", "Numucipi", "Lusloris",
    "Gobofali", "Rozgivuk", "Gupeface", "Casapugu", "Celucup", "Tunlupom", "Dimgufaf", "Pekivif",
    "Bacukaco", "Nuvlesab", "Dulnule", "Rituraz", "Dismerac", "Ziscepe", "Bilodozu", "Zuczuto",
    "Karitavu", "Fozuteni", "Vurkici", "Soscoba", "Cifetem", "Volzafiz", "Gamakut", "Lafviro",
    "Pundofon", "Mitzidef", "Modbafol", "Tibinir", "Bedetes", "Zitibimo", "Nizufusu", "Batonaru",
    "Carfomev", "Kinapud", "Gozfugob", "Taksupaz", "Ledilas", "Nisrefos", "Piraseg", "Dibolog",
    "Nedzapit", "Muvalol", "Lugicol", "Murlagiv", "Zekgosuv", "Renonog", "Domfele", "Lureromo",
    "Refveta", "Puvopegu", "Cageleba", "Fuvumic", "Nipkecus", "Nakifak", "Todvesic", "Temosos",
    "Pezdadar", "Gocotud", "Cigidup", "Vunpadi", "Rovpare", "Mefgukug", "Kucrukuz", "Kegzode",
    "Cisivabo", "Bungukot", "Cogafav", "Rupetib", "Mupsilum", "Mogpogem", "Lektazug", "Fiblusik",
    "Zuvekilu", "Bicmanok", "Fuzocal", "Tiklelo", "Lufufec", "Subivane", "Valnalun", "Fasbaciv",
)
# fmt: on


def unique_codename() -> str:
    """One run's fixture entity name: a codename drawn from ``_CODENAMES``.

    Deliberately *not* a shared stem plus a per-run suffix. Suffixed names are
    ~85% similar to each other, which is precisely what makes NAMS' entity
    resolver file a pending fuzzy-merge candidate for every pair -- an
    ever-growing review backlog in a workspace the SDK cannot clean up.
    """
    return random.SystemRandom().choice(_CODENAMES)
