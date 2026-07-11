# Copyright 2026 Cisco Systems, Inc. and its affiliates
# 
# SPDX-License-Identifier: Apache-2.0  

import asyncio
import argparse
import json
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession


def to_jsonable(obj):
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, list, dict)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def save_json(path: str, payload):
    with open(path, "w", encoding="utf-8") as out:
        json.dump(payload, out, indent=2, ensure_ascii=False)


async def run_client(
    mode: str,
    url: str,
    days_to_query: int,
    top_n_policies: int,
    config_file_path: str | None,
    output_json: str | None,
):
    async with streamablehttp_client(
        url
    ) as (read_stream, write_stream, _):

        async with ClientSession(
            read_stream,
            write_stream
        ) as session:

            await session.initialize()

            if mode == "list-tools":
                tools = await session.list_tools()
                print("TOOLS:")
                print(tools)
                if output_json:
                    save_json(output_json, to_jsonable(tools))
                return

            if mode == "compare-config":
                if not config_file_path:
                    raise ValueError("--config-file is required when --mode compare-config")

                result = await session.call_tool(
                    "compare_config_to_hit_counts_tool",
                    {
                        "params": {
                            "days_to_query": days_to_query,
                            "top_n_policies_for_comparison": top_n_policies,
                            "config_file_path": config_file_path,
                        }
                    }
                )
                print(result)
                if output_json:
                    save_json(output_json, to_jsonable(result))
                return

            result = await session.call_tool(
                "get_policy_hit_count_tool",
                {
                    "params": {
                        "days_to_query": days_to_query,
                        "top_n_policies": top_n_policies
                    }
                }
            )

            print(result)
            if output_json:
                save_json(output_json, to_jsonable(result))


def parse_args():
    parser = argparse.ArgumentParser(description="MCP client for ESA Policy Hitcount server")
    parser.add_argument(
        "--mode",
        choices=["run", "list-tools", "compare-config"],
        default="run",
        help="run: call get_policy_hit_count_tool, list-tools: display available MCP tools, compare-config: find zero-hit policies from ESA config (XML export expected for --config-file)",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080/mcp",
        help="MCP streamable-http endpoint",
    )
    parser.add_argument("--days", type=int, default=1, help="days_to_query for tool call")
    parser.add_argument("--top", type=int, default=5, help="top_n_policies for tool call")
    parser.add_argument(
        "--config-file",
        default=None,
        help="Path to ESA XML config export file (required for --mode compare-config)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional output path to save tool result as JSON",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_client(args.mode, args.url, args.days, args.top, args.config_file, args.output_json))
