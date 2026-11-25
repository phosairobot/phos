# SPDX-License-Identifier: Apache-2.0
"""Small, on-demand OpenCV DNN NanoDet detector; it owns neither camera nor frames."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

INPUT_SIZE = (416, 416)
STRIDES = (8, 16, 32, 64)
SUPPORTED_LAYOUTS = ((8, 16, 32), (8, 16, 32, 64))
REG_MAX = 7
COCO_LABELS = ("person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush")

class NanoDetError(RuntimeError): pass
class NanoDetModelUnavailable(NanoDetError): pass
class NanoDetInvalidFrame(NanoDetError): pass
class NanoDetInferenceError(NanoDetError): pass

@dataclass(frozen=True)
class NanoDetDetection:
    label: str; confidence: float; x1: float; y1: float; x2: float; y2: float

def _iou(a, b):
    area = max(0., min(a.x2,b.x2)-max(a.x1,b.x1))*max(0., min(a.y2,b.y2)-max(a.y1,b.y1))
    union = (a.x2-a.x1)*(a.y2-a.y1)+(b.x2-b.x1)*(b.y2-b.y1)-area
    return area/union if union else 0.

def non_max_suppression(items, threshold):
    kept=[]
    for item in sorted(items, key=lambda x:x.confidence, reverse=True):
        if all(_iou(item, old)<=threshold for old in kept): kept.append(item)
    return kept

def _anchors(stride, np):
    w,h=INPUT_SIZE[0]//stride,INPUT_SIZE[1]//stride
    x,y=np.meshgrid(np.arange(w),np.arange(h))
    return np.stack((x,y),axis=-1).reshape(-1,2)*stride+.5*(stride-1)

def output_diagnostics(names, outputs, np):
    entries=[]
    for index, value in enumerate(outputs):
        value=np.asarray(value); shape=value.shape[1:] if value.ndim==3 and value.shape[0]==1 else value.shape
        channels=shape[1] if len(shape)==2 else None
        role="classification" if channels==80 else "regression" if channels==32 else "unknown"
        strides=[s for s in STRIDES if len(shape)==2 and shape[0]==(416//s)**2]
        name=names[index] if index<len(names) else f"<unnamed-{index}>"
        match=re.search(r"(?:stride|strides)[_-]?(\d+)",name.lower()); named=int(match.group(1)) if match else None
        entries.append({"name":name,"shape":list(value.shape),"dtype":str(value.dtype),"role":role,"matching_strides":strides,"named_stride":named,"valid_geometry":value.ndim in (2,3) and len(strides)==1 and role!="unknown" and (named is None or named==strides[0])})
    return {"output_layer_names":list(names),"returned_tensor_count":len(outputs),"tensors":entries}

def decode_outputs(outputs, *, names=(), confidence=0.35, nms=0.6, limit=5, np):
    report=output_diagnostics(names or tuple(f"output_{i}" for i in range(len(outputs))),outputs,np); info=report["tensors"]
    if len(info)%2: raise NanoDetInferenceError("NanoDet outputs require classification/regression pairs")
    levels=[]
    for i in range(0,len(info),2):
        a,b=info[i:i+2]
        if a["role"]!="classification" or b["role"]!="regression" or not a["valid_geometry"] or not b["valid_geometry"] or a["matching_strides"]!=b["matching_strides"]: raise NanoDetInferenceError("unsupported NanoDet output pair")
        levels.append(a["matching_strides"][0])
    if tuple(levels) not in SUPPORTED_LAYOUTS: raise NanoDetInferenceError(f"unsupported NanoDet stride layout {tuple(levels)}")
    found=[]
    for i,stride in enumerate(levels):
        scores=np.asarray(outputs[2*i]); boxes=np.asarray(outputs[2*i+1]); scores=scores[0] if scores.ndim==3 else scores; boxes=boxes[0] if boxes.ndim==3 else boxes
        values=boxes.reshape(-1,4,8).astype(np.float32); values-=values.max(axis=2,keepdims=True); values=np.exp(values); distances=(values/values.sum(axis=2,keepdims=True))@np.arange(8,dtype=np.float32); distances*=stride
        anchors=_anchors(stride,np); ids=scores.argmax(axis=1); conf=scores.max(axis=1)
        for row in np.flatnonzero(conf>=confidence):
            x,y=anchors[row]; l,t,r,b=distances[row]; found.append(NanoDetDetection(COCO_LABELS[int(ids[row])],float(conf[row]),float(max(x-l,0)),float(max(y-t,0)),float(min(x+r,416)),float(min(y+b,416))))
    return non_max_suppression(found,nms)[:limit]

class NanoDetDetector:
    def __init__(self, model_path: Path | str, *, confidence=.35, nms=.6, max_detections=5, cv2_module=None, np_module=None):
        self.model_path=Path(model_path); self.confidence=confidence; self.nms=nms; self.max_detections=max_detections; self._cv2=cv2_module; self._np=np_module; self._net=None; self._names=()
    def load(self):
        if not self.model_path.is_file(): raise NanoDetModelUnavailable(f"model file does not exist: {self.model_path}")
        try:
            if self._cv2 is None: import cv2 as imported; self._cv2=imported
            if self._np is None: import numpy as imported_np; self._np=imported_np
            self._net=self._cv2.dnn.readNet(str(self.model_path)); self._net.setPreferableBackend(self._cv2.dnn.DNN_BACKEND_OPENCV); self._net.setPreferableTarget(self._cv2.dnn.DNN_TARGET_CPU); self._names=tuple(self._net.getUnconnectedOutLayersNames()); return self
        except Exception as error: raise NanoDetModelUnavailable(f"OpenCV DNN could not load NanoDet: {error}") from error
    def _prepare_blob(self, frame):
        np,cv2=self._np,self._cv2
        if not hasattr(frame,"shape") or len(frame.shape)!=3 or frame.shape[2]!=3: raise NanoDetInvalidFrame("expected HxWx3 image")
        try:
            image=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB); h,w=image.shape[:2]; scale=min(416/w,416/h); rw,rh=round(w*scale),round(h*scale); image=cv2.resize(image,(rw,rh),interpolation=cv2.INTER_AREA); top,left=(416-rh)//2,(416-rw)//2; image=cv2.copyMakeBorder(image,top,416-rh-top,left,416-rw-left,cv2.BORDER_CONSTANT,value=0); mean=np.array([103.53,116.28,123.675],dtype=np.float32).reshape(1,1,3); std=np.array([57.375,57.12,58.395],dtype=np.float32).reshape(1,1,3); image=np.asarray((image.astype(np.float32)-mean)/std,dtype=np.float32); return cv2.dnn.blobFromImage(image)
        except NanoDetError: raise
        except Exception as error: raise NanoDetInferenceError(f"NanoDet inference failed: {error}") from error
    def detect(self, frame):
        if self._net is None: self.load()
        try:
            blob=self._prepare_blob(frame); self._net.setInput(blob); return decode_outputs(self._net.forward(self._names),names=self._names,confidence=self.confidence,nms=self.nms,limit=self.max_detections,np=self._np)
        except NanoDetError: raise
        except Exception as error: raise NanoDetInferenceError(f"NanoDet inference failed: {error}") from error
    def close(self): self._net=None
