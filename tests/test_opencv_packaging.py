from pathlib import Path


def test_pi_packaging_declares_no_pypi_cv2_provider_and_keeps_the_smoke_check():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    installer = Path("scripts/install-phos.sh").read_text(encoding="utf-8")
    check = Path("tools/check_opencv.py").read_text(encoding="utf-8")
    assert '"opencv-python-headless' not in project
    assert "opencv-python-headless" not in [line.strip() for line in requirements.splitlines() if not line.lstrip().startswith("#")]
    assert "python3-opencv" in installer and "tools/check_opencv.py" in installer
    assert "CascadeClassifier" in check and "Conflicting Python cv2 providers" in check
