"""
Frame Atlas — CLIP fingerprint generator (runs on Ryan's Mac, not the server).

What it does, in plain English:
  1. Downloads every image thumbnail from the live Frame Atlas site.
  2. Runs each one through the CLIP model (ViT-L/14) to get a 768-number
     "fingerprint" describing its visual vibe.
  3. Saves all fingerprints into backend/embeddings_seed.json.gz.
     When that file is committed and pushed, the server loads it into the
     database on boot, and "Find Similar" starts working.

It is incremental: images that already have a fingerprint in the seed file
are skipped, so re-running after new uploads only processes the new ones.
If there is nothing new, it exits in seconds without downloading anything.

The 1.7GB model is downloaded into scripts/.model_cache only when needed,
and deleted again when the run finishes (pass --keep-model to keep it,
which makes the next run start faster at the cost of 1.7GB of disk).

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/generate_embeddings.py
"""

import base64
import gzip
import io
import json
import os
import shutil
import sys

import requests
import torch
import open_clip
from PIL import Image

SITE = "https://frame-atlas-production.up.railway.app"
MODEL_NAME = "ViT-L-14-quickgelu"  # exact config of the original OpenAI weights
PRETRAINED = "openai"
SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", "embeddings_seed.json.gz")
MODEL_CACHE = os.path.join(os.path.dirname(__file__), ".model_cache")
BATCH_SIZE = 16


def load_existing_seed():
    if not os.path.exists(SEED_PATH):
        return {}
    with gzip.open(SEED_PATH, "rt") as f:
        data = json.load(f)
    if data.get("model") != f"{MODEL_NAME}/{PRETRAINED}":
        print(f"Seed file was built with a different model ({data.get('model')}) — starting fresh.")
        return {}
    return data.get("vectors", {})


def fetch_images():
    print(f"Fetching image list from {SITE} ...")
    resp = requests.get(f"{SITE}/api/images", timeout=120)
    resp.raise_for_status()
    images = resp.json()["images"]
    print(f"  {len(images)} images in the library.")
    return images


def decode_thumbnail(data_uri):
    b64 = data_uri.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def main():
    vectors = load_existing_seed()
    images = fetch_images()
    todo = [img for img in images if str(img["id"]) not in vectors]
    print(f"  {len(vectors)} already fingerprinted, {len(todo)} to do.")
    if not todo:
        print("Nothing to do — seed file is up to date.")
        return

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading CLIP {MODEL_NAME} ({PRETRAINED}) on {device} — downloads ~1.7GB if not cached ...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, cache_dir=MODEL_CACHE
    )
    model = model.to(device).eval()

    done = 0
    failed = []
    with torch.no_grad():
        for start in range(0, len(todo), BATCH_SIZE):
            batch = todo[start:start + BATCH_SIZE]
            tensors, ids = [], []
            for img in batch:
                try:
                    tensors.append(preprocess(decode_thumbnail(img["thumbnail"])))
                    ids.append(img["id"])
                except Exception as e:
                    failed.append((img["id"], img.get("filename", "?"), str(e)))
            if not tensors:
                continue
            feats = model.encode_image(torch.stack(tensors).to(device))
            feats = feats / feats.norm(dim=-1, keepdim=True)  # normalize to length 1
            for img_id, vec in zip(ids, feats.cpu().tolist()):
                vectors[str(img_id)] = [round(v, 5) for v in vec]
            done += len(ids)
            print(f"  fingerprinted {done}/{len(todo)}")

    seed = {"model": f"{MODEL_NAME}/{PRETRAINED}", "dim": 768, "vectors": vectors}
    with gzip.open(SEED_PATH, "wt") as f:
        json.dump(seed, f)
    size_kb = os.path.getsize(SEED_PATH) // 1024
    print(f"\nWrote {len(vectors)} fingerprints to {os.path.relpath(SEED_PATH)} ({size_kb} KB)")
    if failed:
        print(f"{len(failed)} images failed:")
        for img_id, name, err in failed:
            print(f"  id={img_id} {name}: {err}")

    if "--keep-model" not in sys.argv and os.path.isdir(MODEL_CACHE):
        shutil.rmtree(MODEL_CACHE)
        print("Deleted the downloaded model (1.7GB freed). It will re-download next time it's needed.")

    print("Next step: commit + push the seed file so the server picks it up.")


if __name__ == "__main__":
    sys.exit(main())
