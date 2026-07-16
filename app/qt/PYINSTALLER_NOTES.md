## PyInstaller Qt Build (Windows)

### Production runtime (`onedir`) - fastest startup
```powershell
pyinstaller app/main.py `
  --name ComprasVesper `
  --onedir `
  --windowed `
  --icon app/assets/icons/comprasvesper.ico `
  --clean `
  --noconfirm `
  --noupx `
  --collect-data app.assets.catalog `
  --collect-data app.assets.theme `
  --collect-data app.qt.resources
```

### Optional fallback (`onefile`) - smaller distribution, slower startup
```powershell
pyinstaller app/main.py `
  --name ComprasVesper `
  --onefile `
  --windowed `
  --icon app/assets/icons/comprasvesper.ico `
  --clean `
  --noconfirm `
  --noupx `
  --collect-data app.assets.catalog `
  --collect-data app.assets.theme `
  --collect-data app.qt.resources
```

### First-run prewarm (recommended)
After build/install, run once to precompute cache:
```powershell
dist\ComprasVesper\ComprasVesper.exe --prewarm --force-refresh
```

### Installer
- Inno Setup script: `installer/ComprasVesper.iss`
- Automated build script: `build/build_release.ps1`

Example:
```powershell
powershell -ExecutionPolicy Bypass -File build\build_release.ps1 -Version 1.0.0
```

### Notes
- Qt is the production UI entrypoint.
- Legacy CTk code now lives in `app/legacy_ui_ctk` and is out of runtime imports.
- App data and cache are stored under `%APPDATA%\ComprasApp`.
- Do not run the app directly from server share paths; install locally on each PC.
