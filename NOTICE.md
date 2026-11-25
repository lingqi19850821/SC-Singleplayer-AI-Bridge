# License and source notices

The project-authored API code and integration documentation are distributed under GNU AGPL version 3 only; see [LICENSE](LICENSE). The existing repository license is preserved. Source extraction and the portability edits below were made on 2026-11-16; updated 2026-11-25 to the v53 / ABI 27 native candidate set.

## BWAPI

BWAPI 4.4.0 is a technical reference for the bridge. The terrain implementation explicitly records adaptation of BWAPI Map/placement semantics and VF4 flag meanings in `native/stage5_terrain.h`. Any copyrightable BWAPI-derived material remains subject to the upstream LGPLv3 terms and attribution; this repository does not claim exclusive authorship of such material or replace its upstream license with a permissive license.

- Upstream: https://github.com/bwapi/bwapi/tree/v4.4.0
- Original license: https://github.com/bwapi/bwapi/blob/v4.4.0/LICENSE
- Included texts: [LGPL v3](LICENSES/LGPL-3.0.txt) and [GPL v3](LICENSES/GPL-3.0.txt).
- No BWAPI SDK, binary or complete reference checkout is bundled.

## Chkdraft terrain labels

`scapi/terrain_labels.json` records the upstream repository, source file and revision. It and the associated terrain labels retain the MIT notice in [scapi/terrain_labels.LICENSE.txt](scapi/terrain_labels.LICENSE.txt), Copyright (c) 2015-2026 Justin Forsberg. Preserve that notice when redistributing this material.

## Tips and game data

`scapi/tips_runtime.json` contains the existing curated API-oriented Tips and their source identifiers. The original-game inspiration is retained in its source metadata. This repository includes no screenshots, game executables, maps, saves or asset archives, and makes no claim to ownership of Blizzard's names or game materials.

## Standalone extraction changes

The API implementations and native protocol were retained. The package entry point now exposes Stage5; startup context points to the local interface reference. The Windows transport primitives were separated from the research-only probing CLI, and the base transport uses a package import. Native cleanup paths are resolved from this checkout. Episode builds accept an explicit compiler or `SCAPI_TCC`. The native loader requires an exact compiled DLL path and retains its game whitelist, identity checks and unknown-result behavior. `tools/build_native.py` builds the v53 / ABI 27 stability and next-episode candidate set, or the optional v50 diagnostic, without running either artifact.

Desktop/voice modules, model providers, role orchestration, development reports, private runtime data and tests were excluded. Compiler and runtime licensing remains the responsibility of distributors who choose to bundle those separate products; this source repository does not bundle them.
