from pp_dashboard.mayhem.frame import decode_short_pixel, decode_screenframeshort


def test_black_and_white_pixels() -> None:
    assert decode_short_pixel(ord(" ")) == (0, 0, 0, 255)
    assert decode_short_pixel(ord("_")) == (255, 255, 255, 255)


def test_decode_screenframeshort_rows() -> None:
    # 4x2 tiny frame of spaces (black)
    rows = [" " * 4, "_" * 4]
    text = "screenframeshort\n" + "\n".join(rows) + "\nok\nch>"
    frame = decode_screenframeshort(text, expect_width=4)
    assert frame.width == 4
    assert frame.height == 2
    assert frame.rgba[0:4] == bytes([0, 0, 0, 255])
    # last pixel of second row is white
    assert frame.rgba[-4:] == bytes([255, 255, 255, 255])
