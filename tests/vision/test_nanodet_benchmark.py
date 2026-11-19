"""Hardware-free tests for the shared NanoDet detector and its CLI harness."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
from robot.vision.nanodet import (NanoDetDetector, NanoDetInferenceError, NanoDetInvalidFrame,
                                  NanoDetModelUnavailable, STRIDES, decode_outputs, output_diagnostics,
                                  non_max_suppression, NanoDetDetection)

def outputs(strides=STRIDES, score=.9, label=2):
    out=[]
    for s in strides:
        n=(416//s)**2; a=np.zeros((1,n,80),np.float32); b=np.zeros((1,n,32),np.float32); out += [a,b]
    out[0][0,0,label]=score; return out

def test_shared_decoder_supports_six_and_eight_outputs_and_filtering():
    assert decode_outputs(outputs((8,16,32)),np=np)[0].label == "car"
    assert decode_outputs(outputs(),np=np)[0].confidence == pytest.approx(.9)
    assert decode_outputs(outputs(score=.2),confidence=.35,np=np) == []

def test_shared_decoder_rejects_invalid_layouts_and_limits_results():
    with pytest.raises(NanoDetInferenceError): decode_outputs(outputs()[:-1],np=np)
    bad=outputs(); bad[0],bad[1]=bad[1],bad[0]
    with pytest.raises(NanoDetInferenceError): decode_outputs(bad,np=np)
    many=outputs(); many[0][0,100,1]=.8
    assert len(decode_outputs(many,limit=1,np=np)) == 1

def test_shared_nms():
    a=NanoDetDetection("person",.9,0,0,10,10); b=NanoDetDetection("car",.8,1,1,11,11)
    assert non_max_suppression([a,b],.6)==[a]

def test_diagnostics_are_shared_contract():
    report=output_diagnostics(("cls_stride_8","dis_stride_8"),outputs((8,))[:2],np)
    assert report["returned_tensor_count"]==2 and report["tensors"][0]["role"]=="classification"
    assert report["tensors"][0]["matching_strides"]==[8]

def test_preprocessing_uint8_bgr_float32_blob():
    class D:
        def blobFromImage(self,x): self.value=x; return np.moveaxis(x,-1,0)[None]
    class C:
        COLOR_BGR2RGB=1; INTER_AREA=2; BORDER_CONSTANT=3; dnn=D()
        cvtColor=staticmethod(lambda x,_:x[...,::-1]); resize=staticmethod(lambda x,*_,**__:x)
        copyMakeBorder=staticmethod(lambda x,*_,**__:x)
    detector=NanoDetDetector("missing",cv2_module=C,np_module=np); image=np.zeros((416,416,3),np.uint8); image[0,0]=[0,0,255]
    blob=detector._prepare_blob(image)
    assert C.dnn.value.dtype==np.float32 and blob.shape==(1,3,416,416)
    # A BGR pure-red input reaches the model as RGB [255, 0, 0].
    np.testing.assert_allclose(C.dnn.value[0,0],(np.array([255,0,0],np.float32)-np.array([103.53,116.28,123.675],np.float32))/np.array([57.375,57.12,58.395],np.float32))
    with pytest.raises(NanoDetInvalidFrame): detector._prepare_blob(np.zeros((2,2),np.uint8))

def test_missing_model_and_inference_failure(tmp_path):
    with pytest.raises(NanoDetModelUnavailable): NanoDetDetector(tmp_path/"none.onnx").load()
    model=tmp_path/"m.onnx"; model.write_bytes(b"x")
    class D:
        DNN_BACKEND_OPENCV=0; DNN_TARGET_CPU=0
        def readNet(self,_): raise RuntimeError("bad")
    class C: dnn=D()
    with pytest.raises(NanoDetModelUnavailable): NanoDetDetector(model,cv2_module=C,np_module=np).load()

def test_cli_arguments_remain_available():
    spec=importlib.util.spec_from_file_location("bench",Path(__file__).parents[2]/"scripts/benchmark_nanodet.py"); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    args=mod.parse_args(["--model","m.onnx","--image","i.jpg","--diagnose-outputs"])
    assert args.warmup==3 and args.diagnose_outputs
