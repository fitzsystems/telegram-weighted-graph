"""
Telegram Channel Forward Graph Collector
=========================================
Crawls a seed Telegram channel, follows forwarded-message links up to
`max_depth` degrees of separation, and emits a weighted directed graph
where edge weight = number of posts that channel A forwarded from channel B
within the configured date window.

Requirements:
    pip install telethon python-dateutil

Usage:
    1. Create a Telegram app at https://my.telegram.org → API development tools
    2. Copy your api_id and api_hash into config.py (or set env vars)
    3. Run:  python collector.py

Output:
    graph_data.json  — nodes + edges ready to paste into the visualiser
    graph_data.csv   — flat edge list (source, target, weight) for Gephi / networkx
"""

import asyncio
import json
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.parser import parse as parse_date

from telethon import TelegramClient
from telethon.tl.types import (
    Channel, Chat, MessageFwdHeader, PeerChannel, PeerChat, PeerUser
)
from telethon.errors import (
    ChannelPrivateError, ChatAdminRequiredError, FloodWaitError,
    UsernameInvalidError, UsernameNotOccupiedError
)

from dotenv import load_dotenv
load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

API_ID   = int(os.getenv("TG_API_ID",   "0"))        # from my.telegram.org
API_HASH = os.getenv("TG_API_HASH",     "")          # from my.telegram.org
SESSION  = os.getenv("TG_SESSION",      "tg_session") # session file name (no ext)

SEED_CHANNEL = os.getenv("TG_SEED",     "@durov")    # starting channel

DATE_FROM = parse_date(os.getenv("TG_DATE_FROM", "2026-03-01")).replace(tzinfo=timezone.utc)
DATE_TO   = parse_date(os.getenv("TG_DATE_TO",   "2026-03-31")).replace(tzinfo=timezone.utc)

MAX_DEPTH  = int(os.getenv("TG_MAX_DEPTH",   "2"))   # max 2 per spec
MIN_WEIGHT = int(os.getenv("TG_MIN_WEIGHT",  "1"))   # minimum forwards to include edge
BATCH_SIZE = int(os.getenv("TG_BATCH_SIZE",  "200")) # messages per API call
RATE_DELAY = float(os.getenv("TG_RATE_DELAY","0.5")) # seconds between requests

OUTPUT_JSON = "graph_data.json"
OUTPUT_CSV  = "graph_data.csv"

# ── Helpers ────────────────────────────────────────────────────────────────────

def channel_key(entity) -> str:
    """Return a stable string key for a channel-like entity."""
    if hasattr(entity, "username") and entity.username:
        return f"@{entity.username.lower()}"
    return f"id:{entity.id}"


async def resolve_channel(client: TelegramClient, identifier: str):
    """Resolve a channel identifier to a Telethon entity, or None on failure."""
    try:
        entity = await client.get_entity(identifier)
        if isinstance(entity, (Channel, Chat)):
            return entity
    except (ChannelPrivateError, ChatAdminRequiredError):
        print(f"  [skip] {identifier} — private or no access")
    except (UsernameInvalidError, UsernameNotOccupiedError):
        print(f"  [skip] {identifier} — username not found")
    except Exception as e:
        print(f"  [skip] {identifier} — {e}")
    return None


async def fetch_forwards(
    client: TelegramClient,
    channel,
    date_from: datetime,
    date_to: datetime,
) -> dict[str, int]:
    """
    Iterate messages in `channel` within [date_from, date_to] and return a
    dict mapping source_channel_key → forward_count.

    We iterate backwards (newest first) using offset_date = date_to + 1 day,
    stopping once message.date < date_from.
    """
    forward_counts: dict[str, int] = defaultdict(int)
    channel_key_cache: dict[int, str] = {}

    print(f"  Fetching messages from {channel_key(channel)} …", end=" ", flush=True)
    n_messages = 0

    async for msg in client.iter_messages(
        channel,
        offset_date=date_to,
        reverse=False,          # newest first; we stop when we pass date_from
        limit=None,
    ):
        if msg.date is None:
            continue
        msg_date = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
        if msg_date < date_from:
            break                # messages are in reverse-chron order; we're done
        if msg_date > date_to:
            continue             # skip anything newer than our window (shouldn't happen)

        n_messages += 1

        fwd: MessageFwdHeader | None = msg.fwd_from
        if fwd is None:
            continue

        # Resolve the origin channel
        origin_id: int | None = None
        if isinstance(fwd.from_id, PeerChannel):
            origin_id = fwd.from_id.channel_id
        elif isinstance(fwd.from_id, PeerChat):
            origin_id = fwd.from_id.chat_id

        if origin_id is None:
            continue  # forwarded from a user or anonymous — skip

        if origin_id not in channel_key_cache:
            try:
                await asyncio.sleep(RATE_DELAY)
                origin_entity = await client.get_entity(PeerChannel(origin_id))
                channel_key_cache[origin_id] = channel_key(origin_entity)
            except Exception:
                channel_key_cache[origin_id] = f"id:{origin_id}"

        forward_counts[channel_key_cache[origin_id]] += 1

    print(f"{n_messages} messages scanned, {sum(forward_counts.values())} forwards found")
    return dict(forward_counts)


