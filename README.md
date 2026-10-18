# Audio to PDF

An audio guided PDF player for engineering lectures. It renders each original PDF page as a stationary background, places timed annotation events above it, and inserts persistent whiteboard pages for spoken analogies. Lessons can also be exported as compact 720p MP4 files.

## Run locally

This project already has a prepared `.venv` and a sample lesson on this computer. **Double-click `start.cmd`** in this folder and leave its console window open while using the app. Open the address printed after `OPEN THIS ADDRESS` in that window. The launcher chooses a free port between 8766 and 8785.

Alternatively, from PowerShell in this folder:

```powershell
.\run.ps1
```

If you recreate the environment, run `.\setup.ps1` first. Python 3.11+ and an internet connection are needed for a new setup. The first transcription of an uploaded lecture downloads the local Whisper model into `data/models/`.

## Try the sample

Open **L05 - Optimization for Neural Networks** in the lesson list. The sample uses your 29 page PDF, 19 minute audio, and saved transcript. Play or seek to an explanation. The page changes directly at grounded points; there is no automatic PDF scroll or zoom. Prior marks remain on a page and reappear when playback returns to it. The timed point list shows the phrase, topic, action, and review status.

To correct a point: select it in the timeline, choose its source page and action, draw a target on the page for a visual action, then save. Corrections are stored in `data/lessons/<lesson id>/lesson.json`. The export controls download the editable event plan as JSON or render a compact MP4. MP4 exports are cached until the lesson changes.

**Word-timed equations:** Select **Build equation focus** to map a lesson locally after checking its PDF grounding. While a formula is explained, one thin orange box can move between grounded terms at the audio's word timestamps. Select its timeline point to edit a cue's start/end, term, or box. Rebuilding preserves saved cue corrections. Unmatched equations retain a whole-equation box for review. Equations use PDF character positions and local speech rules; this step makes no Gemini calls. Searchable PDFs work best; scanned or unusually encoded formulas need manual review. New lessons currently start with no equation-focus events while source grounding is prioritized.

To enable Gemini analogy interpretation and illustrations, set `GEMINI_API_KEY` before starting the server. `GEMINI_MODEL` is optional and defaults to `gemini-2.5-flash`; `GEMINI_IMAGE_MODEL` defaults to `gemini-3.1-flash-image`. The key stays on the server. Analogy planning and image generation share a persisted maximum of eight Gemini requests per lesson, with cached plans and images. Requests are serialized; a quota error stops further Gemini requests for that lesson and local templates continue. The app cap cannot guarantee the provider will never return a quota error. Without a key, the app uses local templates for common hill, flow, input/output, network, and momentum analogies.

To rebuild and check the bundled sample after changing the matcher, run `.\.venv\Scripts\python.exe scripts\seed_sample.py` and `.\.venv\Scripts\python.exe scripts\check_sample.py`. Rebuilding replaces corrections made to the bundled sample.

## Process your own lecture

Select **New lesson** and upload a PDF plus matching narration. An optional timestamped transcript JSON can avoid retranscription. The app renders pages, extracts positioned words and document blocks, uses local OCR when embedded text is insufficient, transcribes audio with word timestamps, groups narration into short points, and aligns each point across the full PDF with lexical and local semantic evidence. It underlines verified text or boxes a verified equation or figure. Ambiguous matches remain reviewable without a new mark. New lessons currently pause automatic analogy-page creation. Upload limit: 120 MB per file.

## Architecture

```text
PDF -> page images + positioned blocks + OCR if needed ─┐
                                                        ├─> whole-lecture page alignment
Audio -> word-timed transcript -> sentence/topic cues ──┘             │
                                                                      └─> block evidence + confidence
                                                                                │
                                                                                ├─> annotation or review
                                                                                └─> page + overlay player + MP4
```

The audio analysis output describes what is being discussed and when. The grounding stage chooses a page, positioned block, region, and annotation action, or abstains if evidence is weak. The local semantic model makes a one-time download into `data/models`. Core alignment makes no Gemini calls. Existing lessons retain their whiteboards, which are virtual pages inserted between original PDF pages. The analogy module remains available for later use: Gemini can plan a storyboard, local word timestamps control drawing cues, simple analogies use validated strokes, and complex analogies can use one cached illustration. The player and MP4 renderer execute the same saved event plan.

Use **Rebuild PDF grounding** to re-align an older lesson with the current local pipeline. The rebuild preserves existing whiteboards and manual corrections, remaps source blocks where their text still matches, and sends stale references to review. A backup of each supplied sample's lesson JSON was saved before its October 17 rebuild.

Select an analogy in the timeline or page list to edit its title, drawing steps, labels, and stage times. Approve it after checking the explanation. Generated illustrations remain marked for review until approved. Possible analogies that cannot be interpreted confidently create no drawing and appear in the review list.

Run the labeled local check with `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_equation_focus.py -v`. It checks 21 hand-labeled cues in the supplied transcript, box placement, export invalidation, and the Gemini request cap without making Gemini requests.

On the bundled recording, the local mapper covered all 21 labeled cues with the correct term and a 0.0 second median difference from the saved word onset. This is a sample-specific check, not an accuracy claim for other lectures. The complete 19:02.88 sample exported as a 15.9 MB H.264/AAC MP4 in about four minutes on this computer.

## Current limits

- Gemini analogy planning is optional, and automatic analogy creation is paused for new lessons while PDF grounding is improved. Existing analogy pages remain available.
- MP4 export renders the saved event timeline rather than generating backgrounds. A long lecture can take several minutes to encode on a CPU.
- Some narration has no exact PDF match. The player can hold the current page rather than invent a mark. A human should review the marked points before sharing a lesson as accurate.
- The sample transcript is supplied for speed. Fresh uploads use local faster-whisper, which may take time on a CPU.
- In the current two-lecture labeled check, page selection is correct at 60 of 64 landmarks (93.75%). Two landmarks fall in transcript gaps, and two optimization slide boundaries remain wrong. Automatically marked block selection is correct at 15 of 16 marked passages from a 19-label set; broader evaluation is still needed.

## Project layout

`backend/analysis.py` handles extraction, transcription, and event planning. `backend/app.py` serves uploads, lessons, assets, edits, and exports. `frontend/` contains the player. `samples/` contains the user supplied optimization lecture and transcript. `data/` stores generated lessons and model weights and is ignored by Git.
