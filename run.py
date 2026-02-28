"""
Quick way to run a single task manually.
Usage: python run.py "your task description here"
"""
import asyncio
import sys
import os
from dotenv import load_dotenv

load_dotenv()

from llm import run_task
from storage.conversations import create_conversation


async def main():
    if len(sys.argv) < 2:
        print("usage: python run.py \"task description\"")
        sys.exit(1)

    description = " ".join(sys.argv[1:])
    conversation_id = create_conversation(title=description, source="cli")
    print(f"[zipper] conversation: {conversation_id}")
    result = await run_task(description, conversation_id)
    print(f"\n[zipper] result:\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
