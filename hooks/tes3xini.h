#ifndef TES3X_INI_H
#define TES3X_INI_H

#include "tes3xlog.h"

/* [Xbox] KEY from E:\TES3X\xbox.ini, this Xbox's own settings; 0 when it is not set there. */
int tes3x_xbox_ini(const char *key, char *out, u32 size);
/* [Xbox] KEY from xbox.ini, else Morrowind.ini through the engine, else dflt. Needs D:. */
void tes3x_ini_xbox(const char *key, const char *dflt, char *out, u32 size);
/* One ini line: tracks the section in *in_xbox and copies KEY's value when in [Xbox]. */
int tes3x_ini_line(char *line, int *in_xbox, const char *key, char *out, u32 size);

#endif
