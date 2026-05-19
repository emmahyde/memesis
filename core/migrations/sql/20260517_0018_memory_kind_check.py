"""Migration 0018: reject invalid memory_kind values via CHECK triggers.

SQLite has no ``ALTER TABLE ADD CONSTRAINT``. Rather than rebuild the whole
``memories`` table just to attach a CHECK, two BEFORE-write triggers
``RAISE(ABORT)`` whenever ``memory_kind`` is set to a value outside the
curated taxonomy.

NULL stays allowed — it is the correct value for ephemeral and open_question
rows (see ``core/validators.py``). This migration rejects *garbage*, it does
not require *presence*; presence is enforced at promotion time by
``LifecycleManager.can_promote``.

Keep ``_KINDS`` in sync with ``core.validators.MEMORY_KIND_VALUES``.
"""

# Mirror of core.validators.MEMORY_KIND_VALUES. Inlined so the migration does
# not import the core package while the schema is mid-upgrade.
_KINDS = (
    "decision", "lesson", "gotcha", "goal", "invariant",
    "opinion", "bias", "todo", "debt", "fact",
)


def up(conn) -> None:
    kind_list = ", ".join(f"'{k}'" for k in _KINDS)
    for event in ("INSERT", "UPDATE"):
        conn.execute_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS memory_kind_check_{event.lower()}
            BEFORE {event} ON memories
            WHEN NEW.memory_kind IS NOT NULL
             AND NEW.memory_kind NOT IN ({kind_list})
            BEGIN
                SELECT RAISE(ABORT, 'invalid memory_kind');
            END;
            """
        )
