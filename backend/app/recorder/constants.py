"""Step-type constants shared by the recorder session and the LLM authoring
module (app.llm.authoring). Kept in their own module — with no other project
imports — so app.llm.authoring can depend on them without a circular import
through app.recorder.session."""

RECORDED_EVENT_TYPES = {"click", "fill", "press", "select_option"}
VALUE_STEP_TYPES = {"fill", "select_option"}
