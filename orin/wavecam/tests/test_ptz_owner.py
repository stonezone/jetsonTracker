"""Offline transition tests for wavecam.ptz_owner.PtzOwner — the step-5 acceptance
gate. Pure logic, no deps. Includes Codex's required negative cases.

    cd ~/Downloads/wavecam-testbed && python -m tests.test_ptz_owner
"""
from __future__ import annotations
import sys

from wavecam.ptz_owner import PtzOwner

_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


o = PtzOwner()
check(o.owner == "idle" and not o.killed, "starts idle, not killed")

# idle -> request granted; same-owner re-request idempotent
check(o.request("vision_follow"), "idle -> request(vision_follow) granted")
check(o.request("vision_follow"), "same-owner re-request is idempotent")
check(o.owner == "vision_follow", "owner is vision_follow")

# no auto-steal: another autonomous owner cannot take it
check(not o.request("gps_tracker"), "autonomous cannot steal from another autonomous owner")
# manual cannot steal while autonomous owns, and manual nudges are gated
check(not o.request("manual"), "manual cannot steal while vision_follow owns")
check(not o.can_manual(), "manual nudges blocked while autonomous owns")
check(o.owner == "vision_follow", "owner unchanged after rejected steals")

# non-holder cannot release; holder can
check(not o.release("manual"), "non-holder cannot release")
check(o.release("vision_follow") and o.owner == "idle", "holder releases -> idle")
check(o.can_manual(), "manual allowed when idle")

# manual can own; autonomous cannot steal from manual
check(o.request("manual") and o.owner == "manual", "manual can own when idle")
check(not o.request("vision_follow"), "autonomous cannot steal from manual")
o.release("manual")

# KILL latch is sticky: blocks autonomous start + request until RESUME
o.request("vision_follow")
o.kill()
check(o.killed and o.owner == "idle", "kill -> killed (sticky) + owner idle")
check(not o.can_autonomous_start("vision_follow"), "kill blocks autonomous start")
check(not o.request("testbed"), "request rejected while killed")
o.resume()
check(not o.killed, "resume clears the latch")
check(o.can_autonomous_start("vision_follow"), "autonomous start allowed after resume")

# can_autonomous_start respects current owner + only autonomous owners
o.request("vision_follow")
check(not o.can_autonomous_start("gps_tracker"), "cannot start a 2nd autonomous owner")
check(not o.can_autonomous_start("manual"), "manual is not an autonomous owner")

print("\nALL %d CHECKS PASSED" % _n)
