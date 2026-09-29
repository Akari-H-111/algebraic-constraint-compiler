# Show Your Work MCP server (Streamable HTTP, MCP 2025-11-25) for any container host.
# The notebook lives in /tmp: it is per-instance demo state, not durable storage.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY algebraic_compiler ./algebraic_compiler
RUN pip install --no-cache-dir '.[mcp]'
ENV HOST=0.0.0.0 \
    PORT=8000 \
    SYW_NOTEBOOK_PATH=/tmp/show-your-work/notebook.sqlite3
EXPOSE 8000
USER nobody
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/healthz')"
CMD ["python", "-m", "algebraic_compiler.mcp_server", "--cors"]
