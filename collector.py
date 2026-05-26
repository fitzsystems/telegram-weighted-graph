"""
Telegram Channel Forward Graph — Collector + Web Server
========================================================
Run once:
    pip install telethon python-dateutil flask

Then start the server (the only command you ever need):
    python collector.py

Open your browser at http://localhost:5000 — the visualiser loads automatically.
Fill in the form, click "Run collector", watch live progress, and the graph
renders in-page when the crawl finishes.

The only things that remain as environment variables are your Telegram API
credentials (api_id and api_hash), which should never be stored in a file
that might be shared or committed:

    export TG_API_ID="12345678"
    export TG_API_HASH="abcdef1234567890abcdef1234567890"

All other settings (seed channel, date range, depth, min-weight) are entered
through the browser UI.
"""

import asyncio
import json
import csv
import os
import sys
import threading
import queue
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from dateutil.parser import parse as parse_date

from dotenv import load_dotenv
load_dotenv("collector.env")

# ── Flask ──────────────────────────────────────────────────────────────────────
try:
    from flask import Flask, request, Response, send_file, jsonify
except ImportError:
    sys.exit("Flask is not installed. Run:  pip install flask")

# ── Telethon ───────────────────────────────────────────────────────────────────
try:
    from telethon import TelegramClient
    from telethon.tl.types import Channel, Chat, PeerChannel, PeerChat
    from telethon.errors import (
        ChannelPrivateError, ChatAdminRequiredError, FloodWaitError,
        UsernameInvalidError, UsernameNotOccupiedError,
    )
except ImportError:
    sys.exit("Telethon is not installed. Run:  pip install telethon")

# ── Static credentials (env vars only) ────────────────────────────────────────
API_ID   = int(os.getenv("TG_API_ID",  "0"))
API_HASH = os.getenv("TG_API_HASH",    "")
SESSION  = os.getenv("TG_SESSION",     "tg_session")
RATE_DELAY = float(os.getenv("TG_RATE_DELAY", "0.5"))

OUTPUT_JSON = "graph_data.json"
OUTPUT_CSV  = "graph_data.csv"
PORT        = int(os.getenv("PORT", "5000"))

# ── Global crawl state ─────────────────────────────────────────────────────────
_crawl_lock   = threading.Lock()
_crawl_active = False          # only one crawl at a time
_log_queue: queue.Queue = queue.Queue()   # log lines streamed to browser
_result: dict | None = None              # last successful graph payload

app = Flask(__name__, static_folder=None)

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    _log_queue.put(line)


def channel_key(entity) -> str:
    if hasattr(entity, "username") and entity.username:
        return f"@{entity.username.lower()}"
    return f"id:{entity.id}"


# ── Crawl logic ───────────────────────────────────────────────────────────────

async def fetch_forwards(client, channel, date_from, date_to, rate_delay):
    forward_counts: dict[str, int] = defaultdict(int)
    cache: dict[int, str] = {}
    n = 0

    log(f"  scanning {channel_key(channel)} …")

    async for msg in client.iter_messages(channel, offset_date=date_to, reverse=False, limit=None):
        if msg.date is None:
            continue
        md = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
        if md < date_from:
            break
        if md > date_to:
            continue
        n += 1
        fwd = msg.fwd_from
        if fwd is None:
            continue
        origin_id = None
        if hasattr(fwd, "from_id"):
            fi = fwd.from_id
            if isinstance(fi, PeerChannel):
                origin_id = fi.channel_id
            elif isinstance(fi, PeerChat):
                origin_id = fi.chat_id
        if origin_id is None:
            continue
        if origin_id not in cache:
            try:
                await asyncio.sleep(rate_delay)
                oe = await client.get_entity(PeerChannel(origin_id))
                cache[origin_id] = channel_key(oe)
            except Exception:
                cache[origin_id] = f"id:{origin_id}"
        forward_counts[cache[origin_id]] += 1

    log(f"    → {n} messages scanned, {sum(forward_counts.values())} forwards")
    return dict(forward_counts)


