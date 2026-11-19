"""Hosting entrypoint; original bot.py remains supported."""
import runpy
if __name__ == "__main__":
    runpy.run_module("bot", run_name="__main__")
