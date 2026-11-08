"""Command line entry point.

  python -m lungdaily.run all            collect -> process -> amplify (then build separately or with --build)
  python -m lungdaily.run collect
  python -m lungdaily.run process
  python -m lungdaily.run build
  python -m lungdaily.run amplify
  python -m lungdaily.run check-handles a,b,c  look up X handles (name, bio, followers) before adding voices
  python -m lungdaily.run linkedin-test    send the newest article to the Make webhook once (setup)
  python -m lungdaily.run linkedin         share new articles on the LinkedIn page (after deploy)
  python -m lungdaily.run follow           follow relevant respiratory accounts on X
  python -m lungdaily.run release <slug> publish a held article
  python -m lungdaily.run discard <slug> drop a held article
"""
from __future__ import annotations

import sys

from . import amplify, build, collect, follow, process
from .util import log


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "collect":
        collect.run()
    elif cmd == "process":
        process.run()
    elif cmd == "build":
        build.build()
    elif cmd == "amplify":
        amplify.run()
    elif cmd == "check-handles" and len(argv) > 1:
        from .sources.x import lookup_handles
        for u in lookup_handles([h.strip().lstrip("@") for h in argv[1].replace(" ", ",").split(",") if h.strip()]):
            print("HANDLE", u)
    elif cmd == "linkedin-test":
        from . import linkedin_share
        return 0 if linkedin_share.send_test() else 3
    elif cmd == "linkedin":
        from . import linkedin_share
        linkedin_share.run_pending()
    elif cmd == "follow":
        follow.run()
    elif cmd == "all":
        collect.run()
        stats = process.run()
        build.build()
        amplify.run(stats)
    elif cmd in ("release", "discard") and len(argv) > 1:
        ok = (process.release if cmd == "release" else process.discard)(argv[1])
        log.info("%s %s: %s", cmd, argv[1], "done" if ok else "not found in held queue")
        if ok and cmd == "release":
            build.build()
            amplify.run()
        return 0 if ok else 2
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
