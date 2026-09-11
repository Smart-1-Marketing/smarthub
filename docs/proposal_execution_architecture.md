# Proposal Execution architecture seam

The orchestration layer should remain thin: proposal parsing produces normalized channel facts; task recipes produce a dependency graph; adapters are the only code that knows how a SmartHub tool is executed.

Current adapters are `brief`, `radio_scripts`, and `launch_packet`. The next implementation wave should add tool-native adapters without changing run/task state semantics.

An adapter may generate internal work automatically. Any adapter capable of publishing, scheduling, changing live media or spending money must declare an approval/handoff mode and must not report `live` or `completed` until the external action is verified.

The queue is persisted in the shared Hub database. The MVP advances from the existing scheduler leader through a bridge installed when the feature registers. That bridge is intentionally replaceable by a dedicated scheduler entry later; task storage and APIs do not depend on how the tick is delivered.
