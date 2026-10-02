# Exec scripts

With the [console](../patches/console.md) patch, a build runs console commands from a script file
once per launch, with no controller. Game tests, smoke tests and memory tours are all exec scripts.

## Where the file goes

The game reads `tes3xexec.txt` from `E:\`, then from beside `default.xbe`. `E:\` is checked first,
so a script can be changed without touching the game folder. A file in `E:\` runs on every launch
until it is deleted. The xemu runner's `--exec` puts the file on the emulated disk for you.

The file is read into memory sized to it and released when its last line has run (`exec.done`),
so even a long script costs only its own size while it runs.

## Example

```
# start a New Game straight from boot, skipping the main menu
@start new
wait 30
player->getpos x
coc "Balmora"
wait 60
assert player->getpos x == -12288.00
exit
```

## Lines

| Line | Meaning |
|---|---|
| `@start new` | start a New Game at boot, without the main menu |
| `@start load U:\DIR\NAME.ess` | load that save at boot, without the main menu |
| `@menu ...` | run only while the main menu is up, e.g. `@menu click MenuOptions MenuOptions_New_container` |
| `wait N` | pause N frames |
| `click MENU WIDGET` | press a menu widget by name |
| `pad MENU A\|B\|X\|Y\|N` | send a pad button to a menu; `B` closes most menus. `N` is a raw event, counted from A's |
| `nav up\|down\|left\|right` | move pad focus as the D-pad does, through the engine's navigation |
| `menu inventory`, `menu journal` | open the in-game menu or the journal, as the pad's buttons do; `menu inventory` again closes it |
| `activate ID` | the player activates the nearest reference of `ID`, as with the A button (a script's `Activate` only reaches scripted objects) |
| `visible MENU` | log whether a menu is on screen, as `menu.MENU 0` or `1` |
| `assert COMMAND == VALUE` | run a console command and check the value it prints |
| `mark LABEL` | log free memory now, as `mem.LABEL <KB>` |
| `exit` | turn the Xbox off |
| `reboot` | restart the Xbox into the dashboard |
| `# ...` | comment |
| anything else | a console command |

Lines run one per frame; ordinary lines start once the game has run 150 frames without the main
menu. `@start` applies only when the game was started directly, not from another program that
passed its own launch data. In xemu that means booting with `--direct-engine`; see
[xemu](xemu.md).

## Loading a save

`U:` is `E:\UDATA\42530005`, or the save pool's folder for a build that sets `save_pool`. A save
loads by path from any folder there; it does not need to appear in the game's save list. **A path
that does not load is not reported: the game starts a New Game instead.** A test that loads a save
should check something only that save has.

## Output

Each command is logged as `exec> ...` and the first 8 lines it prints as `console< ...`, such as
`console< GetPos >> -12288.00`. A command that loads a cell runs that cell's scripts, which print
too; the rest are counted as `console.more N`. Commands typed on the pad are logged the same way.

## Asserts

`assert player->getpos x == 61.00` compares the text after the last `>> ` of the command's first
printed line with `61.00`, exactly. It logs `assert> ...`, then `assert.pass N` or `assert.fail N`
followed by `assert.got <value>`, N counting the asserts. Before `exit` or `reboot`, or when the
script ends, `assert.total` and `assert.failed` summarise them. The test runners fail a run with a
failed assert, or with fewer asserts run than the script has.

## Commands from a debugger

In an emulator, a debugger can also hand the running game one command at a time: write the text
into the payload's `tes3x_mailbox`, then change its sequence number. It runs on the next frame and
is logged as `live> ...`.
