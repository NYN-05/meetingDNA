<p align="center">
  <img src="https://img.shields.io/badge/MeetingDNA-Decision%20Intelligence-0f766e?style=for-the-badge" alt="MeetingDNA" />
</p>

<h1 align="center">MeetingDNA</h1>

<p align="center">
  Turn meetings into structured memory, searchable decisions, and graph-aware answers.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white&style=flat-square" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black&style=flat-square" alt="React" />
  <img src="https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white&style=flat-square" alt="Vite" />
  <img src="https://img.shields.io/badge/Neo4j-4581C3?logo=neo4j&logoColor=white&style=flat-square" alt="Neo4j" />
  <img src="https://img.shields.io/badge/ChromaDB-1F2937?style=flat-square" alt="ChromaDB" />
  <img src="https://img.shields.io/badge/Whisper-111827?style=flat-square" alt="Whisper" />
  <img src="https://img.shields.io/badge/Ollama-000000?style=flat-square" alt="Ollama" />
</p>

## What It Does

MeetingDNA ingests audio files, transcript files, and pasted transcript text. It extracts meeting summaries, participants, topics, organizations, decisions, and action items, then stores that information for retrieval later.

The system supports two main ways to explore the stored knowledge:

- Semantic search over meeting and decision embeddings
- Graph-based navigation of meetings, people, topics, organizations, decisions, and action items

## Project Structure

```text
app/
  main.py                 FastAPI application entry point
  api/endpoints/          Ingestion, query, and graph routes
  core/                   Extraction, graph, vector, queue, and retrieval logic
  models/                 Meeting and decision schemas
  utils/                  Runtime configuration

ui/
  src/App.jsx             React interface for ingestion, graph view, and query flow
```

## Core Flow

```text
Upload meeting input
  -> queue background ingestion job
  -> transcribe or normalize text
  -> extract structured meeting data
  -> persist to ChromaDB and graph storage
  -> answer future queries with graph + semantic retrieval
```

## Tech Stack

- FastAPI for the backend API
- Whisper for transcription
- Ollama for structured extraction and answering
- ChromaDB for semantic storage
- Neo4j for graph relationships, with local JSON fallback for development
- React and Vite for the UI

## Local Setup

1. Install Python dependencies from `requirements.txt`.
2. Copy `.env.example` to `.env` and set the runtime values.
3. Start the backend.
4. Start the frontend from `ui/`.

## Environment

The application expects configuration for:

- Neo4j connection settings
- Ollama base URL and model name
- ChromaDB storage path

## Purpose

This project is built to transform raw meeting content into reusable organizational memory. It helps preserve decisions, follow action items, and answer questions using stored evidence instead of isolated transcripts.
