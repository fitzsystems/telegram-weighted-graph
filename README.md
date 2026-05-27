# Telegram Channel Forward Graph

Maps how Telegram channels forward each other's posts, producing a weighted directed graph where edge weight = number of forwarded posts from channel A to channel B within a configurable date window.


<img width="1878" height="933" alt="Screenshot from 2026-05-26 21-21-37" src="https://github.com/user-attachments/assets/25027e90-3148-4669-ac0b-049f8b80ea06" />

---

## Files

| File | Purpose |
|------|---------|
| `collector.py` | Telethon crawler + Flask web server — the only process you run |
| `visualiser.html` | Browser UI — served automatically by `collector.py` |
| `graph_data.json` | Output: graph payload (nodes + edges + metadata) |
| `graph_data.csv` | Output: flat edge list for Gephi, networkx, or Excel |

---

## Quick start

### 1. Get Telegram API credentials

1. Go to <https://my.telegram.org> and log in with your phone number.
2. Click **API development tools** and create an app (name/description don't matter).
3. Copy your `api_id` and `api_hash`.

### 2. Install dependencies

```bash
pip install telethon flask python-dateutil
```

### 3. Set credentials as environment variables

These are the **only** values that go on the command line — they are secrets and should never be stored in a file that could be shared or committed.

```bash
export TG_API_ID="12345678"
export TG_API_HASH="abcdef1234567890abcdef1234567890"
```

### 4. Start the server

```bash
python collector.py
```

On first run Telethon will ask for your phone number and send a login code via Telegram. The session is saved to `tg_session.session` — subsequent runs re-use it silently.

### 5. Open the browser

```
http://localhost:5000
```

Everything else — seed channel, date range, depth, min forwards — is entered in the browser UI. No further command-line interaction is needed.

---

## Using the visualiser

### Running a real crawl

1. Fill in the four fields in the sidebar: seed channel, date range, max depth, min forwards.
2. Click **▶ Run collector**.
3. A live log panel opens and streams progress line by line as the crawl runs.
4. When the crawl finishes the graph renders automatically.

The **server status indicator** at the top of the sidebar shows:

- 🟢 Green — server online, credentials loaded, ready to crawl
- 🟡 Amber — server online but `TG_API_ID` / `TG_API_HASH` not set
- ⚫ Grey — `collector.py` is not running

### Demo mode

Click **▷ Demo graph** to render a deterministic synthetic graph using the current form values. No server or Telegram account required.

### Loading a previous result

Click **↑ Load graph_data.json** to reload a saved crawl result from disk. The form fields auto-populate from the file's metadata.

### Graph controls

| Action | How |
|--------|-----|
| Zoom | Scroll wheel |
| Pan | Click and drag on the canvas |
| Move a node | Drag the node |
| Focus a node | Click the node or its row in the sidebar list |
| Clear focus | Click the canvas background or press `Esc` |
| Search channels | Type in the search box above the sidebar list |
| Anonymise labels | Toggle **Anonymise nodes** to replace channel names with `node1`, `node2`, etc. Hover any node to reveal its real name regardless of toggle state. |

### Node colours

| Colour | Meaning |
|--------|---------|
| Green | Seed channel (degree 0) |
| Blue | Degree 1 — directly connected to seed |
| Purple | Degree 2 |
| Orange | Degree 3 |

Edge thickness is proportional to forward count.

---

## Configuration reference

All crawl settings (seed, dates, depth, min-weight) are set through the browser UI. The following environment variables control low-level behaviour:

| Variable | Default | Description |
|----------|---------|-------------|
| `TG_API_ID` | — | **Required.** From my.telegram.org |
| `TG_API_HASH` | — | **Required.** From my.telegram.org |
| `TG_SESSION` | `tg_session` | Session file name (no `.session` extension) |
| `TG_RATE_DELAY` | `0.5` | Seconds between API calls — increase if hitting flood limits |
| `PORT` | `5000` | Port the Flask server listens on |

---

## How the crawl works

1. **Resolve seed** — look up the seed channel by username or numeric ID.
2. **BFS crawl** — for each channel in the queue at depth < `max_depth`:
   - Iterate all messages within the date window (newest-first, stopping at `date_from`).
   - For each message with a `fwd_from` header pointing at another channel, increment `edge[(current_channel, origin_channel)]`.
   - Any newly discovered origin channel at depth < `max_depth` is added to the BFS queue.
3. **Filter** — edges with weight below `min_weight` are discarded.
4. **Save** — results written to `graph_data.json` and `graph_data.csv`, then loaded into the visualiser automatically.

Forwards from users (not channels) are ignored. Private channels, deleted channels, and channels that reject API access are skipped gracefully.

---

## Rate limits

Telegram enforces per-account request quotas. If you hit a `FloodWaitError`:

- During a crawl — the collector waits automatically and resumes.
- At seed resolution — you'll see a clear message in the log panel with the exact wait time (e.g. "must wait 5h 47m"). The limit is per-account; it lifts automatically. You can use a different account in the meantime by setting a different `TG_SESSION` path.

To reduce the likelihood of hitting limits, increase `TG_RATE_DELAY` (e.g. `export TG_RATE_DELAY=1.5`).

---

## Output format

### graph_data.json

```json
{
  "meta": {
    "seed": "@channelname",
    "date_from": "2026-03-01",
    "date_to": "2026-03-31",
    "max_depth": 2,
    "min_weight": 1,
    "generated_at": "2026-03-31T12:00:00Z"
  },
  "nodes": [
    { "id": "@channelname", "degree": 0, "title": "Channel Display Name" },
    { "id": "@other",       "degree": 1, "title": "Other Channel" }
  ],
  "edges": [
    { "source": "@channelname", "target": "@other", "weight": 14 }
  ]
}
```

Edge direction: `source` forwarded posts that originated in `target`. Weight = number of such forwards within the date window.

### graph_data.csv

Flat edge list sorted by weight descending — suitable for import into Gephi, networkx, or Excel.

```
source,target,weight
@channelname,@other,14
```

---

## Legal & ethical notes

- Only crawl **public** channels. Private channels require admin access and explicit consent.
- Respect Telegram's [Terms of Service](https://telegram.org/tos) and API usage guidelines.
- Store `TG_API_ID` and `TG_API_HASH` securely — never commit them to version control.
- The session file (`tg_session.session`) contains your auth token — treat it like a password.# Telegram Channel Forward Graph — Data Collector
