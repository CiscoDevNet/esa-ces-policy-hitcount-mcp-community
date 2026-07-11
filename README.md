# ESA (CES) Policy Hitcount MCP Server (Stage 2)

Stage 2 provides an MCP-based workflow for ESA policy analysis with AI/NLP conversational investigation.

Stage 1 reference (script-only baseline):
- https://github.com/CiscoDevNet/ESA-Policy-Hitcount
- Stage 1 runs direct scripts to fetch and compare policy hit data, without MCP tool orchestration.

## What Stage 2 Adds

With Stage 2, MCP clients can call tools to:
- Query incoming and outgoing policy hit counts for a selected time window.
- Compare policy inventory (ESA XML config export or SSH `policyconfig`) versus API hit results using Stage 1-equivalent crunching logic.
- Identify zero-hit and low-traffic policies more reliably.
- Ask follow-up investigation questions and get context-aware summaries.
- Optionally export comparison output to JSON and generate a DOCX health report.

Primary server and client:
- `mcp-esa-ces.py`
- `mcp-client.py`

## Stage 2 Tooling Surface

Server exposes these MCP tools:
- `get_policy_hit_count_tool`
  - Inputs: `days_to_query`, `top_n_policies`
  - Returns: incoming/outgoing policy hit lists with counts
- `compare_config_to_hit_counts_tool`
  - Inputs: `config_text` or `config_file_path` (ESA XML config export expected for file input)
  - Optional SSH inventory mode: `fetch_via_ssh`, `ssh_host`, `ssh_user`, `ssh_pass`, `ssh_port`
  - Returns: `policies_with_hits`, `policies_without_hits`, summary counts
- `explain_top_policy_hits_tool`
  - Inputs: `days_to_query`, `top_n_policies`, `compare_with_previous_period`
  - Returns: period-over-period explanation candidates with evidence
- `fetch_esa_config_text_tool`
  - Inputs: `config_api_path`/`config_url`, optional `save_to_file_path`
  - Returns: config text preview and save status

## Prerequisites

- Python 3.11+
- Node.js (for DOCX generation script)
- Network reachability from this host to ESA API/management interfaces
- ESA API credentials with reporting access
- ESA policy inventory source:
  - Preferred: ESA XML config export
  - Alternative: SSH inventory collection from `policyconfig`

Python packages used by server:
- `fastmcp`
- `paramiko`
- `requests`
- `urllib3`

## Quick Start (Stage 2)

1. Create and activate virtual environment

```bash
python3 -m venv env
source env/bin/activate
```

2. Install dependencies

```bash
pip install fastmcp paramiko requests urllib3
npm install
```

3. Start MCP server

```bash
python3 mcp-esa-ces.py
```

4. Keep server running, then choose one of the usage paths below

Default endpoint:
- `http://127.0.0.1:8080/mcp`

## Stage 2 Usage Paths

### A) Backend Smoke Test (CLI)

Use this path to verify server health and ESA connectivity only.

In another terminal:

```bash
python3 mcp-client.py --mode list-tools
python3 mcp-client.py --mode run --days 1 --top 5
python3 mcp-client.py --mode compare-config --days 30 --top 1000 --config-file /path/to/esa-config.xml
python3 mcp-client.py --mode compare-config --days 30 --top 1000 --config-file /path/to/esa-config.xml --output-json compare-config-output.json
```

### B) AI Conversational Workflow (Primary)

This is the main Stage 2 value.

1. Connect your MCP-capable AI client to `http://127.0.0.1:8080/mcp`.
2. Provide policy inventory as ESA XML config export (or use SSH inventory mode).
3. Ask natural-language questions; AI will call tools and guide the investigation.

Suggested prompts:
- Which policies have zero hits in the last 30 days based on this ESA XML export?
- Group policies into active, low-traffic, and inactive.
- Which zero-hit policies are safer to disable first, and why?
- Draft customer communication before disabling inactive policies.

## Stage 2 Workflow Details

1. Collect policy inventory
- Use ESA XML config export file, or
- Use SSH `policyconfig` collection through MCP tool parameters

2. Run policy comparison for a target window (for example, 30 days)

```bash
python3 mcp-client.py --mode compare-config --days 30 --top 1000 --config-file /path/to/esa-config.xml --output-json compare-config-output.json
```

Input guidance:
- For `--config-file`, use ESA XML config export.
- Do not use CLI table text dumps (for example, `policyconfig` terminal output) as file input.
- If you only have CLI access, use SSH inventory mode (`fetch_via_ssh=true`) instead.

3. Use MCP/AI Q&A to investigate actionability (primary Stage 2 value)
- Zero-hit policies
- Active vs low-traffic vs inactive grouping
- Safer removal candidates
- Customer messaging before disablement

4. Optional: generate DOCX report artifact

```bash
node generate-health-report.js --input compare-config-output.json --output ESA-Policy-Health-Check.docx
```

## Screenshot Walkthrough (Stage 2 Q&A)

The following screenshots show a practical Stage 2 investigation sequence.

### Q1: Which policies have zero hits in the last 30 days?

![Stage 2 Q1 - Zero-hit policies](docs/images/stage2-q2.png)

Use this to identify initial cleanup candidates and confirm inventory/hit-count coverage.

### Q2: Summarize policies into active, low-traffic, and inactive groups

![Stage 2 Q2 - Policy grouping](docs/images/stage2-q1.png)

This gives a fast triage view for operational decisions.

### Q3: Which zero-hit policies are safer to remove first, and why?

![Stage 2 Q3 - Removal prioritization](docs/images/stage2-q3.png)

This helps prioritize lower-risk candidates (for example, test/lab-like policy names) before business-critical flows.

### Q4: What should be communicated before disabling zero-hit policies?

![Stage 2 Q4 - Customer communication guidance](docs/images/stage2-q4.png)

Use this to frame customer validation checks and staged rollout guidance.

## Optional Report Artifact

DOCX output preview:

![Sample ESA Policy Health Report Output](docs/images/stage1-sample-docx.png)

## Notes and Caveats

- API hit-count endpoints report observed traffic; they do not independently prove policy intent.
- Zero hits in a period is a strong signal, but not sufficient alone for removal.
- Validate exception, seasonal, fallback, and compliance-related policies before disablement.
- SSH `policyconfig` output may include truncated names; treat name matching carefully in review.

## Security and Publishing Hygiene

Before publishing, remove any hardcoded credentials and move secrets to environment variables.

Recommended `.gitignore` entries:
- `env/`
- `node_modules/`
- `__pycache__/`
- `.DS_Store`
- `*.pyc`
- `*.log`
- `.env`
