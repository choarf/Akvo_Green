"""hwinfo.hardware_info: board model and serial from the device tree."""
import hwinfo


def test_reads_model_and_serial(tmp_path):
    (tmp_path / "model").write_bytes(b"Raspberry Pi 4 Model B Rev 1.5\x00")
    (tmp_path / "serial-number").write_bytes(b"10000000028aeebf\x00")
    info = hwinfo.hardware_info(tmp_path)
    assert info["hw_model"] == "Raspberry Pi 4 Model B Rev 1.5"
    assert info["hw_serial"] == "10000000028aeebf"
    assert info["hostname"]


def test_off_a_pi_model_and_serial_are_none(tmp_path):
    info = hwinfo.hardware_info(tmp_path / "missing")
    assert info["hw_model"] is None and info["hw_serial"] is None


def test_empty_file_is_none(tmp_path):
    (tmp_path / "serial-number").write_bytes(b"\x00")
    assert hwinfo.hardware_info(tmp_path)["hw_serial"] is None
