"""Small built-in DTC description table + rule-based fallback for any code.

Generic (P0/P2/P3 0-3xx, U0) codes are standardised by SAE J2012 and mean the
same on every car. P1/B1/C1/U1 etc. are manufacturer-specific: we give the
system/category and you should look up the brand meaning.
Add your own entries to <data dir>/custom_dtcs.json as {"P1234": "text"}.
"""
import json
import os

CODES = {
    "P0010": "Camshaft position actuator circuit (Bank 1)",
    "P0011": "Camshaft timing over-advanced (Bank 1)",
    "P0012": "Camshaft timing over-retarded (Bank 1)",
    "P0016": "Crankshaft/camshaft position correlation (Bank 1 Sensor A)",
    "P0030": "HO2S heater control circuit (Bank 1 Sensor 1)",
    "P0087": "Fuel rail/system pressure too low",
    "P0088": "Fuel rail/system pressure too high",
    "P0100": "Mass air flow circuit malfunction",
    "P0101": "Mass air flow circuit range/performance",
    "P0102": "Mass air flow circuit low input",
    "P0103": "Mass air flow circuit high input",
    "P0106": "MAP/baro circuit range/performance",
    "P0110": "Intake air temperature circuit malfunction",
    "P0113": "Intake air temperature circuit high input",
    "P0115": "Engine coolant temperature circuit malfunction",
    "P0116": "Engine coolant temperature range/performance",
    "P0117": "Engine coolant temperature circuit low",
    "P0118": "Engine coolant temperature circuit high",
    "P0120": "Throttle position sensor circuit malfunction",
    "P0121": "Throttle position sensor range/performance",
    "P0122": "Throttle position sensor circuit low",
    "P0123": "Throttle position sensor circuit high",
    "P0125": "Insufficient coolant temp for closed loop",
    "P0128": "Coolant thermostat below regulating temperature",
    "P0130": "O2 sensor circuit (Bank 1 Sensor 1)",
    "P0131": "O2 sensor low voltage (Bank 1 Sensor 1)",
    "P0133": "O2 sensor slow response (Bank 1 Sensor 1)",
    "P0135": "O2 sensor heater circuit (Bank 1 Sensor 1)",
    "P0136": "O2 sensor circuit (Bank 1 Sensor 2)",
    "P0141": "O2 sensor heater circuit (Bank 1 Sensor 2)",
    "P0161": "O2 sensor heater circuit (Bank 2 Sensor 2)",
    "P0171": "System too lean (Bank 1)",
    "P0172": "System too rich (Bank 1)",
    "P0174": "System too lean (Bank 2)",
    "P0175": "System too rich (Bank 2)",
    "P0191": "Fuel rail pressure sensor range/performance",
    "P0200": "Injector circuit malfunction",
    "P0201": "Injector circuit - cylinder 1",
    "P0202": "Injector circuit - cylinder 2",
    "P0203": "Injector circuit - cylinder 3",
    "P0204": "Injector circuit - cylinder 4",
    "P0217": "Engine overheat condition",
    "P0219": "Engine overspeed condition",
    "P0234": "Turbo/supercharger overboost",
    "P0299": "Turbo/supercharger underboost",
    "P0300": "Random/multiple cylinder misfire detected",
    "P0301": "Cylinder 1 misfire detected",
    "P0302": "Cylinder 2 misfire detected",
    "P0303": "Cylinder 3 misfire detected",
    "P0304": "Cylinder 4 misfire detected",
    "P0305": "Cylinder 5 misfire detected",
    "P0306": "Cylinder 6 misfire detected",
    "P0307": "Cylinder 7 misfire detected",
    "P0308": "Cylinder 8 misfire detected",
    "P0325": "Knock sensor 1 circuit (Bank 1)",
    "P0335": "Crankshaft position sensor A circuit",
    "P0340": "Camshaft position sensor circuit (Bank 1)",
    "P0351": "Ignition coil A primary/secondary circuit",
    "P0352": "Ignition coil B primary/secondary circuit",
    "P0353": "Ignition coil C primary/secondary circuit",
    "P0354": "Ignition coil D primary/secondary circuit",
    "P0380": "Glow plug/heater circuit A",
    "P0400": "EGR flow malfunction",
    "P0401": "EGR flow insufficient",
    "P0402": "EGR flow excessive",
    "P0403": "EGR circuit malfunction",
    "P0404": "EGR circuit range/performance",
    "P0410": "Secondary air injection system",
    "P0420": "Catalyst efficiency below threshold (Bank 1)",
    "P0430": "Catalyst efficiency below threshold (Bank 2)",
    "P0440": "EVAP emission control system malfunction",
    "P0441": "EVAP incorrect purge flow",
    "P0442": "EVAP small leak detected",
    "P0443": "EVAP purge control valve circuit",
    "P0446": "EVAP vent control circuit",
    "P0455": "EVAP large leak detected",
    "P0456": "EVAP very small leak detected",
    "P0457": "EVAP leak detected (fuel cap loose/off)",
    "P0461": "Fuel level sensor range/performance",
    "P0480": "Cooling fan 1 control circuit",
    "P0500": "Vehicle speed sensor malfunction",
    "P0505": "Idle control system malfunction",
    "P0506": "Idle RPM lower than expected",
    "P0507": "Idle RPM higher than expected",
    "P0520": "Engine oil pressure sensor circuit",
    "P0560": "System voltage malfunction",
    "P0562": "System voltage low",
    "P0563": "System voltage high",
    "P0571": "Brake switch A circuit",
    "P0600": "Serial communication link malfunction",
    "P0601": "Control module memory checksum error",
    "P0603": "Control module KAM error",
    "P0604": "Control module RAM error",
    "P0606": "Control module processor fault",
    "P0700": "Transmission control system malfunction (see TCM codes)",
    "P0705": "Transmission range sensor circuit",
    "P0715": "Input/turbine speed sensor circuit",
    "P0720": "Output speed sensor circuit",
    "P0730": "Incorrect gear ratio",
    "P0740": "Torque converter clutch circuit",
    "P0741": "Torque converter clutch stuck off",
    "P0750": "Shift solenoid A malfunction",
    "P0755": "Shift solenoid B malfunction",
    "P2002": "Diesel particulate filter efficiency below threshold (Bank 1)",
    "P2096": "Post-catalyst fuel trim too lean (Bank 1)",
    "P2097": "Post-catalyst fuel trim too rich (Bank 1)",
    "P2135": "Throttle/pedal position sensor A/B correlation",
    "P2138": "Throttle/pedal position sensor D/E correlation",
    "P2187": "System too lean at idle (Bank 1)",
    "P2463": "Diesel particulate filter soot accumulation",
    "U0001": "High speed CAN communication bus",
    "U0073": "Control module communication bus A off",
    "U0100": "Lost communication with ECM/PCM",
    "U0101": "Lost communication with TCM",
    "U0121": "Lost communication with ABS module",
    "U0140": "Lost communication with body control module",
    "U0151": "Lost communication with airbag (SRS) module",
    "U0155": "Lost communication with instrument cluster",
    "U0164": "Lost communication with HVAC module",
    "U0184": "Lost communication with radio",
    "U3003": "Battery voltage",
    "C0035": "Left front wheel speed sensor circuit",
    "C0040": "Right front wheel speed sensor circuit",
    "C0045": "Left rear wheel speed sensor circuit",
    "C0050": "Right rear wheel speed sensor circuit",
    "B0001": "Driver frontal stage 1 deployment control",
    "B0012": "Passenger frontal stage 1 deployment control",
    "B0020": "Left side airbag deployment control",
    "B0081": "Passenger seat occupant classification",
}

