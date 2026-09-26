# ChatGPT Plugin / MCP setup

The Render deployment now exposes a Streamable HTTP MCP endpoint:

```
https://testprep-school-search-api.onrender.com/mcp
```

Health check:

```
https://testprep-school-search-api.onrender.com/health
```

## Tools

- `start_school_search(query, city, depth=5, no_email=false)`
- `get_school_search_job(job_id)`
- `get_school_search_results(job_id, limit=100)`

The first tool starts the Maps + leadership enrichment job. Poll the second until completion, then call the third. Completed jobs include private random-token CSV/XLSX download URLs.

## Connect in ChatGPT

1. Open **Settings → Security and login**.
2. Turn on **Developer mode**.
3. Open **ChatGPT Plugins** and select **+**.
4. Create a developer-mode MCP connection using:
   `https://testprep-school-search-api.onrender.com/mcp`
5. Review the three discovered tools and create/install the plugin.
6. Start a new chat (or Work chat where required), select the plugin, and ask:
   “Find CBSE schools in Lucknow, Uttar Pradesh. Use depth 3. Return phone, email, website, leadership details, social links and the Excel download.”

This MCP server currently exposes only public school-research functions and uses conservative job limits. Do not expose private data through it without adding standards-compliant OAuth authorization.
