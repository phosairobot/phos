#!/usr/bin/env python3
"""Verify the one-cv2-provider invariant in the active Python environment."""
from importlib import metadata

PROVIDERS = ("opencv", "opencv-python", "opencv-python-headless",
             "opencv-contrib-python", "opencv-contrib-python-headless")

def installed_providers():
    found = []
    for name in PROVIDERS:
        try:
            found.append((name, metadata.version(name)))
        except metadata.PackageNotFoundError:
            pass
    return found

def main():
    import cv2
    version = tuple(int(part) for part in cv2.__version__.split(".")[:2])
    if not ((4, 10) <= version < (5, 0)):
        raise SystemExit(f"Unsupported PHOS OpenCV version {cv2.__version__}; require >=4.10,<5 from Raspberry Pi OS.")
    providers = installed_providers()
    if len(providers) > 1:
        raise SystemExit(f"Conflicting Python cv2 providers: {providers}. Keep only Raspberry Pi OS python3-opencv.")
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise SystemExit(f"OpenCV Haar cascade could not load: {cascade_path}")
    print(f"OpenCV {cv2.__version__}; cv2={cv2.__file__}; haarcascades={cv2.data.haarcascades}; providers={providers or 'system package'}")

if __name__ == "__main__":
    main()