SUB = {
    "P": {"0": "Fuel/air metering & auxiliary emission controls", "1": "Fuel/air metering",
          "2": "Fuel/air metering (injector circuit)", "3": "Ignition system or misfire",
          "4": "Auxiliary emission controls", "5": "Vehicle speed, idle control & aux inputs",
          "6": "Computer & output circuits", "7": "Transmission", "8": "Transmission",
          "9": "Transmission", "A": "Hybrid propulsion", "B": "Hybrid propulsion",
          "C": "Hybrid propulsion"},
}
SYSTEM = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network/communication"}

_custom: dict[str, str] = {}


def load_custom(path: str) -> int:
    """Load extra descriptions from a JSON file {"P1234": "text"}. Returns how many."""
    global _custom
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as f:
            _custom = {k.upper(): str(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        _custom = {}
    return len(_custom)


def describe(code: str) -> str:
    code = code.upper()
    if code in _custom:
        return _custom[code]
    if code in CODES:
        return CODES[code]
    sysname = SYSTEM.get(code[0], "?")
    generic = code[1] in "02" or (code[0] == "P" and code[1] == "3" and code[2] in "4567")
    kind = "generic" if generic else "manufacturer-specific"
    sub = SUB.get(code[0], {}).get(code[2], "")
    tail = f" – {sub}" if sub else ""
    return f"{sysname} {kind} code{tail} (look up for your make)"
