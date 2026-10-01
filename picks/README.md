# Pick Files

Place your league's pick JSON files here. `picks/*.json` and `picks/*.xlsx` are gitignored, because each league has its own.

## Usage

```bash
python seed.py --picks-dir ./picks
```

## File naming and discovery

`seed.py` finds pick files by name (`discover_pick_files()`). There is no filename mapping to maintain in code.

- Every file directly inside the directory that matches `season*.json` is considered. Subdirectories are not searched.
- The season number is the run of digits right after `season`: `season51.json` belongs to season 51 and `season47_snakedraft.json` to season 47. A file with no digits there is ignored.
- One file is loaded per season. If several files match the same season, the canonical `season{N}.json` wins over suffixed variants such as `season47_snakedraft.json`. If only suffixed variants exist, the first one in alphabetical order is used.
- `season{N}.json` is the name the app writes when it exports picks from the database, so an exported file takes precedence over a hand-named one.

## Which seasons get built

`seed.py` drops every table and rebuilds, so a pick file is only loaded if its season is built in the same run.

- Without `--seasons`, the seasons built are the `DEFAULT_SEASONS` list in `seed.py` plus every season that has a pick file in `--picks-dir`.
- With `--seasons 46,47`, exactly those seasons are built. Pick files do not add to the list.
- A pick file whose season was not built is skipped, and `seed.py` prints a `WARNING` naming the file and the season. This happens when `--seasons` leaves the season out, or when the survivoR dataset has no data for it.

## Format

```json
{
  "scoring": "default",
  "picks": {
    "PlayerA": [
      {"survivor": "Name", "type": "d", "order": 1},
      {"survivor": "Name", "type": "w"}
    ],
    "PlayerB": [
      {"survivor": "Name", "type": "d", "order": 2},
      {"survivor": "Name", "type": "pmr_d"}
    ]
  },
  "sole_survivor_picks": {
    "PlayerA": [
      {"survivor": "Name", "episode": 1},
      {"survivor": "Name", "episode": 5}
    ]
  }
}
```

### Top-level keys

| Key | Required | Meaning |
|---|---|---|
| `scoring` | no | `"default"`, `"legacy"` or `"custom"`. Sets the season's scoring config. A missing or unrecognized value means `"default"`. |
| `scoring_config` | with `"custom"` | Object of scoring values, stored as the season's config. Keys you leave out fall back to `DEFAULT_CONFIG` in `app/scoring/classic.py`. If `scoring` is `"custom"` and this key is missing, the default config is used. |
| `picks` | yes | Object keyed by fantasy player name. Each value is a list of pick entries. |
| `sole_survivor_picks` | no | Object keyed by fantasy player name. Each value is a list of Sole Survivor entries. |

A file with no `picks` key is read as a bare player map (the older format). That form cannot carry the other top-level keys.

Player names are matched case-insensitively, so `PlayerA` in two season files is the same player. A player who does not exist yet is created, and the name as written becomes the display name.

### Pick entries

| Key | Required | Meaning |
|---|---|---|
| `survivor` | yes | Castaway name, matched case-insensitively against the season's castaways. |
| `type` | yes | Pick type code: `d` (draft), `w` (wildcard), `pmr_w` (wildcard replacement) or `pmr_d` (draft replacement). |
| `order` | no | Draft position, stored as the pick's order. The export writes it for draft picks. |

Castaway names are the survivoR `castaway` short names. A returning player who appears under more than one name is stored under the most common one. Lookup tries an exact match first (after the `NICKNAME_MAP` shorthand table in `seed.py`), then a prefix match, which takes the first castaway whose name starts with the text given. If nothing matches, `seed.py` prints a `WARNING` and skips that entry.

Use the bare type codes. Matching is by substring with the longer codes checked first, so a spelled-out `wildcard` contains `d` and is read as a draft pick. An entry whose type matches no code is skipped.

### Sole Survivor entries

| Key | Required | Meaning |
|---|---|---|
| `survivor` | yes | Castaway name, resolved the same way as in a pick entry. |
| `episode` | yes | Episode at which this pick becomes active. It stays active until the player's next entry. |

A player can have one Sole Survivor entry per episode.
