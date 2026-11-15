"""让头控输出避开屏幕角落，同时保留实体鼠标的紧急停止功能。"""


CORNER_PADDING_PIXELS = 8


def avoid_screen_corners(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """仅将四个角落的小区域向内收，其他屏幕边缘仍可到达。"""
    if width < 3 or height < 3:
        raise ValueError("屏幕尺寸过小，无法保留角落保护区")
    x = min(max(int(x), 0), width - 1)
    y = min(max(int(y), 0), height - 1)
    padding_x = min(CORNER_PADDING_PIXELS, (width - 1) // 2)
    padding_y = min(CORNER_PADDING_PIXELS, (height - 1) // 2)
    near_horizontal_edge = x < padding_x or x > width - 1 - padding_x
    near_vertical_edge = y < padding_y or y > height - 1 - padding_y
    if near_horizontal_edge and near_vertical_edge:
        x = min(max(x, padding_x), width - 1 - padding_x)
        y = min(max(y, padding_y), height - 1 - padding_y)
    return x, y
