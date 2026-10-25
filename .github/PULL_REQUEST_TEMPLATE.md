## What this changes

<!-- One or two sentences. Link the issue if there is one: Fixes #123 -->

## Kind of change

- [ ] Bug fix
- [ ] New or improved detection engine
- [ ] Fingerprint / band-plan data (`config/vendors`, `config/spectrum`)
- [ ] termite / SDR
- [ ] Docs, build or CI

## Checklist

- [ ] `make check` passes (ruff, mypy, tests)
- [ ] `anteater selftest --no-radio` passes
- [ ] New engines or rules have a test, and the selftest scenario still trips every engine
- [ ] Fingerprint changes say where the signature came from (capture, vendor docs, public research)
- [ ] Still receive-only: nothing here transmits, deauths, jams or connects to other devices
- [ ] No personal data (MACs, locations, captures) in the diff

## How I tested it

<!-- Hardware used, replay file, or unit tests -->
