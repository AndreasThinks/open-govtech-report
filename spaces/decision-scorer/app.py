"""GovTech decision scorer: Kev on a ZeroGPU Space, with a batch endpoint.

Each call takes a JSON list of System One requests ({"state": ..., "questions": {...}}) and
returns a JSON list of responses in the same order, all inside one GPU allocation. That keeps
ZeroGPU queueing to one wait per batch instead of one per repository.

Kev's loader and API modules (kev/api.py, kev/checkpoint.py, kev/model.py) are vendored at a
pinned commit by publish.sh, the same way Kev's own Space does it.
"""

import os

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# isort: off
import spaces  # noqa: E402  (must be imported before torch)

import json  # noqa: E402
import time  # noqa: E402

import gradio as gr  # noqa: E402
import torch  # noqa: E402
from kev.api import SystemOneRequest, to_answers, to_record  # noqa: E402
from kev.checkpoint import Checkpoint, LoadOptions  # noqa: E402
from kev.model import admit  # noqa: E402
# isort: on

MODEL = os.environ.get("KEV_MODEL", "jaredpalmer/kev-4b")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "128"))
SECONDS_PER_REQUEST = float(os.environ.get("SECONDS_PER_REQUEST", "0.25"))

# Load once on CPU, then move to "cuda" at module scope, which is what ZeroGPU expects.
# bf16 is kev.serve's GPU default: probabilities within ~0.01 of fp32 and much faster.
_checkpoint = Checkpoint(MODEL)
TOKENIZER, DECISION_MODEL = _checkpoint.load("cpu", LoadOptions(dtype=torch.bfloat16, attn="sdpa"))
DECISION_MODEL.device = "cuda"
DECISION_MODEL.to("cuda")
print(f"[scorer] {MODEL} loaded, temperature {DECISION_MODEL.head.temperature:.2f}", flush=True)


def _parse(requests_json: str) -> list:
    requests = json.loads(requests_json or "[]")
    if not isinstance(requests, list):
        raise gr.Error("Send a JSON list of System One requests")
    if len(requests) > MAX_BATCH:
        raise gr.Error(f"At most {MAX_BATCH} requests per call")
    return requests


def _duration(requests_json: str) -> int:
    try:
        n = len(_parse(requests_json))
    except Exception:
        n = 1
    return int(min(120, 10 + n * SECONDS_PER_REQUEST))


def _answer_one(raw: dict) -> dict:
    req = SystemOneRequest.model_validate({**raw, "model": raw.get("model", "kev-latest")})
    record, meta = to_record(req)
    encoded = admit(DECISION_MODEL, TOKENIZER, record)
    probs = DECISION_MODEL.probs(encoded)
    return {"model": MODEL, "answers": to_answers([p.tolist() for p in probs], meta)}


@spaces.GPU(duration=_duration)
def score_batch(requests_json: str) -> str:
    """Answer a batch of System One requests with Kev.

    Args:
        requests_json: JSON list of {"state": str | object, "questions": {id: question}} requests.

    Returns:
        JSON list with one {"answers": ...} or {"error": ...} per request, in order.
    """
    requests = _parse(requests_json)
    started = time.perf_counter()
    results = []
    with torch.inference_mode():
        for raw in requests:
            try:
                results.append(_answer_one(raw))
            except Exception as e:  # one bad request must not sink the batch
                results.append({"error": f"{type(e).__name__}: {str(e)[:300]}"})
    print(f"[scorer] {len(requests)} requests in {time.perf_counter() - started:.1f}s", flush=True)
    return json.dumps(results)


with gr.Blocks(title="GovTech decision scorer") as demo:
    gr.Markdown(
        "# GovTech decision scorer\n"
        f"Batch scoring for the open-govtech-report tagger, running `{MODEL}` on ZeroGPU. "
        "Send a JSON list of System One requests; get one response per request back."
    )
    requests_box = gr.Code(label="Requests (JSON list)", language="json", value="[]")
    output_box = gr.Code(label="Responses", language="json")
    gr.Button("Score").click(
        score_batch, inputs=requests_box, outputs=output_box, api_name="score_batch"
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch()
