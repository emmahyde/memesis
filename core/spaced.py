"""
SM-2 spaced injection — schedule memory re-injection based on usage feedback.

When a memory is used (was_used=1), its interval grows:
  new_interval = old_interval * ease_factor
  ease_factor adjusted by: EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
  where q=5 for "used" and q=1 for "not used after injection"

When a memory is injected but NOT used, its interval shrinks:
  new_interval = max(1.0, old_interval * 0.5)
  ease_factor decremented

Memories with next_injection_due in the future are excluded from injection.
Memories with no next_injection_due are always eligible.
"""

from datetime import datetime, timedelta

from .models import Memory


def update_sm2_schedule(memory: Memory, was_used: bool) -> None:
    """Update SM-2 schedule fields after injection feedback.

    Args:
        memory: The Memory instance to update.
        was_used: Whether the memory was used in the session.
    """
    from .flags import get_flag

    if not get_flag("sm2_spaced_injection"):
        return

    ef = memory.injection_ease_factor or 2.5
    interval = memory.injection_interval_days or 1.0

    if was_used:
        # SM-2: q=5 (perfect recall)
        # EF' = EF + (0.1 - (5-5)*(0.08 + (5-5)*0.02)) = EF + 0.1
        ef = max(1.3, ef + 0.1)
        interval = interval * ef
    else:
        # SM-2: q=1 (complete failure — injected but ignored)
        # EF' = EF + (0.1 - 4*(0.08 + 4*0.02)) = EF - 0.54
        ef = max(1.3, ef - 0.54)
        interval = max(1.0, interval * 0.5)

    now = datetime.now()
    next_due = (now + timedelta(days=interval)).isoformat()

    memory.injection_ease_factor = ef
    memory.injection_interval_days = interval
    memory.next_injection_due = next_due
    memory.save()


def is_injection_eligible(memory: Memory) -> bool:
    """Check if a memory is eligible for injection (not suppressed by SM-2).

    Returns True if:
    - SM-2 flag is disabled, OR
    - next_injection_due is None (never scheduled), OR
    - next_injection_due is in the past
    """
    from .flags import get_flag

    if not get_flag("sm2_spaced_injection"):
        return True

    if not memory.next_injection_due:
        return True

    try:
        due = datetime.fromisoformat(memory.next_injection_due)
        return datetime.now() >= due
    except (ValueError, TypeError):
        return True
