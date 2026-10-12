---
type: llm
---

PASS if the reply makes clear that nothing was synced yet (for example because there is no ANPOS plan in this folder, the plan is not approved, or no Linear connector is available) and suggests a concrete next step, without claiming any tasks were created.
FAIL if the reply claims tasks were created in Linear, gives a task count as if synced, or invents Linear project details.
