"""Strukturtests fuer plattformspezifische Aufrufe der nativen Bridge."""

from pathlib import Path

BRIDGE_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "native-bridge.js"


def test_fcm_and_device_headers_use_platform_helper():
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    assert "platform: 'android'" not in source
    assert "platform: _platform()" in source
    assert "h['X-ECP-Platform'] = _platform();" in source
    helper = source.split("function _platform() {", 1)[1].split("\n  }", 1)[0]
    assert "typeof window.Capacitor.getPlatform === 'function'" in helper
    assert "? window.Capacitor.getPlatform()" in helper
    assert ": 'android'" in helper


def test_google_barcode_module_is_only_used_on_android():
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    android_block = source.split("if (_platform() === 'android') {", 1)[1].split(
        "} else if (_platform() === 'ios') {", 1
    )[0]
    for method in ("isGoogleBarcodeScannerModuleAvailable", "installGoogleBarcodeScannerModule"):
        assert source.count("BarcodeScanner." + method + "()") == 1
        assert "BarcodeScanner." + method + "()" in android_block

    ios_block = source.split("} else if (_platform() === 'ios') {", 1)[1].split(
        "const { barcodes } = await BarcodeScanner.scan();", 1
    )[0]
    assert "await BarcodeScanner.requestPermissions();" in ios_block
    assert "if (camera !== 'granted') {" in ios_block
    assert "_err('Kamerazugriff wurde nicht erlaubt');\n          return;" in ios_block