async def crawl(seed, date_from, date_to, max_depth, min_weight):
    nodes: dict[str, dict] = {}
    edge_map: dict[tuple, int] = defaultdict(int)

    log(f"Connecting to Telegram …")
    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        log(f"Resolving seed channel: {seed}")
        try:
            seed_entity = await client.get_entity(seed)
        except FloodWaitError as e:
            mins = e.seconds // 60
            hrs  = mins // 60
            human = f"{hrs}h {mins % 60}m" if hrs else f"{mins}m {e.seconds % 60}s"
            raise RuntimeError(
                f"Telegram rate limit — must wait {human} ({e.seconds}s) before "
                f"new username lookups. This is a per-account limit imposed by Telegram; "
                f"it will lift automatically. Try again after the wait, or use a different account."
            )
        except Exception as e:
            raise RuntimeError(f"Cannot resolve seed '{seed}': {e}")

        if not isinstance(seed_entity, (Channel, Chat)):
            raise RuntimeError(f"'{seed}' is not a channel or group.")

        sk = channel_key(seed_entity)
        nodes[sk] = {"id": sk, "degree": 0, "title": getattr(seed_entity, "title", sk)}
        log(f"Seed resolved: {sk} -- '{nodes[sk]['title']}'")

        queue_bfs = [(seed_entity, 0)]
        visited = {sk}

        while queue_bfs:
            current_entity, depth = queue_bfs.pop(0)
            ck = channel_key(current_entity)

            if depth >= max_depth:
                continue

            log(f"Crawling depth {depth}: {ck}")
            try:
                await asyncio.sleep(RATE_DELAY)
                fwd_counts = await fetch_forwards(client, current_entity, date_from, date_to, RATE_DELAY)
            except FloodWaitError as e:
                log(f"  Flood wait: sleeping {e.seconds}s …")
                await asyncio.sleep(e.seconds + 1)
                fwd_counts = await fetch_forwards(client, current_entity, date_from, date_to, RATE_DELAY)
            except (ChannelPrivateError, ChatAdminRequiredError):
                log(f"  Skipping {ck} — private/no access")
                continue
            except Exception as e:
                log(f"  Error on {ck}: {e}")
                continue

            for origin_key, count in fwd_counts.items():
                if count < min_weight:
                    continue
                edge_map[(ck, origin_key)] += count
                if origin_key not in visited:
                    visited.add(origin_key)
                    nd = depth + 1
                    if nd < max_depth:
                        try:
                            await asyncio.sleep(RATE_DELAY)
                            oe = await client.get_entity(origin_key)
                            if isinstance(oe, (Channel, Chat)):
                                queue_bfs.append((oe, nd))
                                nodes[origin_key] = {
                                    "id": origin_key, "degree": nd,
                                    "title": getattr(oe, "title", origin_key),
                                }
                        except Exception:
                            nodes[origin_key] = {"id": origin_key, "degree": nd, "title": origin_key}
                    else:
                        nodes[origin_key] = {"id": origin_key, "degree": nd, "title": origin_key}

    nodes_list = list(nodes.values())
    edges_list = [
        {"source": s, "target": t, "weight": w}
        for (s, t), w in edge_map.items()
        if w >= min_weight
    ]
    return nodes_list, edges_list


def _crawl_thread(params: dict):
    global _crawl_active, _result

    seed       = params["seed"]
    date_from  = parse_date(params["date_from"]).replace(tzinfo=timezone.utc)
    date_to    = parse_date(params["date_to"]).replace(tzinfo=timezone.utc)
    max_depth  = int(params.get("max_depth",  2))
    min_weight = int(params.get("min_weight", 1))

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        nodes, edges = loop.run_until_complete(
            crawl(seed, date_from, date_to, max_depth, min_weight)
        )
        loop.close()

        total_w = sum(e["weight"] for e in edges)
        log(f"Done. {len(nodes)} channels, {len(edges)} edges, {total_w} total forwards.")

        payload = {
            "meta": {
                "seed": seed,
                "date_from": date_from.date().isoformat(),
                "date_to":   date_to.date().isoformat(),
                "max_depth": max_depth,
                "min_weight": min_weight,
                "generated_at": datetime.utcnow().isoformat() + "Z",
            },
            "nodes": nodes,
            "edges": edges,
        }

        # Save to disk as well
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["source", "target", "weight"])
            w.writeheader()
            w.writerows(sorted(edges, key=lambda e: -e["weight"]))
        log(f"Saved → {OUTPUT_JSON}  and  {OUTPUT_CSV}")

        _result = payload
        _log_queue.put("__DONE__")

    except RuntimeError as e:
        # RuntimeError = user-facing message (rate limit, bad channel, etc.)
        log(f"ERROR: {e}")
        _log_queue.put("__ERROR__")
    except Exception:
        log(f"ERROR: {traceback.format_exc()}")
        _log_queue.put("__ERROR__")
    finally:
        _crawl_active = False


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the visualiser HTML from the same directory as this script."""
    html_path = Path(__file__).parent / "visualiser.html"
    if not html_path.exists():
        return "visualiser.html not found — make sure it's in the same folder as collector.py", 404
    return send_file(str(html_path))


@app.route("/run", methods=["POST"])
def run_collector():
    global _crawl_active
    if not API_ID or not API_HASH:
        return jsonify({"error": "TG_API_ID and TG_API_HASH environment variables are not set."}), 400
    with _crawl_lock:
        if _crawl_active:
            return jsonify({"error": "A crawl is already running."}), 409
        _crawl_active = True
    # Drain old log lines
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except Exception:
            break
    params = request.get_json(force=True)
    t = threading.Thread(target=_crawl_thread, args=(params,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/status")
def status_stream():
    """Server-Sent Events stream of log lines from the running crawl."""
    def generate():
        while True:
            try:
                line = _log_queue.get(timeout=30)
                if line in ("__DONE__", "__ERROR__"):
                    yield f"data: {line}\n\n"
                    break
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield "data: [ping]\n\n"  # keep-alive
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/result")
def get_result():
    if _result is None:
        return jsonify({"error": "No result available yet."}), 404
    return jsonify(_result)


@app.route("/check")
def check():
    """Quick health-check used by the UI to detect whether the server is live."""
    creds_ok = bool(API_ID and API_HASH)
    return jsonify({
        "server": "ok",
        "creds": creds_ok,
        "active": _crawl_active,
    })


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 58)
    print("  TG Forward Graph — Collector Server")
    print("=" * 58)
    if not API_ID or not API_HASH:
        print("\n  ⚠  WARNING: TG_API_ID / TG_API_HASH not set.")
        print("     Set them before running a real crawl:")
        print("       export TG_API_ID=12345678")
        print("       export TG_API_HASH=abcdef...")
        print("     Demo mode in the browser will still work.\n")
    else:
        print(f"\n  ✓  Credentials loaded (API_ID={API_ID})\n")
    print(f"  Open → http://localhost:{PORT}")
    print("=" * 58 + "\n")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
