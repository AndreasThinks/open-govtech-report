# Worker disk admission

Classification now checks free space under the worker lock, before opening mutable state, again after merge/planning and before new publication attempts. Required free space is four times the measured master.db plus master.db-wal byte footprint, plus 1 GiB. The multiplier conservatively budgets merge, SQLite snapshot, publication export, and private-checkpoint staging; it is not a measured peak guarantee or a filesystem reservation. File growth and competing writers can still exhaust storage, so retain checkpoint and publication recovery semantics. Production inventory should validate and tune this policy.

Low space raises `Insufficient free disk` before paid work. It does not delete data, alter recovery markers, or silently buy additional capacity. Standalone retention/inventory remains separate from this admission gate; pending-publication recovery does not allocate another bundle. A normal classification invocation is still blocked at admission if it includes recovery.

Bounded publication retention is documented in railway-worker.md. It requires exact remote verification and a clean checkpoint. A dirty volume needs explicit recovery before applying retention. Never clear a dirty marker just to unlock deletion, and never invoke the normal paid wrapper to obtain a filesystem inventory.

Verification: disk gate tests cover low-space rejection before mutable open, master plus WAL sizing, boundary acceptance, and missing-master fail-closed behavior. Retention tests cover interrupted deletion, short writes, and zero-progress receipt writes. The latter stop further deletion with a durable state receipt.
