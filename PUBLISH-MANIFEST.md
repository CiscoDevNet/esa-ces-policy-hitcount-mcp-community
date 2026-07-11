# Stage 2 Publish Manifest

Use this checklist to publish only the intended Stage 2 assets.

## Upload These Files

- .env
- .env.example
- .gitignore
- README.md
- generate-health-report.js
- mcp-client.py
- mcp-esa-ces.py
- package-lock.json
- package.json
- report-input.example.json

## Upload These Directories

- docs/

## Do Not Upload

- env/
- .venv/
- venv/
- node_modules/
- __pycache__/
- .DS_Store
- *.log
- *.tmp

## Mandatory Pre-Publish Checks

1. Confirm no hardcoded credentials in Python files.
2. Confirm SPDX header exists in all Python files.
3. Confirm `.env` still contains dummy values before publish.
4. Confirm local endpoint examples use `http://127.0.0.1:8080/mcp` unless intentionally documenting remote deployment.

## Optional Smoke Test Before Push

```bash
python3 mcp-client.py --url http://127.0.0.1:8080/mcp --mode list-tools
python3 mcp-client.py --url http://127.0.0.1:8080/mcp --mode run --days 1 --top 5
```

## One-Line Upload Scope

Publish everything under `github-ready-stage2/` except items listed in "Do Not Upload".
