#!/usr/bin/env python3
"""Stub for the `grok` binary. Used only by test_grok_driver.py -- never invoke the real
`grok` CLI from a test.

Parses the argv it receives (mirroring `grok --prompt-file ... --output-format streaming-json
...`, see references/grok-cli.md) and writes it, plus a couple of environment variables
swarm.py is supposed to set on this Popen, to the file named by $FAKE_GROK_CAPTURE (JSON) so
tests can assert on the exact command line. Then prints a few `--output-format streaming-json`
lines and exits.

Behavior switches on $FAKE_GROK_MODE:
  normal              -- thought + text chunks + a clean `end` event with usage/cost (default)
  missing_cost         -- like normal but the `end` event omits total_cost_usd entirely
  max_turn_requests    -- `end` event reports stopReason "max_turn_requests"
  sleep_forever        -- prints one line then sleeps, for the watchdog stall/timeout test

$FAKE_GROK_WRITE (JSON object, path -> text, or null to delete) makes the stub edit files under its
working directory first, the way a worker edits its worktree.
"""
import json
import os
import sys
import time


def emit(obj):
    print(json.dumps(obj), flush=True)


def main():
    argv = sys.argv[1:]

    cap = os.environ.get("FAKE_GROK_CAPTURE")
    if cap:
        record = {
            "argv": argv,
            "env": {
                "GROK_MEMORY": os.environ.get("GROK_MEMORY"),
                "GROK_DISABLE_AUTOUPDATER": os.environ.get("GROK_DISABLE_AUTOUPDATER"),
                "GROK_WEB_FETCH": os.environ.get("GROK_WEB_FETCH"),
            },
        }
        # if a --prompt-file was passed, read it *now* (before it can be cleaned up by the
        # caller) so a test can assert on its content and location even after the run finishes
        if "--prompt-file" in argv:
            pf = argv[argv.index("--prompt-file") + 1]
            record["prompt_file_path"] = pf
            try:
                with open(pf) as f:
                    record["prompt_file_content"] = f.read()
            except OSError as e:
                record["prompt_file_content"] = f"<error reading {pf}: {e}>"
        with open(cap, "w") as f:
            json.dump(record, f)

    for name, text in json.loads(os.environ.get("FAKE_GROK_WRITE", "{}")).items():
        if text is None:
            os.remove(name)
        else:
            os.makedirs(os.path.dirname(name) or ".", exist_ok=True)
            with open(name, "w") as f:
                f.write(text)

    mode = os.environ.get("FAKE_GROK_MODE", "normal")

    # a fix round passes --resume <session>; echo it back as this run's sessionId, the way a
    # real resumed session would report the same id it was given
    session_id = "ses_fake0000"
    if "--resume" in argv:
        session_id = argv[argv.index("--resume") + 1]

    if mode == "sleep_forever":
        emit({"type": "thought", "data": "thinking..."})
        time.sleep(3600)
        return 0

    emit({"type": "available_commands", "tools": ["read_file", "grep", "list_dir"], "commands": []})
    emit({"type": "thought", "data": "Let"})
    emit({"type": "thought", "data": " me"})
    emit({"type": "thought", "data": " look at this."})
    emit({"type": "text", "data": "Here"})
    emit({"type": "text", "data": " is"})
    emit({"type": "text", "data": " the answer."})

    usage = {"input_tokens": 100, "output_tokens": 40, "reasoning_tokens": 10,
              "cache_read_input_tokens": 5, "cache_creation_input_tokens": 0, "total_tokens": 155}

    end = {"type": "end", "stopReason": "end_turn", "sessionId": session_id,
           "requestId": "req_fake", "usage": usage, "num_turns": 3, "modelUsage": {}}

    if mode == "max_turn_requests":
        end["stopReason"] = "max_turn_requests"
    if mode != "missing_cost":
        end["total_cost_usd"] = 0.0123
        end["total_cost_usd_ticks"] = 123000000

    emit(end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
