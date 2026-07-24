# Server-side multiple-choice answer placement

## Goal

The generation agent determines a multiple-choice question's meaning: its question,
one correct answer, its distractors, and its explanation. The MCP server alone
determines where those choices appear. Once a result is saved, its choice order and
correct-answer index are immutable for that saved result.

## Input and compatibility

The summarizer prompt will request this agent-owned structure for each multiple-choice
question:

```json
{
  "id": "mc_1",
  "question": "...",
  "correct_answer": "...",
  "incorrect_answers": ["...", "..."],
  "explanation": "..."
}
```

`save_chapter_result` will also continue accepting the existing `options` plus
`answer_index` form. This keeps already-produced agent results and existing MCP
clients valid while clients move to the new prompt schema.

## Save-time transformation

Before a chapter result is written, the server validates every new-format multiple
choice item: required nonempty strings, at least one distractor, and no duplicate
choice text. It then combines the correct answer and distractors, uses server-side
randomness to shuffle that single question's choices, and derives `answer_index`
from the shuffled correct answer.

The server writes only the established persisted/rendered shape:

```json
{
  "id": "mc_1",
  "question": "...",
  "options": ["..."],
  "answer_index": 0,
  "explanation": "..."
}
```

`correct_answer` and `incorrect_answers` are not persisted. The existing HTML and
Markdown/TUI renderers therefore need no behavioral changes.

## Immutability and retries

Random placement occurs only during a successful `save_chapter_result` call. The
materialized `options` and `answer_index` are stored in the chapter quiz JSON;
rendering, study resumption, and repeated finalization only read those values and do
not shuffle again. A failed validation or failed write leaves no saved result, so a
later corrected submission may receive a new placement. A saved result is never
rewritten by rendering.

## Error handling

Malformed new-format questions fail through the existing `data.missing` mechanism
and do not mark the chapter complete or leave result files behind. Legacy payload
validation remains unchanged.

## Tests

Tests will prove that the new agent format is accepted; that server output contains
the canonical `options` and matching `answer_index`; that it no longer retains
agent-owned placement fields; and that rendering/reloading a saved result preserves
the exact stored order. Tests will inject a deterministic shuffle function where
needed, avoiding probabilistic assertions while production uses randomness.
