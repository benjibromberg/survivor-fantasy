# Pick Files

Place your league's draft xlsx files here. These are **not** tracked by git — each league has its own.

## Format

Each xlsx file has columns:

| player | voted_out | made_jury | PlayerA | PlayerB | PlayerC | ... |
|--------|-----------|-----------|---------|---------|---------|-----|
| Name   | 1         | FALSE     |         | d       |         | ... |
| Name   | 0         | TRUE      | w       |         | pmr_d   | ... |

- `player`: Castaway name (matched against survivoR dataset)
- `voted_out`, `made_jury`: Legacy columns (ignored — data comes from survivoR)
- Fantasy player columns contain pick type codes: `d` (draft), `w` (wildcard), `pmr_w` (half-point replacement), `pmr_d` (full replacement)

## File Naming

Map filenames in `SEASON_PICK_FILES` in `seed.py`:

```python
SEASON_PICK_FILES = {
    46: 'season46.xlsx',
    47: 'season47_snakedraft.xlsx',
    49: 'season49_snakedraft.xlsx',
}
```

## Usage

```bash
python seed.py --picks-dir ./picks
```
