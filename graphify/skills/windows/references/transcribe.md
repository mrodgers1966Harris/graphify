# graphify reference: transcribe video and audio

Load this only when `detect` reported one or more `video` files. A corpus with no video never reads this.

### Step 2.5 - Transcribe video / audio files (only if video files detected)

Skip this step entirely if `detect` returned zero `video` files.

Video and audio files cannot be read directly. Transcribe them to text first, then treat the transcripts as doc files in Step 3.

**Strategy:** Read the god nodes from `graphify-out/.graphify_detect.json` (or the analysis file if it exists from a previous run). You are already a language model — write a one-sentence domain hint yourself from those labels. Then pass it to Whisper as the initial prompt. No separate API call needed.

**However**, if the corpus has *only* video files and no other docs/code, use the generic fallback prompt: `"Use proper punctuation and paragraph breaks."`

**Step 1 - Write the Whisper prompt yourself.**

Read the top god node labels from detect output or analysis, then compose a short domain hint sentence, for example:

- Labels: `transformer, attention, encoder, decoder` → `"Machine learning research on transformer architectures and attention mechanisms. Use proper punctuation and paragraph breaks."`
- Labels: `kubernetes, deployment, pod, helm` → `"DevOps discussion about Kubernetes deployments and Helm charts. Use proper punctuation and paragraph breaks."`

**Export** it as `GRAPHIFY_WHISPER_PROMPT` (the exact name the transcriber reads — and it must be `export`ed so the child Python process sees it) for the next command.

**Step 2 - Transcribe:**

```bash
GRAPHIFY_PY="$(cat graphify-out/.graphify_python)"
case "$GRAPHIFY_PY" in //*|/proc/*|/dev/*|*~*|*[/\\].[/\\]*|*[/\\]..[/\\]*|*[/\\].|*[/\\]..) GRAPHIFY_PY= ;; /*|[A-Za-z]:[/\\]*) ;; *) GRAPHIFY_PY= ;; esac
_gdir="$(CDPATH= cd -P "${GRAPHIFY_PY%[/\\]*}/" 2>/dev/null && pwd -P)"
_gchk="$(printf '|%s/' "$GRAPHIFY_PY" "$_gdir" "$(realpath "$GRAPHIFY_PY" 2>/dev/null)" | tr 'A-Z\\' 'a-z/')"
for _gd in "$PWD" "$(pwd -P)" "$(git rev-parse --show-toplevel 2>/dev/null)"; do _gd="$(printf '%s' "$_gd" | tr 'A-Z\\' 'a-z/')"; [ -n "$_gd" ] && case "$_gchk" in *"|$_gd"/*) GRAPHIFY_PY= ;; esac; done
[ -n "$_gdir" ] && [ -f "$GRAPHIFY_PY" ] && [ -x "$GRAPHIFY_PY" ] || { GRAPHIFY_PY=; echo "graphify: refusing graphify-out/.graphify_python - it must name an absolute Python outside this project; rerun the interpreter guard" >&2; }
export GRAPHIFY_WHISPER_MODEL=base  # or whatever --whisper-model the user passed (must be exported)
export GRAPHIFY_WHISPER_PROMPT="<the one-sentence domain hint you composed in Step 1>"
"${GRAPHIFY_PY:?}" -c "
import json, os, sys
from pathlib import Path
from graphify.transcribe import transcribe_all

detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding=\"utf-8\"))
video_files = detect.get('files', {}).get('video', [])
prompt = os.environ.get('GRAPHIFY_WHISPER_PROMPT', 'Use proper punctuation and paragraph breaks.')

transcript_paths = transcribe_all(video_files, initial_prompt=prompt)
# Write the JSON from Python (NOT a shell '>' redirect): transcribe_all/Whisper
# print progress to stdout, which would otherwise corrupt the JSON file (#1392).
Path('graphify-out/.graphify_transcripts.json').write_text(json.dumps(transcript_paths, ensure_ascii=False), encoding=\"utf-8\")
print(f'Transcribed {len(transcript_paths)} file(s)', file=sys.stderr)
"
```

After transcription:
- Read the transcript paths from `graphify-out/.graphify_transcripts.json`
- Add them to the docs list before dispatching semantic subagents in Step 3B
- Print how many transcripts were created: `Transcribed N video file(s) -> treating as docs`
- If transcription fails for a file, print a warning and continue with the rest

**Whisper model:** Default is `base`. If the user passed `--whisper-model <name>`, `export GRAPHIFY_WHISPER_MODEL=<name>` (it must be exported, not just assigned) before running the command above.
