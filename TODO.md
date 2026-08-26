# TODO

Nothing requested is outstanding. Completed work is not listed here — `git
log` has it, and the constraints a future change needs to respect are in
`README.md` under "Constraints worth knowing before changing things".

## Open, but not requested

- **Same-weekday comparison in the Today section.** "Is Wednesday always this
  meaty?" needs several of each weekday before it says anything; there are
  currently 16 weekday menus in total. `stats.json`'s `dishes[].dates` already
  carries what it would need.
- **Pruning `data/`.** Nothing deletes old snapshots — the archive is
  append-only by design. At ~86 KB/day it is 1.7 MB over 20 files, so roughly
  21 MB a year. Not a problem for years, and worth leaving alone until it is:
  the raw archive is the only thing everything else can be rebuilt from.
