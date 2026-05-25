# Telegram Channel Forward Graph — Data Collector

Crawls a seed Telegram channel and maps which channels it (and its neighbours) forward posts from, building a weighted directed graph within a configurable date window.

---

## Quick start

### 1. Get Telegram API credentials

1. Go to <https://my.telegram.org> and log in with your phone number.
2. Click **API development tools**.
3. Create an app (name/description don't matter). Copy your `api_id` and `api_hash`.

### 2. Install dependencies

```bash
pip install telethon python-dateutil
```

### 3. Set environment variables

```bash
export TG_API_ID="12345678"
export TG_API_HASH="abcdef1234567890abcdef1234567890"
export TG_SEED="@durov"
export TG_DATE_FROM="2026-03-01"
export TG_DATE_TO="2026-03-31"
export TG_MAX_DEPTH="2"
export TG_MIN_WEIGHT="2"   # ignore edges with fewer than N forwards
```

Or create a `.env` file and load it:

```bash
pip install python-dotenv
# then at top of collector.py add:
# from dotenv import load_dotenv; load_dotenv()
```

### 4. Run

```bash
python collector.py
```

On first run Telethon will ask for your phone number and a login code sent by Telegram. The session is saved to `tg_session.session` — subsequent runs re-use it silently.

---

## Output files

| File | Contents |
|------|----------|
| `graph_data.json` | Nodes + edges in the visualiser format (paste directly in) |
| `graph_data.csv`  | Flat edge list for Gephi, networkx, or Excel |

### graph_data.json structure

```json
{
  "meta": { "seed": "@durov", "date_from": "2026-03-01", ... },
  "nodes": [
    { "id": "@durov", "degree": 0, "title": "Pavel Durov" },
    { "id": "@telegram", "degree": 1, "title": "Telegram" }
  ],
  "edges": [
    { "source": "@durov", "target": "@telegram", "weight": 14 }
  ]
}
```

Edge direction: `source` forwarded posts that originated in `target`. Weight = number of such forwards within the date window.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TG_API_ID` | — | **Required.** From my.telegram.org |
| `TG_API_HASH` | — | **Required.** From my.telegram.org |
| `TG_SESSION` | `tg_session` | Session file name (no extension) |
| `TG_SEED` | `@durov` | Seed channel username or `id:XXXXXXXXX` |
| `TG_DATE_FROM` | `2026-03-01` | Start of window (inclusive) |
| `TG_DATE_TO` | `2026-03-31` | End of window (inclusive) |
| `TG_MAX_DEPTH` | `2` | Max degrees from seed (spec: ≤ 2) |
| `TG_MIN_WEIGHT` | `1` | Minimum forwards to include an edge |
| `TG_BATCH_SIZE` | `200` | Messages per API request |
| `TG_RATE_DELAY` | `0.5` | Seconds between API calls (avoid flood limits) |

---

## How it works

1. **Resolve seed** — look up the seed channel entity via the Telegram API.
2. **BFS crawl** — for each channel in the queue (up to `MAX_DEPTH`):
   - Iterate all messages in the date window.
   - For each message with a `fwd_from` header pointing at a *channel* (not a user), increment `edges[(current_channel, origin_channel)]`.
3. **Enqueue discovered channels** — any origin channel seen for the first time at depth < MAX_DEPTH is added to the BFS queue.
4. **Write outputs** — JSON and CSV.

Private channels, deleted channels, and channels that have blocked the Telegram API are skipped gracefully.

---

## Rate limits & large channels

Telegram's API allows roughly 100 requests/min for account-based clients. For seed channels with tens of thousands of posts:

- Set `TG_RATE_DELAY` to `1.0` or higher.
- On `FloodWaitError` the script waits automatically and retries.
- For very large windows (months of data on a high-volume channel) expect a runtime of several minutes.

---

## Loading real data into the visualiser

The graph visualiser widget uses `generateMockData()` by default. To swap in real data:

1. Run the collector → `graph_data.json`.
2. In the visualiser's `buildGraph()` function, replace the `generateMockData(...)` call with a `fetch('graph_data.json')` call and parse the `nodes` / `edges` arrays from it.

Or serve the JSON locally:

```bash
python -m http.server 8000
```

Then in the widget JS:

```js
const { nodes, edges } = await fetch('http://localhost:8000/graph_data.json')
  .then(r => r.json())
  .then(d => ({ nodes: d.nodes, edges: d.edges }));
```

---

## Legal & ethical notes

- Only crawl **public** channels. Private channels require admin access and imply consent.
- Respect Telegram's [Terms of Service](https://telegram.org/tos) and API usage guidelines.
- Store credentials (api_id, api_hash) securely — never commit them to version control.
- The session file (`*.session`) contains your auth token — treat it like a password.
