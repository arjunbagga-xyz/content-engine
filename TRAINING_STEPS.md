# RVC Voice Training — Runbook

Datasets are prepped and isolated (Demucs vocals, 16k mono). Total clean audio:
- goku: 10m53s  (data/voice_dataset_goku.zip)
- vegeta: 12m7s  (data/voice_dataset_vegeta.zip)
- peter: 8m29s   (data/voice_dataset_peter.zip)
- stewie: 7m26s  (data/voice_dataset_stewie.zip)

All well above the 1-3 min RVC minimum. Gohan/Meg skipped (stay on Edge-TTS).

## Train (one character per Colab session)

1. Open `scratch/rvc_train_colab.ipynb` in Google Colab.
   - Set runtime: **GPU** (Runtime → Change runtime type → GPU).
2. In the first code cell, set `CHARACTER = 'goku'` (or vegeta/peter/stewie).
3. Run cells top-to-bottom:
   - Install RVC (clones Mangio-RVC fork, pip installs).
   - **Upload** the matching zip (`data/voice_dataset_goku.zip`) when `files.upload()` prompts.
     - The notebook extracts it to `/content/dataset_raw/goku/`.
   - Preprocess (resample + f0 + feature extract).
   - Train (EPOCHS=200 default; 100-300 is fine).
   - (Optional) build feature index.
   - Download `<character>.pth` (+ `<character>.index` if index built).
4. Put the downloaded file(s) into:
   `data/voice_models/<character>/<character>.pth`
   (and `<character>.index` alongside if generated)

## Activate

The pipeline auto-detects the model — no code change. Next time you generate a
Goku/Vegeta/Peter/Stewie reel, `voice_provider.generate_voice()` converts the
Edge-TTS narration to that character's RVC voice (`method='rvc'` in logs).
Until a model exists, it falls back to the Edge-TTS narrator.

## Verify after training

Generate one reel per character and check the log line:
`voice method=rvc for <character>`  (not `edge_tts`).

## Local inference (after training, no GPU needed)

`voice_provider._rvc_convert()` calls the RVC `infer_cli.py`. Point it at your
RVC install via env `RVC_ROOT=/path/to/Retrieval-based-Voice-Conversion-WebUI`.
CPU inference is fast enough per reel.

## Notes
- Demucs isolation already stripped BGM; these clips were near-pure speech.
- More source audio = better timbre match; you can re-train later by adding
  clips to `data/voice_source/<char>/` and re-running `prepare_voice_data.py`.
