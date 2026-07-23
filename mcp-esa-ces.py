#!/usr/bin/env python3
# Copyright 2026 Cisco Systems, Inc. and its affiliates
# 
# SPDX-License-Identifier: Apache-2.0  

"""
ESA Policy Hitcount MCP Server
Wraps the ESA policy hit count script as an MCP server tool.

Author: Adapted for MCP by Cisco AI Assistant
Date: 2026-06-12
"""

import base64
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import urllib3
import requests
import paramiko
from fastmcp import FastMCP, tools


# ---------------- USER PARAMETERS ---------------- #
ESA_IP = os.getenv("ESA_IP", "")                  # ESA IP or hostname
ESA_PORT = int(os.getenv("ESA_PORT", "6080"))     # ESA API port, default HTTP
API_USER = os.getenv("ESA_API_USER", "")          # ESA API username
API_PASS = os.getenv("ESA_API_PASS", "")          # ESA API password
VERIFY_SSL = os.getenv("ESA_VERIFY_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}
SSH_PORT = int(os.getenv("ESA_SSH_PORT", "22"))   # ESA CLI SSH port
# ------------------------------------------------- #

# Disable SSL warnings if VERIFY_SSL=False
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastMCP("ESA Policy Hitcount MCP Server")


def validate_api_config() -> str | None:
    missing = []
    if not ESA_IP:
        missing.append("ESA_IP")
    if not API_USER:
        missing.append("ESA_API_USER")
    if not API_PASS:
        missing.append("ESA_API_PASS")
    if missing:
        return (
            "Missing required environment variables for ESA API access: "
            + ", ".join(missing)
        )
    return None

def get_time_range(days_to_query: int):
    now = datetime.now(timezone.utc)
    end_time = now.replace(minute=0, second=0, microsecond=0)
    start_time = end_time - timedelta(days=days_to_query)
    startDate = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    endDate = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return startDate, endDate

def build_urls(startDate: str, endDate: str, top_n_policies: int):
    url_incoming = (
        f"http://{ESA_IP}:{ESA_PORT}/esa/api/v2.0/reporting/mail_policy_incoming/recipients_matched"
        f"?device_type=esa&startDate={startDate}&endDate={endDate}&top={top_n_policies}"
    )
    url_outgoing = (
        f"http://{ESA_IP}:{ESA_PORT}/esa/api/v2.0/reporting/mail_policy_outgoing/recipients_matched"
        f"?device_type=esa&startDate={startDate}&endDate={endDate}&top={top_n_policies}"
    )
    return url_incoming, url_outgoing

def get_auth_headers():
    auth_string = f"{API_USER}:{API_PASS}"
    encoded_auth = base64.b64encode(auth_string.encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded_auth}",
        "Accept": "application/json"
    }
    return headers

def fetch_policy_hits(url: str):
    config_error = validate_api_config()
    if config_error:
        return {"error": config_error}

    headers = get_auth_headers()
    try:
        response = requests.get(url, headers=headers, verify=VERIFY_SSL, timeout=30)
        response.raise_for_status()
        data = response.json()
        results = data['data']['resultSet']['recipients_matched']
        return results
    except Exception as e:
        return {"error": str(e)}


def fetch_config_text(url: str):
    config_error = validate_api_config()
    if config_error:
        return {"error": config_error}

    headers = get_auth_headers()
    try:
        response = requests.get(url, headers=headers, verify=VERIFY_SSL, timeout=60)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "application/json" in content_type:
            data = response.json()
            # Best-effort extraction from JSON wrappers.
            if isinstance(data, dict):
                for key in ("config", "config_text", "payload", "data"):
                    value = data.get(key)
                    if isinstance(value, str):
                        return {"config_text": value, "content_type": content_type}
                return {"config_text": str(data), "content_type": content_type}
            return {"config_text": str(data), "content_type": content_type}

        return {
            "config_text": response.text,
            "content_type": content_type or "text/plain",
        }
    except Exception as e:
        return {"error": str(e)}


def read_ssh_until(shell, expected_tokens: tuple[str, ...], timeout_seconds: float = 10.0) -> str:
    deadline = time.time() + timeout_seconds
    output = ""
    while time.time() < deadline:
        if shell.recv_ready():
            chunk = shell.recv(65535).decode("utf-8", errors="ignore")
            output += chunk
            if any(token in output for token in expected_tokens):
                return output
        else:
            time.sleep(0.2)
    return output


