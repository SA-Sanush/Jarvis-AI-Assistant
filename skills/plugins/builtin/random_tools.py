"""JARVIS built-in: Random tools"""
import random, re
from typing import Optional

MANIFEST = {
    "name": "Random Tools",
    "description": "Flip coins, roll dice, pick random numbers",
    "triggers": ["flip", "coin", "dice", "roll", "random number", "pick a number"],
    "author": "JARVIS",
    "version": "1.0.0"
}

async def handle(command: str, jarvis) -> Optional[str]:
    cmd = command.lower()
    if "flip" in cmd or "coin" in cmd:
        return f"🪙 {random.choice(['Heads', 'Tails'])}!"
    if "dice" in cmd or "roll" in cmd:
        if m := re.search(r"(\d+)d(\d+)", cmd):
            n, sides = int(m.group(1)), int(m.group(2))
            rolls = [random.randint(1, sides) for _ in range(min(n, 20))]
            return f"🎲 Rolled {n}d{sides}: {rolls} (total: {sum(rolls)})"
        return f"🎲 Rolled: {random.randint(1, 6)}"
    if "random number" in cmd or "pick a number" in cmd:
        if m := re.search(r"between (\d+) and (\d+)", cmd):
            lo, hi = int(m.group(1)), int(m.group(2))
        else:
            lo, hi = 1, 100
        return f"🔢 Random number: {random.randint(lo, hi)}"
    return None
