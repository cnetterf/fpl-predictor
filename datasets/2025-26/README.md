# FPL 2025-26 archive

The immutable finished-season archive is published as the GitHub Release:

- https://github.com/cnetterf/fpl-predictor/releases/tag/data-2025-26

## Release assets

- `fpl-2025-26-raw-sources.zip`
  - Complete GW1-GW38 data from:
    - `vaastav/Fantasy-Premier-League` at commit
      `f2090d378ebd1b0c3d14884770dde95f38c50a0d`
    - `olbauday/FPL-Core-Insights` at commit
      `66ed88d8b9911c0922f2c223a2e26c98defc0c0b`
- `fpl-2025-26-model-snapshot.zip`
  - Model code and generated artifacts from commit
    `6e8a72e40033f1f1da1e0cc6a90960e0ddaefcd7`
  - The model cache in this snapshot covers GW1-GW35.
- `release-manifest.json`
- `SHA256SUMS`

## Checksums

```text
ff28741d71a70b289b06028e3b1c83220f75acc898627aa89cfe29a94a76bf29  fpl-2025-26-raw-sources.zip
a36586668c4b5512a80b39b04cfff0cb38268c879b576ad292c3ebc11e64aac2  fpl-2025-26-model-snapshot.zip
```

Run `python3 archive_season.py` to rebuild the archives from the pinned source
repositories and the current local model snapshot.
