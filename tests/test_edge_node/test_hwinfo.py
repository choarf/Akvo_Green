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


# ---------------------------------------------------------------------------
# wifi_info: active WiFi link from NetworkManager (nmcli -t)
# ---------------------------------------------------------------------------

from types import SimpleNamespace


class FakeRun:
    def __init__(self, wifi="", conns="", raise_=None):
        self.out = {"dev": wifi, "connection": conns}
        self.raise_ = raise_

    def __call__(self, cmd, **kw):
        if self.raise_:
            raise self.raise_
        return SimpleNamespace(stdout=self.out["dev" if "dev" in cmd else "connection"])


def test_wifi_info_active_network_profile_and_signal():
    run = FakeRun(wifi="no:Vecino:40\nyes:Vocho:72\n",
                  conns="Wired connection 1:802-3-ethernet\nakvo-wifi1:802-11-wireless\n")
    assert hwinfo.wifi_info(run) == {"ssid": "Vocho", "profile": "akvo-wifi1", "signal": 72}


def test_wifi_info_ssid_with_escaped_colon():
    run = FakeRun(wifi="yes:Casa\\:5G:55\n", conns="akvo-wifi2:802-11-wireless\n")
    assert hwinfo.wifi_info(run)["ssid"] == "Casa:5G"


def test_wifi_info_none_without_wifi_or_nmcli():
    assert hwinfo.wifi_info(FakeRun(wifi="no:Vecino:40\n")) is None          # Ethernet only
    assert hwinfo.wifi_info(FakeRun(raise_=FileNotFoundError("nmcli"))) is None
