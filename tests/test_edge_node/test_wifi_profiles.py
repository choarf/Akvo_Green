"""Unit tests for tools/wifi_profiles.py: the root script tools/push_site.sh
pipes to the Pi to install a site's WiFi networks into NetworkManager."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import wifi_profiles as wp  # noqa: E402


def config(*networks):
    return {"wifi": {"networks": list(networks)}}


def test_two_networks_become_two_profiles_preferred_first():
    script = wp.build_script(config("Planta", "Oficina"),
                             {"Planta": "secreto123", "Oficina": "otra-clave"}, "wifi.csv")
    assert "id=akvo-wifi1\n" in script and "autoconnect-priority=20" in script
    assert "id=akvo-wifi2\n" in script and "autoconnect-priority=10" in script
    assert script.index("ssid=Planta") < script.index("ssid=Oficina")
    assert "psk=secreto123" in script
    assert script.rstrip().endswith("nmcli connection reload")


def test_one_network_removes_the_second_profile():
    script = wp.build_script(config("Planta"), {"Planta": "secreto123"}, "wifi.csv")
    assert "rm -f '/etc/NetworkManager/system-connections/akvo-wifi2.nmconnection'" in script
    assert "akvo-wifi2\n" not in script


def test_no_networks_removes_both_profiles():
    script = wp.build_script(config(), {}, "wifi.csv")
    assert script.count("rm -f") == 2 and "cat >" not in script


def test_empty_password_is_an_open_network():
    script = wp.build_script(config("Libre"), {"Libre": ""}, "wifi.csv")
    assert "[wifi-security]" not in script


@pytest.mark.parametrize("passwords, needle", [
    ({}, "no password for WiFi 'Planta'"),
    ({"Planta": "corta"}, "8-63 characters"),
    ({"Planta": "x" * 64}, "8-63 characters"),
    ({"Planta": "dos\nlineas"}, "line break"),
    ({"Planta": "CHANGE_ME"}, "placeholder"),
])
def test_bad_or_missing_password_is_an_error(passwords, needle):
    with pytest.raises(ValueError, match=needle):
        wp.build_script(config("Planta"), passwords, "wifi.csv")


def test_keyfile_escapes_backslashes_and_a_leading_space():
    assert wp._keyfile_value("a\\b") == "a\\\\b"
    assert wp._keyfile_value(" red") == "\\sred"


def test_load_passwords_reads_ssid_password_rows(tmp_path):
    f = tmp_path / "wifi.csv"
    f.write_text("ssid,password\nPlanta,secreto123\nLibre,\n", encoding="utf-8")
    assert wp.load_passwords(f) == {"Planta": "secreto123", "Libre": ""}
    assert wp.load_passwords(tmp_path / "missing.csv") == {}


def test_generated_script_writes_mode_600_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "PROFILE_DIR", str(tmp_path))
    script = wp.build_script(config("Planta"), {"Planta": "secreto123"}, "wifi.csv")
    script = script.replace("nmcli connection reload", "true")
    (tmp_path / "akvo-wifi2.nmconnection").write_text("old")
    subprocess.run(["bash", "-c", script], check=True)
    profile = tmp_path / "akvo-wifi1.nmconnection"
    assert "psk=secreto123" in profile.read_text()
    assert oct(profile.stat().st_mode & 0o777) == "0o600"
    assert not (tmp_path / "akvo-wifi2.nmconnection").exists()


def test_main_exit_codes(tmp_path):
    tool = Path(wp.__file__)
    no_wifi = tmp_path / "a.json"
    no_wifi.write_text("{}")
    missing = tmp_path / "b.json"
    missing.write_text('{"wifi": {"networks": ["Planta"]}}')
    run = lambda c: subprocess.run([sys.executable, str(tool), str(c), str(tmp_path / "wifi.csv")],
                                   capture_output=True, text=True)
    assert run(no_wifi).returncode == 2
    r = run(missing)
    assert r.returncode == 1 and r.stdout == "" and "no password" in r.stderr
