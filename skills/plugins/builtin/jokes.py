"""JARVIS built-in: Jokes"""
import random
from typing import Optional

MANIFEST = {
    "name": "Jokes",
    "description": "Tell jokes on request",
    "triggers": ["joke", "funny", "make me laugh", "tell me something funny"],
    "author": "JARVIS",
    "version": "1.0.0"
}

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break. Now it won't stop sending me Kit-Kat ads.",
    "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
    "A SQL query walks into a bar, walks up to two tables and asks... can I join you?",
    "Why do Python programmers wear glasses? Because they can't C#.",
    "I would tell you a joke about UDP, but you might not get it.",
]

async def handle(command: str, jarvis) -> Optional[str]:
    cmd = command.lower()
    if any(t in cmd for t in MANIFEST["triggers"]):
        return random.choice(JOKES)
    return None
