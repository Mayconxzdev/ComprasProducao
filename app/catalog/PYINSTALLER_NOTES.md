# PyInstaller Notes

Ensure the embedded catalog file is bundled:

```bash
pyinstaller app/main.py --collect-data app.assets.catalog
```

If using a `.spec` file, include:

```python
from PyInstaller.utils.hooks import collect_data_files
datas = collect_data_files("app.assets.catalog")
```
