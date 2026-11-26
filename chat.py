#!/usr/bin/env python3
"""Interactive command-line chat with DeepSeek."""

import sys
import llm_bridge


def main():
    # If a prompt was provided as CLI argument, run single completion
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        try:
            print(llm_bridge.complete(prompt))
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Interactive chat loop
    print("=" * 60)
    print("  DeepSeek Terminal Chat")
    print("  Type your prompt and press Enter.")
    print("  Type 'exit', 'quit', or Ctrl+C to leave.")
    print("=" * 60)

    history = []
    while True:
        try:
            prompt = input("\nYou: ").strip()
            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit", "q"):
                print("\nGoodbye!")
                break

            history.append({"role": "user", "content": prompt})
            print("\nDeepSeek thinking...", end="\r", flush=True)

            response = llm_bridge.complete_messages(history)
            print(" " * 30, end="\r")  # clear thinking indicator
            print(f"DeepSeek:\n{response}")
            history.append({"role": "assistant", "content": response})

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"\nError: {exc}")


if __name__ == "__main__":
    main()
