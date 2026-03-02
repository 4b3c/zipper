"""
Dev tool — send a prompt to the running zipper server.
Usage: python run.py "prompt" [conversation_id]
"""
import sys
import json
import urllib.request
import urllib.error

ZIPPER_URL = "http://localhost:4199"


def main():
    if len(sys.argv) < 2:
        print('usage: python run.py "prompt" [conversation_id]')
        sys.exit(1)

    prompt = sys.argv[1]
    conversation_id = sys.argv[2] if len(sys.argv) > 2 else None

    payload = {"prompt": prompt, "source": "cli"}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{ZIPPER_URL}/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
            print(f"[conversation: {body['conversation_id']}]")
            print(body["result"])
    except urllib.error.URLError as e:
        print(f"error: could not reach zipper server at {ZIPPER_URL}")
        print(f"  is it running? try: python main.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
