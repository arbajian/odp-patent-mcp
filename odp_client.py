"""
odp_client.py — a minimal SINGLE-STEP test harness for the ODP MCP server,
driven by a user-selectable LLM backend (DeepSeek, Claude, etc.).

WHAT THIS DOES, in plain English:
  1. Starts the ODP MCP server as a child process. The server exposes 12 tools
     for querying the USPTO Open Data Portal.
  2. Asks that server which tools it has (list_tools).
  3. Translates those tool descriptions into the format the chosen LLM wants.
  4. Sends YOUR natural-language question (passed on the command line) to
     the LLM along with the list of available tools.
  5. If the LLM decides to call ONE tool, we print the tool name + arguments,
     actually run that tool on the MCP server, print the raw result, then hand
     the result back to the LLM for a final human-readable answer.
  6. If the LLM does NOT want a tool, we just print its text answer.

This is intentionally a SINGLE step: we let the LLM pick at most one tool,
run it once, and get one follow-up answer. There is no multi-round "agent
loop" here — that keeps the harness easy to read and debug.

HOW TO RUN:
    uv run python odp_client.py "find patents by inventor Smith"
    uv run python odp_client.py --model claude-sonnet-4-6 "search for neural network patents"

REQUIREMENTS:
    - A .env file in this folder containing API keys for your chosen backend:
      * DEEPSEEK_API_KEY=sk-... (for DeepSeek models)
      * ANTHROPIC_API_KEY=sk-ant-... (for Claude models)
    - The ODP MCP server accessible via: uv run -C /Users/pierrearbajian/patent_mcp_fork_odp python -m odp_patent_mcp
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# =============================================================================
# CONFIGURATION
# =============================================================================

MCP_SERVER_PROGRAM = "uv"
MCP_SERVER_ARGS = [
    "run",
    "--directory",
    "/Users/pierrearbajian/patent_mcp_fork_odp",
    "python",
    "-m",
    "odp_patent_mcp",
]

# Model families: map model name to (provider, base_config)
MODEL_FAMILIES = {
    "deepseek-chat": ("openai", {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_prefix": "sk-",
    }),
    "claude-haiku-4-5": ("anthropic", {
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_prefix": "sk-ant-",
    }),
    "claude-sonnet-4-6": ("anthropic", {
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_prefix": "sk-ant-",
    }),
    "claude-opus-4-8": ("anthropic", {
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_prefix": "sk-ant-",
    }),
}


# =============================================================================
# TOOL SCHEMA CONVERTERS
# =============================================================================

def mcp_tools_to_openai_format(mcp_tools):
    """Convert MCP tool definitions to OpenAI format."""
    openai_tools = []
    for tool in mcp_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        })
    return openai_tools


def mcp_tools_to_anthropic_format(mcp_tools):
    """Convert MCP tool definitions to Anthropic format."""
    anthropic_tools = []
    for tool in mcp_tools:
        anthropic_tools.append({
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        })
    return anthropic_tools


# =============================================================================
# LLM CLIENT FACTORY & HELPERS
# =============================================================================

def get_llm_client(model: str):
    """Create and return the appropriate LLM client based on model name."""
    if model not in MODEL_FAMILIES:
        print(f"ERROR: Unknown model '{model}'.")
        print(f"Supported models: {', '.join(MODEL_FAMILIES.keys())}")
        sys.exit(1)

    provider, config = MODEL_FAMILIES[model]
    env_var = config["api_key_env"]
    prefix = config["api_key_prefix"]

    api_key = os.getenv(env_var)
    if not api_key or not api_key.startswith(prefix):
        print(f"ERROR: {env_var} is not set or invalid.")
        print(f"Add a valid key ({prefix}...) to .env for {model}.")
        sys.exit(1)

    if provider == "openai":
        from openai import OpenAI
        return OpenAI(api_key=api_key, base_url=config["base_url"])
    elif provider == "anthropic":
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_provider(model: str) -> str:
    """Return the provider name for a given model."""
    return MODEL_FAMILIES[model][0]


# =============================================================================
# OPENAI-COMPATIBLE BACKEND
# =============================================================================

async def call_openai_backend(client, model: str, mcp_tools, user_query: str, session):
    """Execute multi-turn tool calls using OpenAI-compatible backend."""
    openai_tools = mcp_tools_to_openai_format(mcp_tools)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant with access to USPTO Open Data Portal (ODP) "
                "patent search tools. Use tools to help answer patent-related questions. "
                "The tools support natural language queries for finding patents, applications, "
                "and related metadata."
            ),
        },
        {"role": "user", "content": user_query},
    ]

    print(f'Sending query to {model}: "{user_query}"\n')

    # Multi-turn loop: keep executing tools until LLM returns final answer
    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
        )

        assistant_message = response.choices[0].message
        tool_calls = assistant_message.tool_calls

        # No tool call — final answer
        if not tool_calls:
            print("=" * 70)
            print(f"{model}'s FINAL ANSWER:")
            print("=" * 70)
            print(assistant_message.content)
            return

        # Add assistant's response to messages
        messages.append({
            "role": "assistant",
            "content": assistant_message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        # Execute all tool calls
        tool_results = []
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            raw_args_string = tool_call.function.arguments
            tool_args = json.loads(raw_args_string)

            print("=" * 70)
            print(f"{model} requested: {tool_name}")
            print("=" * 70)
            print(f"  Arguments: {json.dumps(tool_args, indent=2)}\n")

            print(f"Executing '{tool_name}' on the ODP MCP server...\n")
            tool_result = await session.call_tool(tool_name, tool_args)

            raw_result_text = ""
            for block in tool_result.content:
                if getattr(block, "text", None) is not None:
                    raw_result_text += block.text
                else:
                    raw_result_text += str(block)

            print("Result received (truncated):")
            print(raw_result_text[:1000] + ("..." if len(raw_result_text) > 1000 else ""))
            print()

            tool_results.append({
                "tool_call_id": tool_call.id,
                "content": raw_result_text,
            })

        # Add tool results to messages
        for result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": result["tool_call_id"],
                "content": result["content"],
            })


# =============================================================================
# ANTHROPIC BACKEND
# =============================================================================

async def call_anthropic_backend(client, model: str, mcp_tools, user_query: str, session):
    """Execute multi-turn tool calls using Anthropic backend."""
    anthropic_tools = mcp_tools_to_anthropic_format(mcp_tools)

    messages = [
        {
            "role": "user",
            "content": (
                "You are a helpful assistant with access to USPTO Open Data Portal (ODP) "
                "patent search tools. Use tools to help answer patent-related questions. "
                "The tools support natural language queries for finding patents, applications, "
                "and related metadata.\n\n"
                f"User query: {user_query}"
            ),
        },
    ]

    print(f'Sending query to {model}: "{user_query}"\n')

    # Multi-turn loop: keep executing tools until Claude returns final answer
    while True:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            tools=anthropic_tools,
            messages=messages,
        )

        # Look for tool_use blocks in the response
        tool_use_blocks = []
        text_blocks = []
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_use_blocks.append(block)
            elif hasattr(block, "type") and block.type == "text":
                text_blocks.append(block)

        # No tool calls — final answer
        if not tool_use_blocks:
            print("=" * 70)
            print(f"{model}'s FINAL ANSWER:")
            print("=" * 70)
            for block in response.content:
                if hasattr(block, "text"):
                    print(block.text)
            return

        # Add assistant's response to messages
        messages.append({
            "role": "assistant",
            "content": response.content,
        })

        # Execute all tool calls
        tool_results = []
        for tool_use_block in tool_use_blocks:
            tool_name = tool_use_block.name
            tool_args = tool_use_block.input
            tool_use_id = tool_use_block.id

            print("=" * 70)
            print(f"{model} requested: {tool_name}")
            print("=" * 70)
            print(f"  Arguments: {json.dumps(tool_args, indent=2)}\n")

            print(f"Executing '{tool_name}' on the ODP MCP server...\n")
            tool_result = await session.call_tool(tool_name, tool_args)

            raw_result_text = ""
            for block in tool_result.content:
                if getattr(block, "text", None) is not None:
                    raw_result_text += block.text
                else:
                    raw_result_text += str(block)

            print("Result received (truncated):")
            print(raw_result_text[:1000] + ("..." if len(raw_result_text) > 1000 else ""))
            print()

            tool_results.append({
                "tool_use_id": tool_use_id,
                "content": raw_result_text,
            })

        # Add tool results to messages
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": result["tool_use_id"],
                    "content": result["content"],
                }
                for result in tool_results
            ],
        })


# =============================================================================
# MAIN
# =============================================================================

async def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Query the ODP MCP server using an LLM backend."
    )
    parser.add_argument(
        "query",
        help="Natural-language query to send to the LLM",
    )
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help=f"LLM model to use (default: deepseek-chat). Options: {', '.join(MODEL_FAMILIES.keys())}",
    )
    args = parser.parse_args()

    user_query = args.query
    model = args.model

    # Load .env
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=str(env_path))

    # Print startup info
    provider = get_provider(model)
    print(f"[INFO] Using {provider} backend with model: {model}\n")

    # Create LLM client
    client = get_llm_client(model)

    # Launch MCP server and run the single-step flow
    server_params = StdioServerParameters(
        command=MCP_SERVER_PROGRAM,
        args=MCP_SERVER_ARGS,
    )

    print(f"Launching ODP MCP server: {MCP_SERVER_PROGRAM} {' '.join(MCP_SERVER_ARGS)}\n")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Get available tools
            tools_response = await session.list_tools()
            mcp_tools = tools_response.tools
            print(f"ODP MCP server exposes {len(mcp_tools)} tool(s):")
            for t in mcp_tools:
                print(f"  - {t.name}")
            print()

            # Call appropriate backend
            if provider == "openai":
                await call_openai_backend(client, model, mcp_tools, user_query, session)
            elif provider == "anthropic":
                await call_anthropic_backend(client, model, mcp_tools, user_query, session)


if __name__ == "__main__":
    asyncio.run(main())
