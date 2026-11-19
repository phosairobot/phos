#!/usr/bin/env python3
"""Standalone timing harness for the shared PHOS NanoDet detector."""
from __future__ import annotations
import argparse, hashlib, json, resource, statistics, sys, time
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from robot.vision.nanodet import (NanoDetDetector, NanoDetError, NanoDetInferenceError,
    NanoDetDetection, STRIDES, decode_outputs, non_max_suppression, output_diagnostics)
# Compatibility aliases for the benchmark's focused tests; implementations live in robot.vision.nanodet.
Detection=NanoDetDetection
UnsupportedModelError=NanoDetInferenceError
decode_nanodet_outputs=lambda outputs, confidence_threshold, nms_threshold, max_detections, np_module, output_names=None: decode_outputs(outputs,names=output_names or (),confidence=confidence_threshold,nms=nms_threshold,limit=max_detections,np=np_module)
describe_nanodet_outputs=lambda names,outputs,np_module: output_diagnostics(names,outputs,np_module)

def parse_args(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--model",required=True,type=Path); p.add_argument("--image",required=True,type=Path); p.add_argument("--warmup",type=int,default=3); p.add_argument("--runs",type=int,default=10); p.add_argument("--confidence",type=float,default=.35); p.add_argument("--nms",type=float,default=.6); p.add_argument("--max-detections",type=int,default=5); p.add_argument("--expected-sha256"); p.add_argument("--diagnose-outputs",action="store_true"); a=p.parse_args(argv)
    if a.warmup<0 or a.runs<1 or not 0<=a.confidence<=1 or not 0<=a.nms<=1 or a.max_detections<1: p.error("invalid benchmark limits")
    return a
def sha256_file(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest()
def rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=="darwin" else 1024)
def run_benchmark(a):
    if not a.image.is_file(): raise NanoDetError(f"image file does not exist: {a.image}")
    digest=sha256_file(a.model)
    if a.expected_sha256 and digest.lower()!=a.expected_sha256.lower(): raise NanoDetError("model SHA-256 does not match --expected-sha256")
    started=time.perf_counter(); d=NanoDetDetector(a.model,confidence=a.confidence,nms=a.nms,max_detections=a.max_detections).load(); load_ms=(time.perf_counter()-started)*1000
    cv2=d._cv2; image=cv2.imread(str(a.image),cv2.IMREAD_COLOR)
    if image is None: raise NanoDetError(f"cannot decode input image: {a.image}")
    if a.diagnose_outputs:
        detections=d.detect(image); return {"model":{"sha256":digest,"load_ms":round(load_ms,3)},"runtime":{"opencv":cv2.__version__},"output_diagnostics":output_diagnostics(d._names,d._net.forward(d._names),d._np),"detections":[asdict(x) for x in detections]}
    for _ in range(a.warmup): d.detect(image)
    times=[]; detections=[]
    for _ in range(a.runs):
        t=time.perf_counter(); detections=d.detect(image); times.append((time.perf_counter()-t)*1000)
    ordered=sorted(times); p95=ordered[max(0,round(.95*len(ordered))-1)]
    return {"model":{"path":str(a.model),"sha256":digest,"load_ms":round(load_ms,3)},"runtime":{"opencv":cv2.__version__},"measurements":{"warmup_runs":a.warmup,"measured_runs":a.runs,"end_to_end_ms":[round(x,3) for x in times],"end_to_end_p50_ms":round(statistics.median(times),3),"end_to_end_p95_ms":round(p95,3)},"memory":{"peak_rss_bytes":rss()},"detections":[asdict(x) for x in detections]}
def main(argv=None):
    try: print(json.dumps(run_benchmark(parse_args(argv)),indent=2,sort_keys=True))
    except (NanoDetError,OSError) as e: print(f"benchmark failed: {e}",file=sys.stderr); return 2
    return 0
if __name__=="__main__": raise SystemExit(main())
