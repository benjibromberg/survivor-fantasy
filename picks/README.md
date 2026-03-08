# Pick Files

Place your league's draft JSON files here. These are **not** tracked by git — each league has its own.

## Format

Each JSON file has a `scoring` field and a `picks` object keyed by fantasy player name:

```json
{
  "scoring": "legacy",
  "picks": {
    "PlayerA": [
      {"survivor": "Name", "type": "d"},
      {"survivor": "Name", "type": "w"}
    ],
    "PlayerB": [
      {"survivor": "Name", "type": "d"},
      {"survivor": "Name", "type": "pmr_d"}
    ]
  }
}
```

- `scoring`: `"legacy"` or `"default"` — sets the season's scoring config
- `survivor`: Castaway name (matched against survivoR dataset)
- `type`: Pick type code — `d` (draft), `w` (wildcard), `pmr_w` (half-point replacement), `pmr_d` (full replacement)

## File Naming

Map filenames in `SEASON_PICK_FILES` in `seed.py`:

```python
SEASON_PICK_FILES = {
    45: 'season45.json',
    46: 'season46.json',
    47: 'season47_snakedraft.json',
    49: 'season49_snakedraft.json',
}
```

## Usage

```bash
python seed.py --picks-dir ./picks
```
