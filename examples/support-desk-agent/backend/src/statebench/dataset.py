"""The STATE-Bench customer-support tasks the demo seeds from.

``data/state-bench/`` holds 24 of the 100 training trajectories in
https://github.com/microsoft/STATE-Bench (MIT, ``data/state-bench/LICENSE``),
one to three per task family, copied unchanged with their task definitions and
starting environments:

- ``trajectories/<id>.json``: the conversation, with each assistant turn's tool
  calls (name, arguments and the environment's result);
- ``tasks/<id>.json``: the customer, the task's "today" (``now``), its type and
  a summary;
- ``task_envs/<id>.json``: the customers, orders, order items, products and
  warranties the task starts from.

Orders, items and warranties are unique to one task and the five customers are
identical in every task, so the 24 environments merge into one world. Each
order remembers its task's ``now``, because the return windows and fees the
policies compute depend on it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from src.config import DATA_DIR

STATE_BENCH_DIR = DATA_DIR / "state-bench"
SOURCE_URL = "https://github.com/microsoft/STATE-Bench"
SOURCE_COMMIT = "5644b1838d96bc4483da29642d058ecaa6f80f7f"

#: Record kinds, in the order the seed loads them (owners before what they own).
RECORD_KINDS = ("customers", "products", "orders", "order_items", "warranties")

#: The id field of each record kind.
ID_FIELDS = {
    "customers": "customer_id",
    "products": "product_id",
    "orders": "order_id",
    "order_items": "item_id",
    "warranties": "warranty_id",
}

#: The marker a trajectory's simulated user sends when the task is finished.
TASK_DONE = "[TASK_DONE]"

#: Difficulty words at the start of a task slug, left out of thread titles.
_DIFFICULTY = {"hard", "spare", "challenge", "edge"}


@dataclass(frozen=True)
class Task:
    """One STATE-Bench task: its conversation, its starting state and its date."""

    id: str
    customer_id: str
    now: str
    task_type: str
    summary: str
    conversation: list[dict[str, Any]]
    env: dict[str, list[dict[str, Any]]]

    @property
    def number(self) -> int:
        return int(self.id.split("-", 1)[0])

    @property
    def session_id(self) -> str:
        """``seed-<id>``, the thread the conversation is stored in."""
        return "seed-" + self.id.replace("_", "-")

    @property
    def topic(self) -> str:
        """The slug as words: ``hard_exchange_oos_store_credit`` -> "Exchange oos store credit"."""
        words = self.id.split("-", 1)[1].split("_")
        if words and words[0] in _DIFFICULTY:
            words = words[1:]
        text = " ".join(words)
        return text[:1].upper() + text[1:]


@dataclass
class World:
    """The merged starting state of every task."""

    records: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {kind: {} for kind in RECORD_KINDS}
    )
    #: order_id -> the ``now`` of the task the order belongs to.
    as_of: dict[str, str] = field(default_factory=dict)
    #: record id -> the task that defined it (orders, items, warranties).
    source_task: dict[str, str] = field(default_factory=dict)


def _summary(raw: Any) -> str:
    """The task summary's first sentence, without its Markdown lead-in."""
    text = re.sub(r"\*\*[^*]+:\*\*\s*", "", str(raw or "")).strip()
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return sentence


@lru_cache
def load_tasks() -> tuple[Task, ...]:
    """Every task under ``data/state-bench/``, in task-number order."""
    tasks: list[Task] = []
    for path in sorted((STATE_BENCH_DIR / "trajectories").glob("*.json")):
        task_id = path.stem
        meta = json.loads((STATE_BENCH_DIR / "tasks" / f"{task_id}.json").read_text("utf-8"))
        env = json.loads((STATE_BENCH_DIR / "task_envs" / f"{task_id}.json").read_text("utf-8"))
        conversation = json.loads(path.read_text("utf-8"))["conversation"]
        tasks.append(
            Task(
                id=task_id,
                customer_id=str(meta["user_id"]),
                now=str(meta["now"]),
                task_type=str(meta.get("task_type") or ""),
                summary=_summary(meta.get("task_summary")),
                conversation=conversation,
                env=env,
            )
        )
    return tuple(sorted(tasks, key=lambda task: task.number))


def merge_world(tasks: tuple[Task, ...] | list[Task]) -> World:
    """One world from every task's starting environment.

    Customers are identical across tasks and products are keyed by id, so the
    first copy of each is kept; orders, items and warranties belong to one task.
    """
    world = World()
    for task in tasks:
        for kind in RECORD_KINDS:
            id_field = ID_FIELDS[kind]
            for record in task.env.get(kind, []):
                record_id = str(record[id_field])
                world.records[kind].setdefault(record_id, record)
                if kind in ("orders", "order_items", "warranties"):
                    world.source_task.setdefault(record_id, task.id)
        for order in task.env.get("orders", []):
            world.as_of.setdefault(str(order["order_id"]), task.now)
    return world


def turns(task: Task) -> list[dict[str, Any]]:
    """The user and assistant messages of a task, without the system prompt or the end marker."""
    return [
        message
        for message in task.conversation
        if message.get("role") in ("user", "assistant")
        and isinstance(message.get("content"), str)
        and message["content"].strip()
        and message["content"].strip() != TASK_DONE
    ]
