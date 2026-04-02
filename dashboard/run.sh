#!/bin/bash
cd "$(dirname "$0")/.."
source .env 2>/dev/null
./venv/bin/streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0