def fetch_policyconfig_via_ssh(ssh_host: str, ssh_user: str, ssh_pass: str, ssh_port: int = SSH_PORT) -> dict:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(needle.lower() in lowered for needle in needles)

    def _is_cluster_mode_prompt(text: str) -> bool:
        return _contains_any(
            text,
            (
                "what would you like to do?",
                "not yet been configured for the current cluster mode",
            ),
        )

    def _open_policyconfig_menu(shell) -> str:
        """Open policyconfig and wait for top-level policy selection prompt."""
        last_output = ""
        for _ in range(3):
            shell.send(b"policyconfig\n")
            output = read_ssh_until(
                shell,
                (
                    "Would you like to configure Incoming Mail Policy",
                    "Would you like to configure Incoming Mail Policies",
                    "Choose the operation you want to perform:",
                    "Incoming Mail Policy Configuration",
                    "Outgoing Mail Policy Configuration",
                    "[1]>",
                ),
                timeout_seconds=12,
            )
            last_output = output

            # Non-cluster behavior may jump directly into operation/config screens.
            if _contains_any(
                output,
                (
                    "choose the operation you want to perform:",
                    "incoming mail policy configuration",
                    "outgoing mail policy configuration",
                    "would you like to configure incoming mail policy",
                ),
            ):
                return output

            # If we reached the cluster prompt, return so caller can switch modes.
            if _is_cluster_mode_prompt(output):
                return output

        return last_output

    try:
        client.connect(
            hostname=ssh_host,
            port=ssh_port,
            username=ssh_user,
            password=ssh_pass,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell(width=200, height=2000)
        read_ssh_until(shell, (">", "#"), timeout_seconds=8)

        sections = {}
        for choice, label in (("1", "incoming"), ("2", "outgoing")):
            menu_output = _open_policyconfig_menu(shell)

            # Some ESA/CES deployments present cluster-mode prompt immediately.
            if _is_cluster_mode_prompt(menu_output):
                shell.send(b"1\n")
                menu_output = read_ssh_until(
                    shell,
                    (
                        "Choose the operation you want to perform:",
                        "Incoming Mail Policy Configuration",
                        "Outgoing Mail Policy Configuration",
                        "Would you like to configure Incoming Mail Policy",
                        "Would you like to configure Incoming Mail Policies",
                        "What would you like to do?",
                    ),
                    timeout_seconds=20,
                )

                if _is_cluster_mode_prompt(menu_output):
                    raise RuntimeError(
                        "Unable to switch policyconfig into cluster mode automatically. "
                        "Run policyconfig once manually and initialize cluster mode, then retry."
                    )

            shell.send(f"{choice}\n".encode())
            section_output = read_ssh_until(
                shell,
                (
                    "NOTICE: This configuration command has not yet been configured for the current cluster mode",
                    "What would you like to do?",
                    "Choose the operation you want to perform:",
                    "Incoming Mail Policy Configuration",
                    "Outgoing Mail Policy Configuration",
                ),
                timeout_seconds=12,
            )

            # In cluster mode, prompt appears after selecting incoming/outgoing policy config.
            if _is_cluster_mode_prompt(section_output):
                shell.send(b"1\n")
                cluster_switched_output = read_ssh_until(
                    shell,
                    (
                        "Choose the operation you want to perform:",
                        "Incoming Mail Policy Configuration",
                        "Outgoing Mail Policy Configuration",
                        "Would you like to configure Incoming Mail Policy",
                        "Would you like to configure Incoming Mail Policies",
                        "What would you like to do?",
                    ),
                    timeout_seconds=20,
                )
                section_output = section_output + "\n" + cluster_switched_output

                # If prompt still remains after selecting switch, fail with clear guidance.
                if _is_cluster_mode_prompt(cluster_switched_output):
                    raise RuntimeError(
                        "Unable to switch policyconfig into cluster mode automatically. "
                        "Run policyconfig once manually and initialize cluster mode, then retry."
                    )

            # For either deployment mode, ensure we captured a policy section before returning.
            if not _contains_any(
                section_output,
                (
                    "choose the operation you want to perform:",
                    "incoming mail policy configuration",
                    "outgoing mail policy configuration",
                ),
            ):
                raise RuntimeError(
                    "Unable to capture policyconfig section output for "
                    f"{label} policies. Check SSH role/CLI access and retry."
                )

            sections[label] = section_output
            shell.send(b"\n")
            read_ssh_until(shell, (">", "#"), timeout_seconds=8)

        return sections
    finally:
        client.close()


def aggregate_policy_hits(formatted_results) -> dict:
    if not isinstance(formatted_results, list):
        return {}

    totals = {}
    for item in formatted_results:
        if not isinstance(item, dict):
            continue
        policy_name = item.get("policy_name")
        hit_count = item.get("hit_count", 0)
        if not policy_name:
            continue
        totals[policy_name] = totals.get(policy_name, 0) + hit_count
    return totals


def to_formatted_policy_hits(raw_results):
    if isinstance(raw_results, dict) and "error" in raw_results:
        return {"error": raw_results["error"]}

    formatted = []
    for item in raw_results:
        for policy_name, hit_count in item.items():
            formatted.append({"policy_name": policy_name, "hit_count": hit_count})
    return formatted


def build_why_candidates(current_totals: dict, previous_totals: dict, top_n: int):
    ranked = sorted(current_totals.items(), key=lambda x: x[1], reverse=True)
    candidates = []
    for policy_name, current_hits in ranked[:top_n]:
        prev_hits = previous_totals.get(policy_name, 0)
        delta = current_hits - prev_hits
        pct_change = None
        if prev_hits > 0:
            pct_change = round((delta / prev_hits) * 100, 2)

        if prev_hits == 0 and current_hits > 0:
            likely_why = "New or newly active traffic/rule path for this policy compared to previous period."
            confidence = "medium"
        elif delta > 0:
            likely_why = "Increased traffic volume or broader policy match conditions in current period."
            confidence = "medium"
        elif delta < 0:
            likely_why = "Reduced traffic volume, tighter policy conditions, or upstream filtering before this policy."
            confidence = "low"
        else:
            likely_why = "Traffic pattern appears stable period over period."
            confidence = "low"

        evidence = {
            "current_hits": current_hits,
            "previous_hits": prev_hits,
            "delta": delta,
            "percent_change": pct_change,
        }

        candidates.append(
            {
                "policy_name": policy_name,
                "likely_why": likely_why,
                "confidence": confidence,
                "evidence": evidence,
                "next_data_to_collect": [
                    "top_sender_domains",
                    "top_recipient_groups",
                    "message_verdict_breakdown",
                    "policy_rule_change_history",
                ],
            }
        )

    return candidates


def normalize_policy_name(name: str) -> str:
    # Keep policy identity case-sensitive while normalizing surrounding formatting.
    return re.sub(r"\s+", " ", name.strip().strip('"').strip("'"))


def names_match(config_name: str, api_name: str) -> bool:
    normalized_config = normalize_policy_name(config_name)
    normalized_api = normalize_policy_name(api_name)
    return normalized_config == normalized_api


def resolve_api_match(config_name: str, api_names: list[str]) -> str | None:
    exact_matches = [candidate for candidate in api_names if names_match(config_name, candidate)]
    if exact_matches:
        return exact_matches[0]

    normalized_config = normalize_policy_name(config_name)
    prefix_matches = [
        candidate for candidate in api_names
        if normalize_policy_name(candidate).startswith(normalized_config)
        or normalized_config.startswith(normalize_policy_name(candidate))
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    return None


def extract_policy_names_from_config(config_text: str) -> list[str]:
    """
    Best-effort parser for ESA config text.
    Extracts policy names from common CLI/config line patterns.
    """
    patterns = [
        re.compile(r'^\s*(?:mailpolicy|policyconfig|incomingmailpolicy|outgoingmailpolicy)\s+["\']?(.+?)["\']?\s*$', re.IGNORECASE),
        re.compile(r'^\s*policy\s+name\s*[:=]\s*["\']?(.+?)["\']?\s*$', re.IGNORECASE),
        re.compile(r'^\s*name\s*[:=]\s*["\']?(.+?)["\']?\s*$', re.IGNORECASE),
    ]

    extracted = []
    seen = set()

    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        for pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue

            candidate = match.group(1).strip()
            # Keep only the first token if command has trailing args after name.
            if " " in candidate and not (candidate.startswith('"') or candidate.startswith("'")):
                candidate = candidate.split(" ", 1)[0]

            normalized = normalize_policy_name(candidate)
            if normalized and normalized not in seen:
                extracted.append(candidate)
                seen.add(normalized)
            break

    return extracted


def extract_policy_inventory_from_xml(config_text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract inbound/outbound mail policy names from ESA XML config export."""

    try:
        root = ET.fromstring(config_text)
    except ET.ParseError:
        return [], [], []

    def _collect_policy_names(container_path: str) -> list[str]:
        names = []
        seen = set()

        for policy_node in root.findall(container_path):
            name_node = policy_node.find("policy_name")
            if name_node is None or not name_node.text:
                continue

            candidate = name_node.text.strip()
            normalized = normalize_policy_name(candidate)
            if normalized and normalized not in seen:
                names.append(candidate)
                seen.add(normalized)

        return names

    incoming = _collect_policy_names(".//inbound_policies/policy")
    outgoing = _collect_policy_names(".//outbound_policies/policy")

    # Keep behavior consistent with SSH mode: disambiguate names present in both directions.
    incoming_normalized = {normalize_policy_name(name) for name in incoming}
    outgoing_normalized = {normalize_policy_name(name) for name in outgoing}
    shared = incoming_normalized & outgoing_normalized
    if shared:
        incoming = [f"{name}-incoming" if normalize_policy_name(name) in shared else name for name in incoming]
        outgoing = [f"{name}-outgoing" if normalize_policy_name(name) in shared else name for name in outgoing]

    combined = []
    combined_seen = set()
    for name in incoming + outgoing:
        normalized = normalize_policy_name(name)
        if normalized and normalized not in combined_seen:
            combined.append(name)
            combined_seen.add(normalized)

    return combined, incoming, outgoing


def parse_policy_inventory(config_text: str) -> tuple[list[str], list[str] | None, list[str] | None]:
    """Parse policy inventory from ESA XML export when possible, then fall back to text parsing."""

    all_xml, incoming_xml, outgoing_xml = extract_policy_inventory_from_xml(config_text)
    if all_xml:
        return all_xml, incoming_xml, outgoing_xml

    return extract_policy_names_from_config(config_text), None, None


def extract_policy_names_from_policyconfig_output(section_text: str) -> list[str]:
    names = []
    seen = set()
    capture = False

    for raw_line in section_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            continue
        if stripped.startswith("Choose the operation you want to perform:"):
            break
        if stripped.startswith("-----"):
            capture = True
            continue
        if not capture:
            continue
        if stripped == "Default":
            continue

        if line[0].isspace():
            continue

        name_field = line[:16].strip()
        if not name_field or name_field in {"Name:", "Threat Defense", "Connector:"}:
            continue
        if stripped.isdigit():
            continue
        if "  " not in line[16:]:
            continue

        candidate = "DEFAULT" if name_field.lower() == "default" else name_field
        if candidate not in seen:  # Use exact case-sensitive match for deduplication
            names.append(candidate)
            seen.add(candidate)

    return names


def load_policy_names_from_params(params: dict) -> tuple[list[str], list[str] | None, list[str] | None, str]:
    """
    Resolves policy inventory from one of:
            1) config_text (str)
            2) config_file_path (str, expected ESA XML export)
        3) fetch_via_ssh (bool)
    """
    config_text = params.get("config_text")
    if isinstance(config_text, str) and config_text.strip():
        policy_inventory, incoming_inventory, outgoing_inventory = parse_policy_inventory(config_text)
        return policy_inventory, incoming_inventory, outgoing_inventory, "config_text"

    config_file_path = params.get("config_file_path")
    if isinstance(config_file_path, str) and config_file_path.strip():
        try:
            with open(config_file_path, "r", encoding="utf-8", errors="ignore") as config_file:
                policy_inventory, incoming_inventory, outgoing_inventory = parse_policy_inventory(config_file.read())
                return policy_inventory, incoming_inventory, outgoing_inventory, "config_file_path"
        except Exception as e:
            raise ValueError(f"Unable to read config_file_path: {e}") from e

    ssh_host = params.get("ssh_host") or ESA_IP
    ssh_user = params.get("ssh_user") or API_USER
    ssh_pass = params.get("ssh_pass") or API_PASS
    ssh_port = params.get("ssh_port", SSH_PORT)
    if params.get("fetch_via_ssh"):
        if not ssh_host or not ssh_user or not ssh_pass:
            raise ValueError(
                "SSH inventory requires ssh_host, ssh_user, and ssh_pass (directly or via environment variables)."
            )
        sections = fetch_policyconfig_via_ssh(ssh_host, ssh_user, ssh_pass, ssh_port)
        incoming_names = extract_policy_names_from_policyconfig_output(sections.get("incoming", ""))
        outgoing_names = extract_policy_names_from_policyconfig_output(sections.get("outgoing", ""))

        # Track which normalized names appear in both directions.
        incoming_normalized = {normalize_policy_name(name) for name in incoming_names}
        outgoing_normalized = {normalize_policy_name(name) for name in outgoing_names}
        shared = incoming_normalized & outgoing_normalized

        # Rename policies that appear in both directions to make them distinctive.
        if shared:
            incoming_names = [f"{name}-incoming" if normalize_policy_name(name) in shared else name for name in incoming_names]
            outgoing_names = [f"{name}-outgoing" if normalize_policy_name(name) in shared else name for name in outgoing_names]

        return incoming_names + outgoing_names, incoming_names, outgoing_names, "ssh_policyconfig"

    raise ValueError("Provide one of: config_text, config_file_path, or fetch_via_ssh")


def compare_inventory_to_hits(policy_inventory: list[str], incoming_hits: dict, outgoing_hits: dict, incoming_inventory: list[str] | None = None, outgoing_inventory: list[str] | None = None):
    """
    Compare policy inventory against incoming and outgoing hit data.
    Returns results separated by direction (incoming/outgoing).

    If incoming_inventory and outgoing_inventory are provided (from SSH or XML), policies are compared
    only against their respective direction. Otherwise, all policies are compared against both directions.
    """
    # If direction-specific inventories are provided, use them; otherwise treat all as both directions.
    incoming_only = set(normalize_policy_name(p) for p in (incoming_inventory or []))
    outgoing_only = set(normalize_policy_name(p) for p in (outgoing_inventory or []))

    incoming_with_hits = []
    incoming_without_hits = []
    outgoing_with_hits = []
    outgoing_without_hits = []

    # Compare inventory against both directions (sorted by original name, preserving all policies).
    for original_name in sorted(policy_inventory, key=str.lower):
        normalized_name = normalize_policy_name(original_name)
        is_incoming_only = normalized_name in incoming_only
        is_outgoing_only = normalized_name in outgoing_only

        # Determine which direction(s) this policy should be compared against.
        should_check_incoming = (not incoming_inventory and not outgoing_inventory) or is_incoming_only or (not is_outgoing_only and not is_incoming_only)
        should_check_outgoing = (not incoming_inventory and not outgoing_inventory) or is_outgoing_only or (not is_outgoing_only and not is_incoming_only)

        incoming_api_name = None
        outgoing_api_name = None
        incoming_hit_count = 0
        outgoing_hit_count = 0

        if should_check_incoming:
            incoming_api_name = resolve_api_match(original_name, list(incoming_hits.keys()))
            incoming_hit_count = incoming_hits.get(incoming_api_name, 0) if incoming_api_name else 0

        if should_check_outgoing:
            outgoing_api_name = resolve_api_match(original_name, list(outgoing_hits.keys()))
            outgoing_hit_count = outgoing_hits.get(outgoing_api_name, 0) if outgoing_api_name else 0

        # Create entries for incoming if this is an incoming or ambiguous policy.
        if should_check_incoming:
            entry = {
                "policy_name_from_config": original_name,
                "matched_policy_name_from_api": incoming_api_name,
                "hit_count": incoming_hit_count,
            }
            if incoming_hit_count > 0:
                incoming_with_hits.append(entry)
            elif not is_outgoing_only:  # Add to incoming without_hits unless it's explicitly outgoing-only.
                incoming_without_hits.append(entry)

        # Create entries for outgoing only if it has outgoing hits OR is explicitly outgoing-only.
        if should_check_outgoing and (outgoing_hit_count > 0 or is_outgoing_only):
            entry = {
                "policy_name_from_config": original_name,
                "matched_policy_name_from_api": outgoing_api_name,
                "hit_count": outgoing_hit_count,
            }
            if outgoing_hit_count > 0:
                outgoing_with_hits.append(entry)
            else:
                outgoing_without_hits.append(entry)

    # API-only policies for incoming.
    incoming_api_only = []
    for api_name in incoming_hits:
        if not any(resolve_api_match(config_name, [api_name]) for config_name in policy_inventory):
            incoming_api_only.append({
                "policy_name_from_api": api_name,
                "hit_count": incoming_hits.get(api_name, 0),
            })

    # API-only policies for outgoing.
    outgoing_api_only = []
    for api_name in outgoing_hits:
        if not any(resolve_api_match(config_name, [api_name]) for config_name in policy_inventory):
            outgoing_api_only.append({
                "policy_name_from_api": api_name,
                "hit_count": outgoing_hits.get(api_name, 0),
            })

    incoming_api_only.sort(key=lambda x: x["hit_count"], reverse=True)
    outgoing_api_only.sort(key=lambda x: x["hit_count"], reverse=True)

    return {
        "incoming": {
            "with_hits": incoming_with_hits,
            "without_hits": incoming_without_hits,
            "api_only": incoming_api_only,
        },
        "outgoing": {
            "with_hits": outgoing_with_hits,
            "without_hits": outgoing_without_hits,
            "api_only": outgoing_api_only,
        },
    }

@app.tool()
def get_policy_hit_count_tool(params: dict) -> dict:
    """
    MCP tool to fetch ESA policy hit counts.
    Accepts parameters:
      - days_to_query (int): number of days to query (default 1)
      - top_n_policies (int): top N policies to retrieve (default 10)
    Returns a dict with incoming and outgoing policy hit counts.
    """
    days_to_query = params.get("days_to_query", 1)
    top_n_policies = params.get("top_n_policies", 10)

    startDate, endDate = get_time_range(days_to_query)
    url_incoming, url_outgoing = build_urls(startDate, endDate, top_n_policies)

    incoming_results = fetch_policy_hits(url_incoming)
    outgoing_results = fetch_policy_hits(url_outgoing)

    return {
        "time_range": {"start": startDate, "end": endDate},
        "incoming_policy_hits": to_formatted_policy_hits(incoming_results),
        "outgoing_policy_hits": to_formatted_policy_hits(outgoing_results)
    }


@app.tool()
def explain_top_policy_hits_tool(params: dict) -> dict:
    """
    MCP tool to answer: "Which policy has the most hit count, and why (likely)?"
    Uses period-over-period comparison to produce evidence-based why candidates.

    Parameters:
      - days_to_query (int): current analysis window in days (default 7)
      - top_n_policies (int): number of policies returned (default 5)
      - compare_with_previous_period (bool): compare with prior equal-length window (default True)
    """
    days_to_query = params.get("days_to_query", 7)
    top_n_policies = params.get("top_n_policies", 5)
    compare_with_previous_period = params.get("compare_with_previous_period", True)

    startDate, endDate = get_time_range(days_to_query)
    url_incoming, url_outgoing = build_urls(startDate, endDate, top_n_policies)

    incoming_current_raw = fetch_policy_hits(url_incoming)
    outgoing_current_raw = fetch_policy_hits(url_outgoing)

    incoming_current = to_formatted_policy_hits(incoming_current_raw)
    outgoing_current = to_formatted_policy_hits(outgoing_current_raw)

    if isinstance(incoming_current, dict) and "error" in incoming_current:
        return {"error": f"Incoming query failed: {incoming_current['error']}"}
    if isinstance(outgoing_current, dict) and "error" in outgoing_current:
        return {"error": f"Outgoing query failed: {outgoing_current['error']}"}

    incoming_totals_current = aggregate_policy_hits(incoming_current)
    outgoing_totals_current = aggregate_policy_hits(outgoing_current)

    incoming_previous_totals = {}
    outgoing_previous_totals = {}
    comparison_time_range = None

    if compare_with_previous_period:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        current_end = now
        current_start = current_end - timedelta(days=days_to_query)
        previous_end = current_start
        previous_start = previous_end - timedelta(days=days_to_query)

        prev_start_str = previous_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        prev_end_str = previous_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        comparison_time_range = {"start": prev_start_str, "end": prev_end_str}

        prev_url_incoming, prev_url_outgoing = build_urls(prev_start_str, prev_end_str, top_n_policies)
        incoming_prev_raw = fetch_policy_hits(prev_url_incoming)
        outgoing_prev_raw = fetch_policy_hits(prev_url_outgoing)

        incoming_prev = to_formatted_policy_hits(incoming_prev_raw)
        outgoing_prev = to_formatted_policy_hits(outgoing_prev_raw)

        if not (isinstance(incoming_prev, dict) and "error" in incoming_prev):
            incoming_previous_totals = aggregate_policy_hits(incoming_prev)
        if not (isinstance(outgoing_prev, dict) and "error" in outgoing_prev):
            outgoing_previous_totals = aggregate_policy_hits(outgoing_prev)

    incoming_why = build_why_candidates(incoming_totals_current, incoming_previous_totals, top_n_policies)
    outgoing_why = build_why_candidates(outgoing_totals_current, outgoing_previous_totals, top_n_policies)

    top_incoming = incoming_why[0] if incoming_why else None
    top_outgoing = outgoing_why[0] if outgoing_why else None

    return {
        "analysis_time_range": {"start": startDate, "end": endDate},
        "comparison_time_range": comparison_time_range,
        "top_policy_summary": {
            "incoming_top_policy": top_incoming,
            "outgoing_top_policy": top_outgoing,
        },
        "incoming_policy_why_candidates": incoming_why,
        "outgoing_policy_why_candidates": outgoing_why,
        "note": "Likely why values are heuristic until message-level telemetry and policy change logs are integrated.",
    }


@app.tool()
def compare_config_to_hit_counts_tool(params: dict) -> dict:
    """
    MCP tool to identify policies with and without hits by comparing:
      - policy inventory from customer ESA config input
      - policy hits from ESA reporting API

    Parameters:
      - days_to_query (int): window in days for hit lookup (default 30)
      - top_n_policies_for_comparison (int): API top N for hit lookup (default 1000)
            - config_text (str): optional raw ESA config text/XML
            - config_file_path (str): optional local file path to ESA XML config export (preferred)
            - fetch_via_ssh (bool): fetch policy inventory from ESA CLI using policyconfig
            - ssh_host/ssh_user/ssh_pass/ssh_port: optional SSH overrides
    """
    days_to_query = params.get("days_to_query", 30)
    top_n_for_comparison = params.get("top_n_policies_for_comparison", 1000)

    try:
        policy_inventory, incoming_inventory, outgoing_inventory, source_type = load_policy_names_from_params(params)
    except ValueError as e:
        return {"error": str(e)}

    if not policy_inventory:
        return {
            "error": "No policies parsed from provided config input.",
            "hint": "For file input, provide ESA XML config export, or use fetch_via_ssh=true for CLI inventory collection.",
        }

    startDate, endDate = get_time_range(days_to_query)
    url_incoming, url_outgoing = build_urls(startDate, endDate, top_n_for_comparison)

    incoming_raw = fetch_policy_hits(url_incoming)
    outgoing_raw = fetch_policy_hits(url_outgoing)

    incoming_formatted = to_formatted_policy_hits(incoming_raw)
    outgoing_formatted = to_formatted_policy_hits(outgoing_raw)

    if isinstance(incoming_formatted, dict) and "error" in incoming_formatted:
        return {"error": f"Incoming query failed: {incoming_formatted['error']}"}
    if isinstance(outgoing_formatted, dict) and "error" in outgoing_formatted:
        return {"error": f"Outgoing query failed: {outgoing_formatted['error']}"}

    incoming_totals = aggregate_policy_hits(incoming_formatted)
    outgoing_totals = aggregate_policy_hits(outgoing_formatted)
    comparison_result = compare_inventory_to_hits(
        policy_inventory,
        incoming_totals,
        outgoing_totals,
        incoming_inventory,
        outgoing_inventory,
    )

    policies_with_hits = comparison_result["incoming"]["with_hits"] + comparison_result["outgoing"]["with_hits"]
    policies_without_hits = comparison_result["incoming"]["without_hits"] + comparison_result["outgoing"]["without_hits"]
    api_only_policies = (
        [{**p, "direction": "incoming"} for p in comparison_result["incoming"]["api_only"]]
        + [{**p, "direction": "outgoing"} for p in comparison_result["outgoing"]["api_only"]]
    )

    inventory_unique_count = len({normalize_policy_name(x) for x in policy_inventory})

    return {
        "analysis_time_range": {"start": startDate, "end": endDate},
        "query_parameters": {
            "days_to_query": days_to_query,
            "top_n_policies_for_comparison": top_n_for_comparison,
        },
        "config_inventory_source": source_type,
        "summary": {
            "inventory_policy_count": inventory_unique_count,
            "policies_with_hits_incoming": len(comparison_result["incoming"]["with_hits"]),
            "policies_with_hits_outgoing": len(comparison_result["outgoing"]["with_hits"]),
            "policies_without_hits": len(comparison_result["incoming"]["without_hits"]),
            "api_only_incoming": len(comparison_result["incoming"]["api_only"]),
            "api_only_outgoing": len(comparison_result["outgoing"]["api_only"]),
            # Backward-compatible aggregate counters.
            "policies_with_hits": len(policies_with_hits),
            "api_only_policies": len(api_only_policies),
        },
        # Stage 1-aligned directional payload used by AI/NLP interaction and report generator.
        "incomingPolicies": comparison_result["incoming"]["with_hits"] + comparison_result["incoming"]["without_hits"],
        "outgoingPolicies": comparison_result["outgoing"]["with_hits"] + comparison_result["outgoing"]["without_hits"],
        "incoming": comparison_result["incoming"],
        "outgoing": comparison_result["outgoing"],
        # Legacy merged fields retained for compatibility with older clients.
        "policies_with_hits": policies_with_hits,
        "policies_without_hits": policies_without_hits,
        "api_only_policies": sorted(api_only_policies, key=lambda x: x["hit_count"], reverse=True),
        "note": "If API top-N is too small, some hit policies may be missing from comparison. Increase top_n_policies_for_comparison as needed.",
    }


@app.tool()
def fetch_esa_config_text_tool(params: dict) -> dict:
    """
    MCP tool to fetch ESA config text using configured API credentials.

    Parameters:
      - config_api_path (str): API path, e.g. /esa/api/v2.0/<config-endpoint>
      - config_url (str): optional full URL override (if provided, takes precedence)
      - save_to_file_path (str): optional local path to save fetched config text

    Notes:
      - Exact config endpoint varies by ESA/SMA version and deployment.
            - This tool authenticates with ESA_API_USER/ESA_API_PASS and returns raw text.
    """
    config_url = params.get("config_url")
    config_api_path = params.get("config_api_path")
    save_to_file_path = params.get("save_to_file_path")

    if not config_url:
        if not config_api_path:
            return {
                "error": "Provide config_url or config_api_path",
                "hint": "Example config_api_path: /esa/api/v2.0/<config-endpoint>",
            }
        path = str(config_api_path).strip()
        if not path.startswith("/"):
            path = f"/{path}"
        config_url = f"http://{ESA_IP}:{ESA_PORT}{path}"

    fetch_result = fetch_config_text(config_url)
    if "error" in fetch_result:
        return {
            "error": f"Failed to fetch config: {fetch_result['error']}",
            "requested_url": config_url,
        }

    config_text = fetch_result.get("config_text", "")
    if not isinstance(config_text, str) or not config_text.strip():
        return {
            "error": "Fetched response is empty or not text.",
            "requested_url": config_url,
        }

    save_status = None
    if isinstance(save_to_file_path, str) and save_to_file_path.strip():
        try:
            with open(save_to_file_path, "w", encoding="utf-8") as out:
                out.write(config_text)
            save_status = {
                "saved": True,
                "path": save_to_file_path,
            }
        except Exception as e:
            save_status = {
                "saved": False,
                "path": save_to_file_path,
                "error": str(e),
            }

    return {
        "requested_url": config_url,
        "content_type": fetch_result.get("content_type", "unknown"),
        "config_text_length": len(config_text),
        "preview_first_500_chars": config_text[:500],
        "save_status": save_status,
        "next_step": "Use compare_config_to_hit_counts_tool with config_text or config_file_path.",
    }

def get_esa_time():
    return {
        "time": datetime.now().isoformat()
    }

if __name__ == "__main__":
#    app.run(host="0.0.0.0", port=8080, streamable_http=True)
#    app.run()
    app.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8080,
    )
