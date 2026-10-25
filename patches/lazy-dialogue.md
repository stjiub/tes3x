# Dialogue responses built on first use

Patch key: `lazy-dialogue`

Every response the game can give is loaded into memory when the game starts: an object per
response, its conditions, the names of the responses before and after it, and the text of any
script variable it tests. Most of them belong to ordinary topics, such as "little secret" or a
place name, which a playthrough asks only a few people about. Tamriel Rebuilt adds about 68,000
such responses, and holding them all is a large part of why its masters do not load in the
Xbox's memory.

This patch keeps responses in the plugins until they are needed. Loading records only where
each response is in its plugin and in what order it belongs. When a conversation, a script or a
save first needs a topic, greeting or journal entry, the game reads its responses from the
plugins and builds them exactly as loading would have.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

Every `DIAL` type is handled this way: topics, greetings, voice lines, persuasion and journal
entries. For each `INFO`, the loader still builds the
response, then records its plugin, its offset in that plugin and whether any plugin deleted it,
and frees it. Its place in the topic follows the engine's own rules, replayed on these records:
the first master appends in file order, and each later plugin places a new response after the one
its `PNAM` names, or moves an existing one when it changes that `PNAM`. Once the last plugin has
loaded, the names that placement needed are dropped and each topic keeps its records as an
18-byte array in order. With Tamriel Rebuilt that is 2.1 MB, down from 4.3 MB during loading.

The topic's response list stays empty until one of three readers needs it: choosing a response to
show (`getFilteredInfo`, which also picks greetings, voice lines and persuasion replies and decides
whether a topic appears in the list), restoring a heard response from a save, or a journal entry. That reader builds every response of the topic in order,
from the plugins, as `DialogueInfo::getText` already reopens them for the response text. The topic
then stays built. Code that only looks for responses the player has heard sees none in an unbuilt
topic, which is correct, because nothing in it can have been heard.

Each record also keeps the response's speaker, race, class, faction, cell and sex filters. Whenever
the dialogue window opens, the game asks every topic the player knows for a response the speaker
could give. The patch builds a topic for that question only when at least one of its responses
passes those filters for the speaker; otherwise no response could be chosen, and the topic is left
unbuilt and hidden, as the game would hide it. With Tamriel Rebuilt, about 100 of its 4,856 topics
pass for a typical speaker.

The patch requires `dialogue-merge`, so that a plugin that repeats a topic adds to it rather than
replacing it, as on PC.

With the full Tamriel Rebuilt masters (Tamriel Data 26.08, TR_Mainland 26.08.23) on a 128 MB Xbox,
at the first frame of a New Game, the heap in use falls from 47.6 to 35.7 MB and the heap's peak
during loading from 64.9 to 45.4 MB; free memory rises by 15.1 MB for topics alone. Greetings, voice,
persuasion and journal entries add 0.65 MB there and 1.8 MB once in Seyda Neen. With retail data the topic
records take 0.8 MB in place of their responses.

## Configuration

`[Xbox] LazyDialogue` is `on` by default. `off` loads every response as the unpatched game does.
`verify` loads them all as well and records the stubs beside them, for checking the placement
against the engine's.

## Compatibility and limits

Building a topic reads its responses from the plugins: roughly one read per plugin that adds to
the topic. The first conversation with each speaker can therefore read several topics.
