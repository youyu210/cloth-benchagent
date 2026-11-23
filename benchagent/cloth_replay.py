"""Render fixed-cloth benchmark geometry from archived state arrays."""
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from .common import read, sha


class ClothReplay:
    def __init__(self, raw, mesh, expected_hash):
        meta = read(raw / "metadata.json")
        status = read(raw / "status.json")
        if (meta.get("scene_no") != 3 or meta.get("vertex_count") != 4225
                or status.get("completed") is not True
                or status.get("finite_state") is not True
                or type(status.get("frames_completed")) is not int
                or not 1 <= status["frames_completed"] <= 1000):
            raise ValueError("缺少完整的固定布料状态")
        if sha(mesh) != expected_hash:
            raise ValueError("布料拓扑与归档资产身份不一致")
        self.raw = raw
        self.last = status["frames_completed"]
        self.stride = 4225 * 3 * 8
        self.full = (raw / "states.f64").is_file() and (
            raw / "states.f64").stat().st_size == self.last * self.stride
        vertices, faces = [], []
        for line in mesh.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if fields and fields[0] == "v":
                vertices.append([float(value) for value in fields[1:4]])
            if fields and fields[0] == "f":
                faces.append([int(value.split("/")[0]) - 1 for value in fields[1:]])
        self.faces = np.asarray(faces)
        if self.faces.ndim != 2 or self.faces.shape[1] != 3 or self.faces.min() < 0 or self.faces.max() >= 4225:
            raise ValueError("无效的布料三角面索引")
        angle = 3.1415926 / 2
        c, s = np.cos(angle), np.sin(angle)
        initial = .6 * (np.asarray(vertices) @ np.array([[1, 0, 0], [0, c, s], [0, -s, c]])) + [0, 1, 0]
        if not np.allclose(initial, self._vertices(0), rtol=0, atol=1e-10):
            raise ValueError("状态顶点顺序与场景拓扑不一致")

    def _vertices(self, frame):
        filename, offset = ("states.f64", (frame - 1) * self.stride)
        if frame in (0, self.last):
            filename, offset = ("initial.f64" if frame == 0 else "final.f64"), 0
            if (self.raw / filename).stat().st_size != self.stride:
                raise ValueError("状态大小不正确")
        with (self.raw / filename).open("rb") as stream:
            stream.seek(offset)
            points = np.frombuffer(stream.read(self.stride), dtype="<f8").reshape(4225, 3)
        if not np.isfinite(points).all():
            raise ValueError("状态包含非有限坐标")
        return points

    def _render(self, frame):
        if frame not in (0, self.last) and not (self.full and 0 < frame < self.last):
            raise ValueError("所选帧没有已归档的几何状态")
        points = self._vertices(frame) - [0, 1, 0]
        right = np.array([.94, 0, -.342])
        up = np.array([-.18, .85, -.494])
        depth = np.cross(right, up)
        pixels = np.column_stack((360 + 345 * (points @ right), 235 - 345 * (points @ up)))
        order = np.argsort((points @ depth)[self.faces].mean(axis=1), kind="stable")
        triangles = points[self.faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        light = np.abs(normals @ depth) / np.maximum(np.linalg.norm(normals, axis=1), 1e-20)
        image = Image.new("RGB", (720, 470), "#111827")
        draw = ImageDraw.Draw(image)
        for i in order:
            color = tuple(int(base + light[i] * gain) for base, gain in ((35, 68), (87, 95), (136, 102)))
            draw.polygon([tuple(p) for p in pixels[self.faces[i]]], fill=color, outline="#315577")
        return image

    def png(self, frame):
        output = BytesIO()
        self._render(frame).save(output, format="PNG")
        return output.getvalue()

    def animation(self):
        """Return an evenly sampled preview including both endpoints."""
        if not self.full:
            raise ValueError("该归档没有完整的逐帧状态")
        numbers = np.linspace(0, self.last, min(60, self.last + 1), dtype=int)
        frames = [
            self._render(int(frame)).resize((480, 313), Image.Resampling.LANCZOS)
            for frame in numbers
        ]
        output = BytesIO()
        frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                       duration=100, loop=0, disposal=2)
        return output.getvalue(), len(numbers)
