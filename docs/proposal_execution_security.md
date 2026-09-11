# Proposal Execution approval boundary

Proposal Execution is an internal staff workflow. Its page and APIs inherit the Hub login guard. Proposal text is read only from the proposal record already attached to the client; callers do not provide arbitrary fetch URLs.

Research, analysis, internal drafts and QA may run automatically. Publishing, scheduling client content, changing a live campaign, or beginning media spend must stop at an explicit approval/handoff state. A generated launch packet is not evidence that an external action occurred.
