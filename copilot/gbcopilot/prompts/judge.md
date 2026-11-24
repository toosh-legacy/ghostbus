---
name: judge
version: 1
---
You grade a reliability copilot's answer. Given the question, the result table it was computed from and the answer,
score:
- faithful (true/false): every number and claim in the answer appears in, or follows directly from, the table.
- answers_question (true/false): the answer addresses what was asked.
Give a one-sentence rationale.

Question: $question
Table columns: $columns
Table rows (JSON): $rows
Answer: $answer
