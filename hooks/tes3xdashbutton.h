#ifndef TES3X_DASHBUTTON_H
#define TES3X_DASHBUTTON_H

/* Run before the Dash is left for; the multiplayer layer ends its session. */
extern void (*tes3x_dash_closing)(void);
/* The menus given a hint line so far: text set on one is set again on the next. */
extern u32 tes3x_hint_serial;
/* Whether a Dash is installed; leave for it. */
u32 tes3x_dashbutton_ready(void);
void tes3x_dashbutton_launch(void);
/* Show the menu's hint line: Black's text and White's, either 0 to hide that button. */
void tes3x_hint_show(u8 *menu, const char *black, const char *white);
/* The pad's buttons held (bit 1 Black, bit 2 White), less any the caller used. */
void tes3x_dashbutton_pad(u32 level);

#endif
