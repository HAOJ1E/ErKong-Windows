"""摄像头子进程：仅输出眼睛分数，不保存图像、不移动或点击鼠标。"""

import argparse
import json
import os
from pathlib import Path
import threading
import time
import sys


MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "face_landmarker.task"


def emit(value):
    line = json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n"
    if sys.stdout is not None:
        print(line, end="", flush=True)
    else:
        write_windows_pipe(line.encode("ascii"))


def write_windows_pipe(payload):
    """无控制台 EXE 的 sys.stdout 可能为 None，直接写父进程传入的管道。"""
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_uint32]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    kernel.WriteFile.restype = ctypes.c_int
    handle = kernel.GetStdHandle(0xFFFFFFF5)  # STD_OUTPUT_HANDLE = -11
    written = ctypes.c_uint32()
    buffer = ctypes.create_string_buffer(payload)
    if not handle or not kernel.WriteFile(handle, buffer, len(payload), ctypes.byref(written), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if written.value != len(payload):
        raise RuntimeError("摄像头结果管道写入不完整")


def watch_parent(parent):
    from nodlink.bridge_recovery import process_is_alive
    while True:
        time.sleep(1)
        if not process_is_alive(parent):
            os._exit(0)  # 主窗口异常退出时，Windows 会释放本进程的摄像头句柄。


def run_camera(camera_index=0, seconds=None):
    import cv2
    import mediapipe as mp

    capture = None
    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_faces=2, output_face_blendshapes=True,
        min_face_detection_confidence=0.6, min_face_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    try:
        with mp.tasks.vision.FaceLandmarker.create_from_options(options) as model:
            for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
                capture = cv2.VideoCapture(camera_index, backend)
                if capture.isOpened():
                    break
                capture.release()
            if capture is None or not capture.isOpened():
                raise RuntimeError("摄像头无法打开，可能被其他程序占用或设备未就绪")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            emit({"kind": "ready"})
            started = time.monotonic()
            while seconds is None or time.monotonic() - started < seconds:
                frame_time = time.monotonic()
                success, frame = capture.read()
                if not success:
                    raise RuntimeError("摄像头读取中断，眨眼点击已暂停")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = model.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int(frame_time * 1000))
                faces = len(result.face_landmarks)
                scores = {item.category_name: item.score for item in result.face_blendshapes[0]} if faces == 1 and result.face_blendshapes else {}
                emit({"kind": "sample", "face_count": faces, "left": scores.get("eyeBlinkLeft"),
                      "right": scores.get("eyeBlinkRight"), "received_at_monotonic": frame_time})
                time.sleep(max(0, 0.05 - (time.monotonic() - frame_time)))
    finally:
        if capture is not None:
            capture.release()


def main(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0, choices=range(10))
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--parent", type=int)
    args = parser.parse_args(arguments)
    if args.parent:
        threading.Thread(target=watch_parent, args=(args.parent,), daemon=True).start()
    try:
        if not MODEL_PATH.exists():
            raise RuntimeError("缺少预训练模型，请先运行 install_blink.bat")
        run_camera(args.camera, args.seconds)
    except ModuleNotFoundError:
        emit({"kind": "error", "message": "缺少眨眼组件，请先运行 install_blink.bat"})
        return 1
    except Exception as error:
        emit({"kind": "error", "message": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
