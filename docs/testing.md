# Validação local

```powershell
uv venv .venv --python 3.12
uv pip install -r requirements-dev.txt --python .venv\Scripts\python.exe
$env:COMPRAS_VESPER_DEMO = "1"
$env:APPDATA = "$PWD\.demo-runtime"
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.tools.smoke_core
```

Os testes existentes cobrem contratos de UI, seleção de destinatário, busca com tolerância a erro, rastreamento IMAP, análise básica de proposta comercial e comportamento do modo demonstração.
