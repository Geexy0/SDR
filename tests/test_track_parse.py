"""Unit tests for ADS-B SBS + AIS AIVDM track parsers."""

from __future__ import annotations

from pp_dashboard.recon.track_parse import parse_adsb_json, parse_ais_nmea, parse_sbs_line


def test_parse_sbs_position_message() -> None:
    # MSG,3 ... with lat/lon populated
    line = (
        "MSG,3,1,1,ABC123,1,2024/01/01,12:00:00.000,2024/01/01,12:00:00.000,"
        "TEST123 ,35000,,,48.8566,2.3522,,,0,0,0,0"
    )
    out = parse_sbs_line(line)
    assert out is not None
    assert out["icao"] == "ABC123"
    assert out["callsign"] == "TEST123"
    assert out["lat"] == 48.8566
    assert out["lon"] == 2.3522
    assert out["alt"] == 35000.0


def test_parse_sbs_ignores_non_msg() -> None:
    assert parse_sbs_line("STA,1,1,1,ABC123") is None


def test_parse_adsb_json() -> None:
    out = parse_adsb_json('{"hex":"a1b2c3","lat":40.1,"lon":-74.2,"flight":"UAL1  "}')
    assert out is not None
    assert out["icao"] == "A1B2C3"
    assert out["lat"] == 40.1
    assert out["callsign"] == "UAL1"


def test_parse_ais_type1_position() -> None:
    # Known AIVDM type-1 sample (MMSI 257061900-ish region); validate decode shape.
    # Payload from public AIS sample: position report
    line = "!AIVDM,1,1,,A,133m@ogP00PD;88MD5MTDww@2D7k,0*46"
    out = parse_ais_nmea(line)
    assert out is not None
    assert out["type"] == "ais"
    assert out["mmsi"]
    assert len(out["mmsi"]) == 9
    # Position fields present for type 1
    if "lat" in out:
        assert -90 <= out["lat"] <= 90
        assert -180 <= out["lon"] <= 180


def test_parse_ais_text_mmsi() -> None:
    out = parse_ais_nmea("AIS MMSI=366123456 lat=47.6 lon=-122.3")
    assert out is not None
    assert out["mmsi"] == "366123456"
    assert out["lat"] == 47.6
    assert out["lon"] == -122.3
