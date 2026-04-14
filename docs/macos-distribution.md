# macOS Distribution

This is the release path for a smooth macOS install where OCR works immediately after the user drags the app into Applications.

## Prerequisites

- Apple Developer Program membership
- A `Developer ID Application` certificate installed in Keychain
- Xcode command line tools with `xcrun notarytool`
- A self-contained OCR runtime staged in `vendor/tesseract_runtime`

## Build

Stage the OCR runtime first:

```bash
python scripts/stage_tesseract_runtime.py /path/to/self-contained/tesseract-runtime
```

```bash
python -m pip install -e .[dev]
python scripts/build_desktop.py \
  --bundle-identifier com.openanonymizer.app \
  --codesign-identity "Developer ID Application: YOUR NAME (TEAMID)"
```

That produces `dist/OpenAnonymizer.app` with the bundled OCR runtime if `vendor/tesseract_runtime` exists.

## Notarize

Create a notarytool profile once:

```bash
xcrun notarytool store-credentials open-anonymizer-notary \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password"
```

Zip the app for upload:

```bash
ditto -c -k --keepParent dist/OpenAnonymizer.app dist/OpenAnonymizer-macos.zip
```

Submit and wait:

```bash
xcrun notarytool submit dist/OpenAnonymizer-macos.zip \
  --keychain-profile open-anonymizer-notary \
  --wait
```

Staple the ticket after approval:

```bash
xcrun stapler staple dist/OpenAnonymizer.app
```

## Why this matters

- Code signing proves the app came from your Developer ID identity.
- Notarization lets Apple scan the app and approve it for distribution outside the Mac App Store.
- Stapling attaches the notarization ticket to the app so first launch is smoother, even offline.
