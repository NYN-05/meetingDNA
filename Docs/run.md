# Run MeetingDNA

This project has two parts:

- FastAPI backend at `app/main.py`
- React + Vite UI at `ui/`

## Prerequisites

- Python 3.10 or newer
- Ollama installed and running locally
- The `gemma4:31b-cloud` model pulled into Ollama
- FFmpeg installed and available on `PATH` for Whisper audio transcription
- Node.js 18 or newer
- npm
- Neo4j is optional; set `NEO4J_ENABLED=true` only if you want to sync the graph store to Neo4j

## 1. Create and activate a virtual environment

From the project root, run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, run this once in an elevated or current session and then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 2. Install dependencies

```powershell
pip install -r requirements.txt
```

## 3. Configure environment variables

Create a `.env` file in the project root with the values below:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
CHROMA_DB_PATH=./data/chromadb
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:31b-cloud
```

Notes:

- `CHROMA_DB_PATH` defaults to `./data/chromadb` if you do not set it.
- `OLLAMA_BASE_URL` defaults to `http://localhost:11434`.
- `OLLAMA_MODEL` defaults to `gemma4:31b-cloud`.

To install the model, run:

```powershell
ollama pull gemma4:31b-cloud
```

If Ollama is not already running, start it before launching the app.

## 4. Start Neo4j

Make sure Neo4j is running and reachable at the URI you configured.

Default local settings used by the app:

- URI: `bolt://localhost:7687`
- Username: `neo4j`
- Password: `password`

If you use Neo4j Desktop or a remote instance, update `.env` accordingly.

## 5. Start the backend API

From the project root:

```powershell
python -m uvicorn app.main:app --reload
```

The API will be available at:

- `http://127.0.0.1:8000`
- Swagger docs: `http://127.0.0.1:8000/docs`

## 6. Install the frontend dependencies

In a second terminal, install the React app dependencies:

```powershell
cd ui
npm install
```

## 7. Start the React UI

From the `ui` folder, run:

```powershell
npm run dev
```

The UI will be available at:

- `http://localhost:5173`

## 8. Use the app

In the UI, you can:

- Upload an audio file for transcription
- Upload a transcript file
- Paste transcript text directly
- Query the decision graph and transcript store
- Reopen saved uploads later from persistent ChromaDB storage

## Common issues

- If audio transcription fails, confirm that FFmpeg is installed and on `PATH`.
- If you enable Neo4j sync, confirm Neo4j is running and the credentials in `.env` are correct.
- If the UI cannot reach the backend, make sure the FastAPI server is still running on port `8000` and the browser can reach `http://localhost:8000`.
- The React dev server proxies `/api` to the backend, so browser requests should stay same-origin during local development.