# ── Main crawler ───────────────────────────────────────────────────────────────

async def crawl(client: TelegramClient) -> tuple[list[dict], list[dict]]:
    """
    BFS from SEED_CHANNEL up to MAX_DEPTH degrees.

    Returns:
        nodes — list of {id, degree, title}
        edges — list of {source, target, weight}
    """
    nodes: dict[str, dict] = {}   # key → {id, degree, title}
    edges: dict[tuple, int] = defaultdict(int)  # (source, target) → total weight

    seed_entity = await resolve_channel(client, SEED_CHANNEL)
    if seed_entity is None:
        sys.exit(f"Could not resolve seed channel: {SEED_CHANNEL}")

    seed_key = channel_key(seed_entity)
    nodes[seed_key] = {"id": seed_key, "degree": 0, "title": getattr(seed_entity, "title", seed_key)}

    # BFS queue: (channel_entity, depth)
    queue: list[tuple] = [(seed_entity, 0)]
    visited: set[str] = {seed_key}

    while queue:
        current_entity, depth = queue.pop(0)
        current_key = channel_key(current_entity)

        if depth >= MAX_DEPTH:
            continue  # don't crawl beyond the depth limit

        try:
            await asyncio.sleep(RATE_DELAY)
            forward_counts = await fetch_forwards(client, current_entity, DATE_FROM, DATE_TO)
        except FloodWaitError as e:
            print(f"  [flood] Waiting {e.seconds}s …")
            await asyncio.sleep(e.seconds + 1)
            forward_counts = await fetch_forwards(client, current_entity, DATE_FROM, DATE_TO)
        except Exception as e:
            print(f"  [error] {current_key}: {e}")
            continue

        for origin_key, count in forward_counts.items():
            if count < MIN_WEIGHT:
                continue

            # edge: current_key forwarded FROM origin_key
            edges[(current_key, origin_key)] += count

            if origin_key not in visited:
                visited.add(origin_key)
                next_depth = depth + 1
                # Resolve the origin so we can crawl it next
                if next_depth < MAX_DEPTH:
                    try:
                        await asyncio.sleep(RATE_DELAY)
                        origin_entity = await client.get_entity(origin_key)
                        if isinstance(origin_entity, (Channel, Chat)):
                            queue.append((origin_entity, next_depth))
                            nodes[origin_key] = {
                                "id": origin_key,
                                "degree": next_depth,
                                "title": getattr(origin_entity, "title", origin_key),
                            }
                    except Exception:
                        nodes[origin_key] = {"id": origin_key, "degree": next_depth, "title": origin_key}
                else:
                    nodes[origin_key] = {"id": origin_key, "degree": next_depth, "title": origin_key}

    nodes_list = list(nodes.values())
    edges_list = [
        {"source": src, "target": tgt, "weight": w}
        for (src, tgt), w in edges.items()
        if w >= MIN_WEIGHT
    ]
    return nodes_list, edges_list


# ── Output ─────────────────────────────────────────────────────────────────────

def write_json(nodes: list[dict], edges: list[dict], path: str):
    payload = {
        "meta": {
            "seed": SEED_CHANNEL,
            "date_from": DATE_FROM.date().isoformat(),
            "date_to": DATE_TO.date().isoformat(),
            "max_depth": MAX_DEPTH,
            "min_weight": MIN_WEIGHT,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },
        "nodes": nodes,
        "edges": edges,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Wrote {len(nodes)} nodes and {len(edges)} edges → {path}")


def write_csv(edges: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source", "target", "weight"])
        w.writeheader()
        w.writerows(sorted(edges, key=lambda e: -e["weight"]))
    print(f"✓ Wrote edge list → {path}")


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    if API_ID == 0 or not API_HASH:
        sys.exit(
            "Set TG_API_ID and TG_API_HASH environment variables.\n"
            "Get them at https://my.telegram.org → API development tools."
        )

    print(f"Telegram Channel Forward Graph Collector")
    print(f"  Seed:       {SEED_CHANNEL}")
    print(f"  Date range: {DATE_FROM.date()} → {DATE_TO.date()}")
    print(f"  Max depth:  {MAX_DEPTH}")
    print(f"  Min weight: {MIN_WEIGHT}")
    print()

    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        nodes, edges = await crawl(client)

    write_json(nodes, edges, OUTPUT_JSON)
    write_csv(edges, OUTPUT_CSV)

    print(f"\nSummary")
    print(f"  Channels discovered : {len(nodes)}")
    print(f"  Forward edges       : {len(edges)}")
    print(f"  Total forward volume: {sum(e['weight'] for e in edges)}")
    print(f"\nPaste {OUTPUT_JSON} into the visualiser's 'Load data' button (add that to the UI next if needed).")


if __name__ == "__main__":
    asyncio.run(main())
