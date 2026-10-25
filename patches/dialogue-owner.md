# Direct response topic lookup

Patch key: `dialogue-owner`

Several times during a conversation the engine needs to know which topic a response belongs to: when
a response is heard for the first time, whenever a response runs its result script, and whenever a
`Choice` condition is checked. Responses do not record their topic, so the engine searches for it.
It walks every topic and every response under it until it meets the one it was given. That is
about 32,000 responses with the retail game and about 110,000 with Tamriel Rebuilt, so the search
grows with every dialogue mod in the load order.

This patch records each response's topic once, after loading, and answers from that. The answer is
the same; the search goes away.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

`DialogueInfo::findDialogue` (`0x000FE390`) walks `NonDynamicData`'s topic list and each topic's
response chain. A response's field at `+0x10` holds its temporary load names while plugins load;
the engine frees them and clears the field when loading finishes, and nothing reads it afterwards.
On the first lookup after that point, the patch walks every topic once and stores the topic in each
response's `+0x10`, then answers every lookup from it. A response in no topic's chain, or a lookup
before loading has finished, still takes the original search.

The response destructor frees whatever `+0x10` points at. The patch guards that read: a pointer
whose first word is the topic class's vtable is a stored topic, and the destructor clears it
instead of freeing it.

## Compatibility and limits

It works with [`info-name-arena`](info-name-arena.md) and [`dialogue-merge`](dialogue-merge.md),
which change how the load names are allocated and which responses stay on their topics. Responses
are never created after loading, so a stored topic stays correct for the session.
