"""메이플 클라이언트 MCV0 영상의 마지막 프레임을 PNG로 저장합니다."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import cv2


def write_ivf(path: Path, fourcc: bytes, width: int, height: int, frames: list[bytes]) -> None:
    """OpenCV가 VP9 프레임을 읽을 수 있도록 작은 IVF 컨테이너를 만듭니다."""
    with path.open("wb") as output:
        output.write(
            struct.pack(
                "<4sHH4sHHIIII",
                b"DKIF",
                0,
                32,
                fourcc,
                width,
                height,
                60,
                1,
                len(frames),
                0,
            )
        )
        for timestamp, frame in enumerate(frames):
            output.write(struct.pack("<IQ", len(frame), timestamp))
            output.write(frame)


def decode_last_frame(path: Path):
    capture = cv2.VideoCapture(str(path))
    last_frame = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            last_frame = frame
    finally:
        capture.release()
    if last_frame is None:
        raise ValueError(f"영상 프레임을 읽지 못했습니다: {path}")
    return last_frame


def extract(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    if data[:4] != b"MCV0":
        raise ValueError("MCV0 영상이 아닙니다.")

    header_length = struct.unpack_from("<H", data, 6)[0]
    fourcc = (struct.unpack_from("<I", data, 8)[0] ^ 0xA5A5A5A5).to_bytes(4, "little")
    width, height, frame_count = struct.unpack_from("<HHI", data, 12)
    flags = data[20]
    position = header_length

    # 색상·알파 프레임의 위치표를 먼저 읽고 실제 데이터 시작점을 계산합니다.
    color_info = [
        struct.unpack_from("<ii", data, position + index * 8)
        for index in range(frame_count)
    ]
    position += frame_count * 8
    alpha_info: list[tuple[int, int]] = []
    if flags & 1:
        alpha_info = [
            struct.unpack_from("<ii", data, position + index * 8)
            for index in range(frame_count)
        ]
        position += frame_count * 8
    if flags & 2:
        position += frame_count * 4
    if flags & 4:
        position += frame_count * 8
    data_start = position

    def frame_bytes(entries: list[tuple[int, int]]) -> list[bytes]:
        return [
            data[data_start + offset : data_start + offset + size]
            for offset, size in entries
        ]

    color_ivf = source.with_suffix(".color.ivf")
    write_ivf(color_ivf, fourcc, width, height, frame_bytes(color_info))
    color = decode_last_frame(color_ivf)
    result = cv2.cvtColor(color, cv2.COLOR_BGR2BGRA)

    if alpha_info:
        alpha_ivf = source.with_suffix(".alpha.ivf")
        write_ivf(alpha_ivf, fourcc, width, height, frame_bytes(alpha_info))
        alpha = decode_last_frame(alpha_ivf)
        # 클라이언트도 알파 영상의 빨간 채널을 최종 투명도로 사용합니다.
        result[:, :, 3] = alpha[:, :, 2]

    destination.parent.mkdir(parents=True, exist_ok=True)
    # OpenCV의 Windows 저장 함수는 한글 경로를 처리하지 못해 메모리에서 PNG로 바꿉니다.
    ok, encoded = cv2.imencode(".png", result)
    if not ok:
        raise OSError(f"PNG를 만들지 못했습니다: {destination}")
    destination.write_bytes(encoded.tobytes())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("사용법: extract_mcv_last_frame.py <입력.mcv> <출력.png>")
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
