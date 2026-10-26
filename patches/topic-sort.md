# Single-pass topic sort at load

Patch key: `topic-sort`

After the data files load, the engine sorts every dialogue topic by name length, longest first,
keeping topics of equal length in load order. It does this with a bubble sort that makes one full
pass over the topic list per topic, measuring both names of every pair it compares. Retail
Morrowind's topics take a fraction of a second; with the Tamriel Rebuilt masters, about 7,000
topics, the sort took about 30 s of the console's load.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch replaces the sort (`hooks/tes3xtopicsort.c`). It measures each topic name once, sorts
the topics with a stable merge sort, and writes them back into the same list in the new order,
which is the order the engine's sort produces. The loading bar advances by the same amount as
before, in one step.

The log records the number of topics sorted as `topicsort.topics`. If the list holds an empty
entry, or the patch cannot get the few hundred kilobytes of scratch memory it needs, it logs
`topicsort.fallback` and runs the engine's original passes instead.
