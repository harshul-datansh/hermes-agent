# Datansh Mission Control

Datansh Mission Control is a local Streamlit dashboard for the POC. It reads `monitor/events.jsonl`, groups events by run, shows each role's status, previews role output files, and displays the final report.

Start it from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_monitor.ps1
```

Or directly:

```powershell
streamlit run monitor/app.py --server.address 127.0.0.1 --server.port 8501
```

