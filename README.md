# Notebook+ Annotator

> **Where NotebookLM speaks, NotebookAnnotator points.**  
> Built for the **Google Cloud AI Builder Cup 2026** · Track: *Media, Content & Digital Experiences*

Notebook+ Annotator synchronizes dense audio explanations (such as NotebookLM Audio Overviews or recorded engineering lectures) directly to the original lecture PDF. Instead of generating black-box video frames from scratch (which hallucinate formulas and produce multi-gigabyte files), Notebook+ Annotator keeps the PDF immutable and overlays millisecond-accurate bounding boxes and word-timed mathematical sub-term focus.

---

## 🌟 Key Capabilities

1. **Stationary PDF Grounding**: The original vector PDF remains the crisp background. As the audio explanation plays, the viewer automatically advances to the correct slide and highlights the exact sentence or paragraph being spoken.
2. **Word-Timed Equation Focus**: High-precision sub-term tracking. As the speaker explains complex formulas (e.g., in backpropagation or Stokes' theorem), a focused bounding box moves from term to term (e.g., highlighting $\frac{\partial E}{\partial w}$ or $\nabla \times \mathbf{F}$) synchronized to the spoken audio.
3. **Google Gemini 3 Flash Disambiguation**: When multiple slides share similar headings or vocabulary, Google Gemini 3 Flash evaluates transcript context against candidate slide snippets to make the correct navigation decision.
4. **Deterministic Guardrails & Abstention**: If Gemini proposes a bounding box outside the verified document geometry, our guardrail rejects the hallucination. On out-of-scope or conversational tangents, the system abstains from drawing false marks, holds the slide, and routes the point to human review.
5. **Background Processing Studio**: Immediate, non-blocking uploads with live streaming progress cards in the *Research Notebooks* sidebar.
6. **Ultra-Compact Export**: Renders the complete event timeline into an optimized, lightweight 720p MP4 ($\sim 15\text{ MB}$ for a 19-minute lecture—over 100× smaller than generative video).

---

## 🏗️ System Architecture

```text
Lecture PDF ───> [pdfplumber / RapidOCR] ───> Positioned Text Blocks & LaTeX Formulas ──┐
                                                                                         ├──> [FastEmbed ONNX] Whole-Lecture Alignment
Lecture Audio ─> [Whisper Word Timestamps] ─> Spoken Points & Equation Cues ────────────┘              │
                                                                                                       ▼
                                                                                   Borderline Candidates (0.50 ≤ conf < 0.82)
                                                                                                       │
                                                                                                       ▼
                                                                                        [Google Gemini 3 Flash]
                                                                                        Semantic Disambiguation
                                                                                                       │
                                                                                                       ▼
                                                                                       [Deterministic Guardrails]
                                                                                       • Block hallucinated IDs
                                                                                       • Enforce containment
                                                                                       • Hold page on low evidence
                                                                                                       │
                                                                                                       ▼
                                                                                 [Notebook+ Annotator Reactive Studio]
                                                                                 • Synchronized Web Player
                                                                                 • Sub-term Equation Focus
                                                                                 • Compact MP4 / JSON Export
```

---

## 🚀 Evaluator Quick-Start Guide

### 1. Launch the Live Application

Run the local launcher (or access the deployed Cloud Run instance):

```powershell
# From PowerShell in the project root:
.\run.ps1
```
The server will start on `http://127.0.0.1:8766/` (or the first free port in `8766–8785`).

### 2. Evaluator Golden Path (1-Click Bookmarks)

Open the flagship notebook: **L05 - Optimization for Neural Networks** (29 pages, 19:02 audio). In the workspace header, click any of the **Demo Highlights** chips to immediately inspect key behaviors:

- **`03:12 PDF Grounding Match`**: Instant slide transition to Page 3 with active paragraph highlight.
- **`04:14 Neural Net Architecture`**: Precise bounding box around the 3-layer neural network diagram.
- **`07:38 Squared Loss Formula`**: Formula-level visual box on the mean squared error loss function.
- **`18:07 Sub-Term: ∂E/∂w`**: Granular equation focus tracking the partial derivative term as it is spoken.
- **`08:41 Uncertain Review`**: Demonstrates the abstention guardrail—no false mark is drawn; the point is routed to the human review drawer.

---

## 🧪 Automated Verification & Benchmarks

The project includes an automated test suite verifying grounding accuracy, sub-term tracking, and Gemini safety:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

### Benchmark Results
- **Unit Tests**: $22 / 22$ passed ($100\%$).
- **Page Landmark Accuracy**: $60 / 64$ hand-labeled landmarks verified across real multi-page lectures ($93.8\%$).
- **Equation Cue Precision**: $100\%$ precision on labeled mathematical sub-term cues.
- **Export Efficiency**: 19:02 lecture exported as a $15.9\text{ MB}$ H.264/AAC MP4 in under 4 minutes.

---

## ☁️ Google Cloud Run Deployment

Notebook+ Annotator is containerized and optimized for **Google Cloud Run**:

```bash
# Deploy directly to Cloud Run
gcloud run deploy notebook-annotator \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --set-env-vars GEMINI_API_KEY="your-gemini-api-key"
```

The container automatically listens on `$PORT`, includes pre-bundled sample datasets, and scales to zero when idle for maximum cost efficiency.

---

## 🔒 Code Hygiene & Secrets

This repository strictly complies with AI Builder Cup submission guidelines:
- **Zero Exposed Secrets**: All API keys are loaded via environment variables (`GEMINI_API_KEY`) or Google Secret Manager.
- **Strict `.gitignore`**: Local virtual environments, temporary caches, and `.env` files are excluded from source control.
