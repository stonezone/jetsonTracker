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

# OWN-1: atomic transition() closes the release()->request() TOCTOU window where a
# manual claim could slip into the transient idle gap during an autonomous handoff.
o2 = PtzOwner()
check(o2.transition("idle", "vision_follow") and o2.owner == "vision_follow",
      "transition idle -> vision_follow (atomic claim)")
check(o2.transition("vision_follow", "gps_tracker") and o2.owner == "gps_tracker",
      "transition vision_follow -> gps_tracker (atomic autonomous handoff)")

# TOCTOU: if the owner moved underneath us (manual grabbed it), the atomic
# transition must REFUSE and leave the operator's manual ownership intact.
o2.release("gps_tracker")
check(o2.request("manual") and o2.owner == "manual", "manual owns after operator claim")
check(not o2.transition("gps_tracker", "vision_follow"),
      "transition refused when current != expected_from (no clobber of manual)")
check(o2.owner == "manual", "manual ownership preserved through refused transition")
o2.release("manual")

# transition to idle / invalid is refused; idle is reached via release(), not transition
check(not o2.transition("idle", "idle"), "transition to idle refused")
check(not o2.transition("idle", "bogus"), "transition to unknown owner refused")

# KILL latch blocks transition
o2.request("vision_follow")
o2.kill()
check(not o2.transition("idle", "gps_tracker"), "transition blocked while killed")
check(o2.owner == "idle", "owner stays idle while killed")
o2.resume()

print("\nALL %d CHECKS PASSED" % _n)
