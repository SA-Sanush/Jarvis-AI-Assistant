"""
JARVIS — Entry Point (Multilingual Edition)
Usage:
  python main.py                    # Text mode (auto language detect)
  python main.py --voice            # Voice mode (wake word, multilingual)
  python main.py --ptt              # Push-to-talk voice mode
  python main.py --lang ml          # Start in Malayalam
  python main.py --lang hi          # Start in Hindi
  python main.py --lang ta          # Start in Tamil
  python main.py --voice --lang ml  # Malayalam voice mode
"""

import asyncio
import sys
import os
import argparse
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def parse_args():
    p = argparse.ArgumentParser(description="JARVIS AI Assistant — Multilingual")
    p.add_argument("--voice", action="store_true", help="Voice mode (wake word)")
    p.add_argument("--ptt", action="store_true", help="Push-to-talk voice mode")
    p.add_argument("--continuous", action="store_true", help="Continuous voice mode")
    p.add_argument("--lang", default=None,
                   choices=["en", "ml", "hi", "ta"],
                   help="Language: en=English, ml=Malayalam, hi=Hindi, ta=Tamil")
    p.add_argument("--debug", action="store_true", help="Debug logging")
    p.add_argument("--config", default="config/settings.yaml")
    return p.parse_args()


async def run_voice_mode(args, mode):
    from core.jarvis import JARVIS
    from core.language import LanguageManager, SUPPORTED_LANGUAGES
    from voice.multilingual_pipeline import MultilingualPipeline

    jarvis = JARVIS(config_path=args.config)
    if args.lang:
        jarvis.lang.set_language(args.lang)

    lang_name = SUPPORTED_LANGUAGES.get(jarvis.lang.current_lang, {}).get("name", "English")
    print(f"\n🤖 JARVIS Voice Mode — {lang_name} ({mode}) — Ctrl+C to exit\n")

    pipeline = MultilingualPipeline(
        jarvis_instance=jarvis,
        language_manager=jarvis.lang,
        config=jarvis.cfg,
        on_listening=lambda: print("\r🎙 Listening...     ", end="", flush=True),
        on_processing=lambda: print("\r🧠 Processing...    ", end="", flush=True),
        on_response=lambda t, l: print(f"\rJARVIS [{l}]: {t}\n"),
        on_language_change=lambda l: print(f"\n🌐 Language detected: {SUPPORTED_LANGUAGES.get(l,{}).get('name',l)}\n"),
    )
    try:
        await pipeline.start()
    except KeyboardInterrupt:
        pipeline.stop()
        print("\nJARVIS: Goodbye!")


async def run_text_mode(args):
    from core.jarvis import interactive_cli
    await interactive_cli()


async def main():
    args = parse_args()
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s | %(name)s | %(message)s")

    if args.voice:
        from voice.pipeline import PipelineMode
        await run_voice_mode(args, PipelineMode.WAKE_WORD)
    elif args.ptt:
        from voice.pipeline import PipelineMode
        await run_voice_mode(args, PipelineMode.PUSH_TO_TALK)
    elif args.continuous:
        from voice.pipeline import PipelineMode
        await run_voice_mode(args, PipelineMode.CONTINUOUS)
    else:
        await run_text_mode(args)


if __name__ == "__main__":
    asyncio.run(main())
