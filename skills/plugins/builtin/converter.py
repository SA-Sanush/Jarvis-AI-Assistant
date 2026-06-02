"""JARVIS built-in: Unit converter"""
import re
from typing import Optional

MANIFEST = {
    "name": "Unit Converter",
    "description": "Convert between units: km/miles, C/F, kg/lbs, etc.",
    "triggers": ["convert", "in miles", "in km", "in celsius", "in fahrenheit", "in kg", "in lbs"],
    "author": "JARVIS",
    "version": "1.0.0"
}

CONVERSIONS = {
    ("km", "miles"):     lambda x: x * 0.621371,
    ("miles", "km"):     lambda x: x * 1.60934,
    ("c", "f"):          lambda x: x * 9/5 + 32,
    ("celsius", "fahrenheit"): lambda x: x * 9/5 + 32,
    ("f", "c"):          lambda x: (x - 32) * 5/9,
    ("fahrenheit", "celsius"): lambda x: (x - 32) * 5/9,
    ("kg", "lbs"):       lambda x: x * 2.20462,
    ("lbs", "kg"):       lambda x: x * 0.453592,
    ("m", "ft"):         lambda x: x * 3.28084,
    ("ft", "m"):         lambda x: x * 0.3048,
    ("l", "gallons"):    lambda x: x * 0.264172,
    ("gallons", "l"):    lambda x: x * 3.78541,
}

async def handle(command: str, jarvis) -> Optional[str]:
    cmd = command.lower()
    if not any(t in cmd for t in MANIFEST["triggers"]):
        return None
    m = re.search(r"([\d.]+)\s*(\w+)\s+(?:to|in)\s+(\w+)", cmd)
    if not m:
        return None
    val, from_unit, to_unit = float(m.group(1)), m.group(2), m.group(3)
    key = (from_unit, to_unit)
    if key in CONVERSIONS:
        result = CONVERSIONS[key](val)
        return f"{val} {from_unit} = {result:.4g} {to_unit}"
    return f"I don\'t know how to convert {from_unit} to {to_unit}."
