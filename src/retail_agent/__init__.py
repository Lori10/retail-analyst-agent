def main() -> None:
    """Entry point for `retail-agent`; see `retail_agent.cli.main`.

    Imports `cli` on call, not at package import: every `retail_agent.*`
    import runs this file first, and `cli` pulls in LangChain/LangGraph —
    seconds of import time the MCP server (`retail-mcp`) never needs and
    can't afford before its stdio handshake.
    """
    from retail_agent.cli import main as cli_main

    cli_main()


__all__ = ["main"]
