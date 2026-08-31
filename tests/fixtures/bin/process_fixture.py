import os
import sys
import time

mode = sys.argv[1]
if mode == "sleep":
    time.sleep(float(sys.argv[2]))
elif mode == "env":
    print(os.environ.get("QUALOCK_TEST", ""))
elif mode == "exit":
    print("hello")
    print("oops", file=sys.stderr)
    raise SystemExit(int(sys.argv[2]))
