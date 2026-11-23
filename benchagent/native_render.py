"""Identity and archive validation for opt-in native framebuffer capture."""
from io import BytesIO

from PIL import Image, ImageChops

from .common import ROOT, read, sha, write


MANIFEST = "versions/native_frames_manifest.json"
EXECUTABLE = "build/native-frames/gipc.exe"
SOURCE = "versions/native-frames-source/StiffGIPC/gl_main.cu"


VARIANT = "native-box-scene"


def runtime_identity(root=ROOT, variant=None):
    if variant not in (None, VARIANT, "native-material"):
        raise ValueError("未登记的原生构建")
    manifest_path = MANIFEST if variant is None else f"versions/{variant}-manifest.json"
    executable = EXECUTABLE if variant is None else f"build/{variant}/gipc.exe"
    source = SOURCE if variant is None else f"versions/{variant}-source/StiffGIPC/gl_main.cu"
    manifest = read(root / manifest_path)
    if not {executable, source}.issubset(manifest["files"]):
        raise ValueError("原生捕获构建清单缺少必需文件")
    for path, expected in manifest["files"].items():
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root.resolve()) or sha(resolved) != expected:
            raise ValueError("原生捕获构建身份不匹配")
    return {"manifest_sha256": sha(root / manifest_path),
            "executable": str(root / executable),
            "sha256": manifest["files"][executable]}


def frame_name(frame):
    return f"native_frames/frame-{frame}.bmp"


def validate_capture(raw, scene_no, count, identity, cloth_scene=""):
    capture = read(raw / "native_render.json")
    expected = sorted({0, count // 2, count})
    if (capture.get("kind") != "native_render_frame" or capture.get("scene_no") != scene_no
            or capture.get("frames") != expected or capture.get("error")
            or capture.get("width") != 1000 or capture.get("height") != 1000):
        raise ValueError("原生捕获元数据不完整")
    files = {}
    for frame in expected:
        name = frame_name(frame)
        path = raw / name
        with Image.open(path) as image:
            if image.format != "BMP" or image.size != (1000, 1000):
                raise ValueError("原生画面尺寸或格式异常")
            rgb = image.convert("RGB")
            flat = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
            if ImageChops.difference(rgb, flat).getbbox() is None:
                raise ValueError("原生画面未包含可见几何")
        files[name] = sha(path)
    if cloth_scene:
        setup = read(raw / "scene_setup.json")
        if setup.get("scene") != cloth_scene:
            raise ValueError("原生画面子场景身份不匹配")
        capture.update(cloth_scene=cloth_scene, scene_camera=setup["camera"])
    capture.update(files=files, build_sha256=identity["sha256"])
    write(raw / "native_render.json", capture)
    return capture


def frame_png(path, expected_sha):
    if sha(path) != expected_sha:
        raise ValueError("归档原生帧哈希不匹配")
    output = BytesIO()
    with Image.open(path) as image:
        image.save(output, format="PNG")
    return output.getvalue()
