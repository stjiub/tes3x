# Read topic colors

Patch key: `topic-colors`

A dialogue topic whose current response you have already read is drawn dimmer than topics you have
not heard yet, so a list of topics shows at a glance which ones still have something new to say.

A topic counts as read when the response the topic would give now has been shown to your character.
A topic whose answer changes, for example after a quest advances, returns to the normal color until
that new answer is read. The dimmed color follows the pointer or pad highlight like any other topic.

## How it works

The patch records each response you read in `T:\tes3x-topics.dat`, keyed by your character's name
and the response's position in its plugin, so the marks survive a load or restart. After the engine
rebuilds the topic list, the patch looks up each topic's current response and dims the ones
recorded.

## Compatibility and limits

- Characters with the same name share their read marks.
- A plugin that is edited or reordered so a response moves loses that response's mark.
- The record holds about 2,000 responses; after that new reads are not remembered.

See the [patch table](../docs/patches.md) for availability and selection.
