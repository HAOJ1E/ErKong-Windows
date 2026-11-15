"""从 Google 官方固定版本地址下载预训练模型，不获取或上传用户图像。"""

import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

from nodlink.control_settings import save_json_atomic


MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_landmarker.task"


def main():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MODEL_PATH.exists():
        with urllib.request.urlopen(MODEL_URL, timeout=40) as response:
            content = response.read(20 * 1024 * 1024)
        # task 文件允许 ZIP 前存在对齐字节，不能只检查开头是否为 PK。
        if not 1_000_000 < len(content) < 20 * 1024 * 1024 or not zipfile.is_zipfile(io.BytesIO(content)):
            raise RuntimeError("模型下载内容不符合预期，未保存")
        MODEL_PATH.with_suffix(".download").write_bytes(content)
        MODEL_PATH.with_suffix(".download").replace(MODEL_PATH)
    content = MODEL_PATH.read_bytes()
    if not 1_000_000 < len(content) < 20 * 1024 * 1024 or not zipfile.is_zipfile(io.BytesIO(content)):
        raise RuntimeError("现有模型文件损坏，请重新下载")
    manifest = {"source": MODEL_URL, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                "personal_training_required": False}
    save_json_atomic(MODEL_PATH.with_suffix(".json"), manifest)
    print(f"Model ready: {len(content)} bytes; SHA256 {manifest['sha256']}")


if __name__ == "__main__":
    main()
